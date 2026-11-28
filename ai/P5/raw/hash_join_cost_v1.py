"""Hash join GRACE sobre fluxos reiniciáveis. Camada 2, dono: P5.

Usa: JoinSide/TableLike, TempFile, BufferPool e read_all do P4.
Decisões: D-P5-01 a D-P5-03 (docs/decisoes/P5.md).
Invariantes: reserva + cursores WORK <= M; saída r+s; None não casa;
construção termina antes de emitir resultados; temporários têm dono explícito.
Custo típico: direto b_r+b_s; uma rodada GRACE aproximadamente 3(b_r+b_s).
LIMITAÇÃO: versão N1; bfr das partições é herdado da tabela, conforme D-P5-03.
Não usar esta versão para validar memória física ou medições antes da integração.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from itertools import islice
from math import ceil, floor, isfinite
from typing import Callable, Iterator

from cbd.contracts import Cost, JoinSide, Record, Schema
from cbd.records.hashing import hash_value


_MAX_LEVELS = 3
_BASE_SEED = 100


class _Overflow(Exception):
    """Construtor excedeu a reserva antes de começar a sondagem."""


@dataclass(frozen=True)
class _Source:
    schema: Schema
    column: int
    scan: Callable[[], Iterator[Record]]
    blocks: int
    bfr: float


@dataclass(frozen=True)
class _Plan:
    build_name: str
    build: _Source
    probe: _Source
    variant: str
    partitions: int


def _source(side, stats):
    def scan():
        # Importação adiada: o custo pode ser consultado antes de P4 existir.
        # O substituto de read_all fica SOMENTE nos testes, nunca nesta camada.
        from cbd.exec.access import read_all
        return read_all(side)

    return _Source(side.table.schema, side.table.schema.idx(side.column), scan,
                   stats.n_blocks, stats.bfr)


def _partition_count(blocks, memory):
    # D-P5-02: uma página WORK fica disponível para a entrada temporária.
    return min(memory - 1, max(2, ceil(1.2 * blocks / (memory - 2))))


def _plan(eng, r, s, build):
    if eng.M < 3:
        raise ValueError("hash_join precisa de M >= 3 páginas WORK")
    if build not in ("auto", "r", "s"):
        raise ValueError("build deve ser 'auto', 'r' ou 's'")
    rs, ss = r.table.stats(), s.table.stats()
    name = ("r" if rs.n_blocks <= ss.n_blocks else "s") if build == "auto" else build
    left, right = _source(r, rs), _source(s, ss)
    b, p = (left, right) if name == "r" else (right, left)
    variant = "in-memory" if b.blocks <= eng.M - 2 else "grace"
    return _Plan(name, b, p, variant,
                 0 if variant == "in-memory" else _partition_count(b.blocks, eng.M))


@contextmanager
def _scan(source):
    it = iter(source.scan())
    try:
        yield it
    finally:
        close = getattr(it, "close", None)
        if close is not None:
            close()


def _capacity(memory, bfr):
    # D-P5-03: limite lógico N1, não bytes reais de listas/dicionários Python.
    if not isfinite(bfr) or bfr <= 0:
        raise ValueError("bfr deve ser positivo e finito para entrada não vazia")
    limit = floor((memory - 2) * bfr)
    if limit < 1:
        raise ValueError("bfr insuficiente para um registro na reserva WORK")
    return limit


def _orient(build_record, probe_record, build_r):
    return build_record + probe_record if build_r else probe_record + build_record


def _build_probe(eng, build, probe, build_r):
    limit = _capacity(eng.M, build.bfr)
    # D-P5-01: só a área reservada pode reter registros do construtor.
    with eng.bp.reserve_work(eng.M - 2):
        table = defaultdict(list)
        count = 0
        try:
            with _scan(build) as records:
                for record in records:
                    key = record[build.column]
                    if key is None:
                        continue
                    if count >= limit:
                        raise _Overflow
                    table[key].append(record)
                    count += 1
            # Não emitir resultados até ter certeza de que todo o build coube.
            if not table:
                return
            with _scan(probe) as records:
                for record in records:
                    key = record[probe.column]
                    if key is not None:
                        for match in table.get(key, ()):
                            yield _orient(match, record, build_r)
        finally:
            # Libera as referências ANTES de devolver os frames reservados.
            table.clear()


def _partition(eng, source, number, seed):
    parts = []
    # Se criação, append, leitura ou finish falhar, todos os arquivos já criados
    # são removidos. Após sucesso, o chamador assume a propriedade dos arquivos.
    with ExitStack() as cleanup:
        for _ in range(number):
            part = eng.catalog.temp(source.schema)
            parts.append(part)
            cleanup.callback(part.drop)
        with _scan(source) as records:
            for record in records:
                key = record[source.column]
                if key is not None:
                    parts[hash_value(key, seed) % number].append(record)
        for part in parts:
            part.finish()
        cleanup.pop_all()
    return parts


def _temporary_source(part, original):
    # LIMITAÇÃO D-P5-03: substituir/validar bfr com a amostragem real do P4 em N2.
    return _Source(original.schema, original.column, part.scan,
                   part.n_blocks(), original.bfr)


def _block_join(eng, build, probe, build_r):
    limit = _capacity(eng.M, build.bfr)
    # D-P5-02: duas páginas restantes permitem ler build e probe temporários.
    with eng.bp.reserve_work(eng.M - 2):
        with _scan(build) as records:
            nonnull = (rec for rec in records if rec[build.column] is not None)
            chunk = []
            try:
                while True:
                    chunk = list(islice(nonnull, limit))
                    if not chunk:
                        return
                    with _scan(probe) as probe_records:
                        for record in probe_records:
                            key = record[probe.column]
                            if key is not None:
                                for match in chunk:
                                    if match[build.column] == key:
                                        yield _orient(match, record, build_r)
                    # O alvo do for sobrevive ao laço e também retém um registro.
                    match = None
                    # Evita reter o lote anterior ao materializar o seguinte.
                    chunk.clear()
            finally:
                match = None
                chunk.clear()
                nonnull.close()


def _join_pair(eng, build, probe, build_r, level, info):
    if build.blocks == 0 or probe.blocks == 0:
        return
    if build.blocks <= eng.M - 2:
        try:
            yield from _build_probe(eng, build, probe, build_r)
        except _Overflow:
            pass
        else:
            return
        # A exceção (e seu traceback com o dict) já saiu de escopo antes da
        # recursão. Nenhuma tupla daquele par foi emitida no caminho que falhou.
    if level >= _MAX_LEVELS:
        info["skew_fallback"] += 1
        yield from _block_join(eng, build, probe, build_r)
        return
    if level == 0:
        info["variant"] = "grace"
    number = _partition_count(build.blocks, eng.M)
    info["levels"] = max(info["levels"], level + 1)
    info["partitions"] += number
    # D-P5-02: nova seed em cada nível; todos os pares do nível usam a mesma.
    seed = _BASE_SEED + level
    with ExitStack() as cleanup:
        bp = _partition(eng, build, number, seed)
        for part in bp:
            cleanup.callback(part.drop)
        pp = _partition(eng, probe, number, seed)
        for part in pp:
            cleanup.callback(part.drop)
        for bpart, ppart in zip(bp, pp):
            yield from _join_pair(eng, _temporary_source(bpart, build),
                                  _temporary_source(ppart, probe), build_r,
                                  level + 1, info)
            bpart.drop()
            ppart.drop()


def hash_join(eng, r: JoinSide, s: JoinSide, build: str = "auto", info=None) -> Iterator[Record]:
    """Junta r e s por igualdade, emitindo r_rec+s_rec; None nunca casa.

    `eng.M`: páginas WORK (mínimo 3). `build`: auto (menos blocos), r ou s.
    `info`: variante, construtor, total de partições por lado criadas,
    profundidade máxima (0 a 3), número de pares tratados por laço e index_used.
    Levanta ValueError para M/build/bfr inválidos e propaga erros das entradas.
    Fechar o iterador libera reservas, cursores e temporários, inclusive em erro.
    Custo típico: direto b_r+b_s; GRACE sem recursão cerca de 3(b_r+b_s).
    Versão N1: capacidade das partições ainda depende do bfr herdado (D-P5-03).
    """
    plan = _plan(eng, r, s, build)
    details = info if info is not None else {}
    details.update(variant=plan.variant, build=plan.build_name, partitions=0,
                   levels=0, skew_fallback=0, index_used=False)
    yield from _join_pair(eng, plan.build, plan.probe, plan.build_name == "r", 0, details)


def hash_join_cost(eng, r, s, build="auto") -> Cost:
    """Estima transferências de blocos pelo mesmo plano, sem ler registros.

    `eng.M`: páginas WORK; build: auto/r/s, com a mesma validação do operador.
    Direto: b_r+b_s. GRACE: (2L+1)(b_r+b_s)+4P, L rodadas e P total
    estimado de partições por lado, com dispersão uniforme e folga do guia.
    Após três níveis, acrescenta as releituras uniformes do laço em blocos.
    D-P5-04: não prevê skew, páginas retidas em cache nem bfr incorreto.
    Seeks são aproximação com uma página por saída (b_b=1), não previsão física.
    Levanta ValueError para M/build inválidos. Custo de execução: 0 blocos.
    """
    plan = _plan(eng, r, s, build)
    total = plan.build.blocks + plan.probe.blocks
    if plan.variant == "in-memory":
        return Cost(total, formula=f"in-memory: b_r+b_s = {total}; sem escrita de saída")
    # D-P5-04: reproduz o fan-out do plano, supondo partições uniformes.
    size = plan.build.blocks
    pairs = 1
    partitions = rounds = 0
    while size > eng.M - 2 and rounds < _MAX_LEVELS:
        number = _partition_count(size, eng.M)
        pairs *= number
        partitions += pairs
        size = ceil(size / number)
        rounds += 1
    extra = 0
    if size > eng.M - 2:
        probe_size = ceil(plan.probe.blocks / pairs)
        extra = pairs * (ceil(size / (eng.M - 2)) - 1) * probe_size
    reads = (rounds + 1) * total + 2 * partitions + extra
    writes = rounds * total + 2 * partitions
    formula = (
        f"grace uniforme: L={rounds}, P={partitions}, b_r+b_s={total}; "
        f"reads=(L+1)*{total}+2*P+{extra}={reads}; "
        f"writes=L*{total}+2*P={writes}; "
        f"total=(2L+1)*{total}+4*P+{extra}={reads + writes}; "
        "4P=arredondamento do guia; extra=releituras do laço após três níveis; "
        "não prevê skew/cache; saída não materializada"
    )
    return Cost(reads, writes, 2 * rounds * total + 2 * partitions + extra, formula)
