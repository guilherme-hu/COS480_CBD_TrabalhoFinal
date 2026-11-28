"""Sort-merge PK→FK. Camada 2, dono P5.

Usa os cursores de access e external_sort e devolve pares em streaming.
Decisões D-P5-11/12: ordem B+ quando disponível e orçamento WORK dividido.
"""

from contextlib import ExitStack, closing
from dataclasses import dataclass
from typing import Iterator

from cbd.contracts import Cost, JoinSide, NotApplicable, Path, Record
from cbd.exec.access import can_order, fetch_cost, read_all, read_ordered
from cbd.exec.external_sort import external_sort, external_sort_cost


@dataclass(frozen=True)
class _Input:
    side: JoinSide
    column: int
    ordered: bool
    blocks: int
    records: int


def _input(side):
    stats = side.table.stats()
    return _Input(side, side.table.schema.idx(side.column), can_order(side),
                  stats.n_blocks, stats.n_records)


def _plan(eng, r, s):
    rf = r.table.schema.get(r.column)
    sf = s.table.schema.get(s.column)
    if not rf.unique and not sf.unique:
        raise NotApplicable("sort-merge requer ao menos um lado UNIQUE na chave")
    if rf.ftype != sf.ftype:
        raise NotApplicable("sort-merge requer colunas do mesmo tipo")
    left, right = _input(r), _input(s)
    sorts = int(not left.ordered) + int(not right.ordered)
    pages = eng.M // sorts if sorts else 0
    # D-P5-11: os dois cursores de ordenação podem ficar ativos ao mesmo tempo.
    if sorts and pages < 3:
        raise NotApplicable(f"sort-merge requer M >= {3 * sorts} para {sorts} ordenações")
    return left, right, rf.unique, pages


def _stream(eng, entry, pages, info):
    if entry.ordered:
        cursor = read_ordered(entry.side)
    else:
        cursor = external_sort(eng, read_all(entry.side), entry.side.table.schema,
                               entry.side.column, n_blocks_hint=entry.blocks,
                               mem=pages, info=info)
    with closing(cursor):
        for record in cursor:
            if record[entry.column] is not None:
                yield record


def sort_merge_join(eng, r: JoinSide, s: JoinSide, info=None) -> Iterator[Record]:
    """Emite r_rec+s_rec para cada chave igual, ignorando None.

    O orçamento, em páginas WORK, é dividido entre as entradas que ordenamos.
    Levanta NotApplicable para tipos distintos, nenhum lado UNIQUE, caminho
    inválido ou memória insuficiente para os sorts. Um percurso B+ primário
    custa a descida mais as folhas visitadas. Sort externo também usa temporários.
    """
    left, right, master_is_r, pages = _plan(eng, r, s)
    sorts = ({}, {})
    if info is not None:
        variant = ("ordered" if left.ordered and right.ordered else
                   "sort-one" if left.ordered or right.ordered else "sort-both")
        info.update(variant=variant, sorted_r=not left.ordered, sorted_s=not right.ordered,
                    index_used=left.ordered or right.ordered, mem_each=pages)
    try:
        with ExitStack() as cursors:
            a = cursors.enter_context(closing(_stream(eng, left, pages, sorts[0])))
            b = cursors.enter_context(closing(_stream(eng, right, pages, sorts[1])))
            ra, sb = next(a, None), next(b, None)
            while ra is not None and sb is not None:
                rk, sk = ra[left.column], sb[right.column]
                if rk < sk:
                    ra = next(a, None)
                elif sk < rk:
                    sb = next(b, None)
                elif rk == sk:
                    yield ra + sb
                    # D-P5-12: o cursor UNIQUE fica parado para o próximo detalhe.
                    if master_is_r:
                        sb = next(b, None)
                    else:
                        ra = next(a, None)
                else:
                    # FLOAT não ordenável, e.g. NaN, não é chave PK/FK do escopo.
                    raise NotApplicable("chaves do sort-merge precisam de ordem total")
    finally:
        if info is not None:
            info.update(passes=sum(s.get("passes", 0) for s in sorts),
                        runs=sum(s.get("runs", 0) for s in sorts))


def _input_cost(eng, entry, pages):
    side = entry.side
    if not entry.ordered:
        return external_sort_cost(eng, entry.blocks, mem=pages)
    if side.path is Path.PRIMARY:
        height = side.table.data.height
        reads = max(0, height - 1) + entry.blocks
        return Cost(reads, formula=f"primário B+: {height}-1+{entry.blocks}")
    stats = side.table.indexes[side.column].stats()
    height, leaves = stats["levels"], stats["leaf_blocks"]
    postings = stats["overflow_blocks"]
    fetch = fetch_cost(side.table)
    reads = max(0, height - 1) + leaves + postings + entry.records * fetch
    return Cost(reads, formula=f"secundário B+: {height}-1+{leaves}+{postings}+{entry.records}*{fetch}")


def sort_merge_join_cost(eng, r, s) -> Cost:
    """Estima blocos pela leitura dos caminhos e sorts, sem materializar saída.

    Usa o mesmo orçamento do operador. As fórmulas supõem consumo completo
    e não modelam reuso no buffer. Levanta os mesmos NotApplicable do plano.
    """
    left, right, _, pages = _plan(eng, r, s)
    a, b = _input_cost(eng, left, pages), _input_cost(eng, right, pages)
    seeks = None if a.seeks is None or b.seeks is None else a.seeks + b.seeks
    return Cost(a.reads + b.reads, a.writes + b.writes, seeks,
                f"sort-merge, mem={pages}: r [{a.formula}] + s [{b.formula}]")
