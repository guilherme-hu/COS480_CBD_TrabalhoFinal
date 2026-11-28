"""Hash join GRACE e reaproveitamento de hash primário. Camada 2, dono: P5.

Usa: JoinSide/TableLike, TempFile, BufferPool e read_all do P4.
Decisões: D-P5-01 a D-P5-10 (docs/decisoes/P5.md).
Invariantes: reserva + cursores WORK <= M; saída r+s; None não casa;
construção termina antes de emitir resultados; temporários têm dono explícito.
Custo típico: direto b_r+b_s; uma rodada GRACE aproximadamente 3(b_r+b_s).
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from math import ceil, floor, isfinite
from typing import Callable, Iterator

from cbd.contracts import Cost, JoinSide, NotApplicable, Org, Path, Record, RecordTooLarge, Schema
from cbd.exec.access import read_all
from cbd.records.codec import RecordCodec
from cbd.records.hashing import hash_value
from cbd.records.slotted_page import SlottedPage


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
    n_records: int | None = None


@dataclass(frozen=True)
class _Plan:
    build_name: str
    build: _Source
    probe: _Source
    variant: str
    partitions: int
    build_hash: object | None = None
    probe_hash: object | None = None


def _source(side, stats):
    blocks, count, bfr = stats.n_blocks, stats.n_records, stats.bfr
    # D-P5-10: o catálogo descreve o último analyze. Alterações posteriores
    # não podem esconder uma tabela não vazia. Os arquivos mantêm os totais.
    current_stats = getattr(side.table.data, "stats", None)
    if current_stats is not None:
        data = current_stats()
        count = data["records"]
        if side.table.data.spec.org is Org.BTREE:
            blocks = data["leaf_blocks"]
        else:
            blocks = data["blocks"] - data["meta_blocks"]
        bfr = count / blocks if blocks else 0.0
    return _Source(side.table.schema, side.table.schema.idx(side.column),
                   lambda: read_all(side), blocks, bfr, count)


def _primary_hash(side):
    data = side.table.data
    if (side.path is Path.PRIMARY and data.spec.org is Org.HASH
            and data.spec.key == side.column):
        return data
    return None


def _partition_count(blocks, memory):
    # D-P5-02: uma página WORK fica disponível para a entrada temporária.
    return min(memory - 1, max(2, ceil(1.2 * blocks / (memory - 2))))


def _plan(eng, r, s, build):
    if eng.M < 3:
        raise ValueError("hash_join precisa de M >= 3 páginas WORK")
    if build not in ("auto", "r", "s"):
        raise ValueError("build deve ser 'auto', 'r' ou 's'")
    rs, ss = r.table.stats(), s.table.stats()
    left, right = _source(r, rs), _source(s, ss)
    # D-P5-10: o domínio PK→FK usa tipos iguais. O hash do P2 distingue
    # int de float mesmo quando o Python os considera iguais.
    if left.schema.fields[left.column].ftype != right.schema.fields[right.column].ftype:
        raise NotApplicable("hash join requer colunas do mesmo tipo")
    name = ("r" if left.blocks <= right.blocks else "s") if build == "auto" else build
    b, p = (left, right) if name == "r" else (right, left)
    if b.n_records == 0 or p.n_records == 0:
        return _Plan(name, b, p, "empty", 0)
    if b.blocks <= eng.M - 2:
        return _Plan(name, b, p, "in-memory", 0)
    bh, ph = (_primary_hash(side) for side in ((r, s) if name == "r" else (s, r)))
    number = _partition_count(b.blocks, eng.M)
    # D-P5-07/08: reutilizar só a divisão física que preserva a igualdade.
    if bh is not None and ph is not None:
        if (bh.n_buckets, bh.seed) == (ph.n_buckets, ph.seed):
            return _Plan(name, b, p, "bucket-join", bh.n_buckets, bh, ph)
    elif bh is not None or ph is not None:
        hashed = bh if bh is not None else ph
        return _Plan(name, b, p, "prepartitioned", min(number, hashed.n_buckets), bh, ph)
    return _Plan(name, b, p, "grace", number)


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
    # D-P5-06: bfr<1 descreve espaços vazios; um registro não estendido
    # armazenável ainda cabe em uma página. O restante continua sendo modelo N1.
    return floor((memory - 2) * max(1, bfr))


class _Budget:
    """Espaço representado como páginas não estendidas da área reservada."""

    def __init__(self, eng, source, pages):
        self.pages = pages
        self.limit = _capacity(pages + 2, source.bfr)
        self.codec = RecordCodec(source.schema)
        self.payload = eng.bp.block_size - SlottedPage.HEADER
        self.slot = self.payload - SlottedPage.max_record(eng.bp.block_size)
        self.reset()

    def reset(self):
        self.used_pages, self.occupied, self.count = 1, 0, 0

    def add(self, record):
        # D-P5-10: teto por registros e por bytes incluindo slot e cabeçalho.
        size = len(self.codec.encode(record)) + self.slot
        if size > self.payload:
            raise RecordTooLarge("Registro excede uma página WORK do hash join")
        if self.count == self.limit:
            return False
        if self.occupied + size > self.payload:
            if self.used_pages == self.pages:
                return False
            self.used_pages += 1
            self.occupied = 0
        self.occupied += size
        self.count += 1
        return True


def _orient(build_record, probe_record, build_r):
    return build_record + probe_record if build_r else probe_record + build_record


def _build_probe(eng, build, probe, build_r):
    # D-P5-01: só a área reservada pode reter registros do construtor.
    with eng.bp.reserve_work(eng.M - 2) as pages:
        budget = _Budget(eng, build, pages)
        table = defaultdict(list)
        try:
            with _scan(build) as records:
                for record in records:
                    key = record[build.column]
                    if key is None:
                        continue
                    if not budget.add(record):
                        raise _Overflow
                    table[key].append(record)
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


def _partition(eng, source, number, partition_of):
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
                    parts[partition_of(key)].append(record)
        for part in parts:
            part.finish()
        cleanup.pop_all()
    return parts


def _temporary_source(part, original):
    blocks, count = part.n_blocks(), part.n_records()
    return _Source(original.schema, original.column, part.scan,
                   blocks, count / blocks if blocks else 0.0, count)


def _block_join(eng, build, probe, build_r):
    # D-P5-02: duas páginas restantes permitem ler build e probe temporários.
    with eng.bp.reserve_work(eng.M - 2) as pages:
        budget = _Budget(eng, build, pages)
        with _scan(build) as records:
            chunk = []
            try:
                for record in records:
                    if record[build.column] is None:
                        continue
                    if not budget.add(record):
                        # O registro seguinte ainda pertence ao cursor de entrada.
                        yield from _probe_chunk(build, probe, build_r, chunk)
                        chunk.clear()
                        budget.reset()
                        assert budget.add(record)
                    chunk.append(record)
                    # Devolve lotes com teto inteiro sem avançar a entrada.
                    if budget.count == budget.limit:
                        yield from _probe_chunk(build, probe, build_r, chunk)
                        chunk.clear()
                        record = None
                        budget.reset()
                if chunk:
                    yield from _probe_chunk(build, probe, build_r, chunk)
            finally:
                chunk.clear()


def _probe_chunk(build, probe, build_r, chunk):
    # O alvo do for vive só nesta função e não retém o lote anterior.
    with _scan(probe) as records:
        for record in records:
            key = record[probe.column]
            if key is not None:
                for match in chunk:
                    if match[build.column] == key:
                        yield _orient(match, record, build_r)


def _join_pair(eng, build, probe, build_r, level, info):
    # D-P5-05: mesma regra nas tabelas-base e nos temporários.
    if (build.n_records == 0 or probe.n_records == 0
            or build.blocks == 0 or probe.blocks == 0):
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

    def partition_of(key):
        return hash_value(key, seed) % number

    with ExitStack() as cleanup:
        bp = _partition(eng, build, number, partition_of)
        for part in bp:
            cleanup.callback(part.drop)
        pp = _partition(eng, probe, number, partition_of)
        for part in pp:
            cleanup.callback(part.drop)
        for bpart, ppart in zip(bp, pp):
            yield from _join_pair(eng, _temporary_source(bpart, build),
                                  _temporary_source(ppart, probe), build_r,
                                  level + 1, info)
            bpart.drop()
            ppart.drop()


def _bucket_source(source, hashed, start, step):
    def scan():
        for bucket in range(start, hashed.n_buckets, step):
            rows = hashed.scan_bucket(bucket)
            try:
                for _, record in rows:
                    yield record
            finally:
                rows.close()
    # O contrato não expõe o tamanho de um bucket sem scan. _build_probe
    # verifica o limite durante a leitura.
    return _Source(source.schema, source.column, scan, source.blocks, source.bfr)


def _join_aligned_pair(eng, build, probe, build_r, info):
    if build.n_records == 0 or probe.n_records == 0:
        return
    try:
        yield from _build_probe(eng, build, probe, build_r)
    except _Overflow:
        pass
    else:
        return
    # D-P5-09: depois do excesso, releia o par sem criar outro temporário.
    info["skew_fallback"] += 1
    yield from _block_join(eng, build, probe, build_r)


def _reuse_primary(eng, plan, info):
    number = plan.partitions
    build_r = plan.build_name == "r"
    info.update(index_used=True, partitions=number)
    if plan.variant == "bucket-join":
        for bucket in range(number):
            build = _bucket_source(plan.build, plan.build_hash, bucket, number)
            probe = _bucket_source(plan.probe, plan.probe_hash, bucket, number)
            yield from _join_aligned_pair(eng, build, probe, build_r, info)
        return

    hash_build = plan.build_hash is not None
    hashed = plan.build_hash if hash_build else plan.probe_hash
    resident, other = (plan.build, plan.probe) if hash_build else (plan.probe, plan.build)
    # D-P5-08: aplicar o bucket_of público antes do módulo por n_h.
    def partition_of(key):
        return hashed.bucket_of(key) % number

    info["levels"] = 1
    with ExitStack() as cleanup:
        parts = _partition(eng, other, number, partition_of)
        for part in parts:
            cleanup.callback(part.drop)
        for group, part in enumerate(parts):
            disk = _bucket_source(resident, hashed, group, number)
            temp = _temporary_source(part, other)
            build, probe = (disk, temp) if hash_build else (temp, disk)
            yield from _join_aligned_pair(eng, build, probe, build_r, info)
            part.drop()


def hash_join(eng, r: JoinSide, s: JoinSide, build: str = "auto", info=None) -> Iterator[Record]:
    """Junta r e s por igualdade, emitindo r_rec+s_rec; None nunca casa.

    `eng.M`: páginas WORK (mínimo 3). `build`: auto (menos blocos), r ou s.
    `info`: variante, construtor, total de partições por lado criadas,
    profundidade máxima (0 a 3), número de pares tratados por laço e index_used.
    Levanta ValueError para M/build/bfr inválidos e propaga erros das entradas.
    Fechar o iterador libera reservas, cursores e temporários, inclusive em erro.
    Entrada com zero registros: variante empty, nenhum scan ou temporário.
    Custo típico: direto b_r+b_s; GRACE sem recursão cerca de 3(b_r+b_s).
    PRIMARY HASH compatível evita reparticionar um ou ambos os lados.
    Tipos de chave distintos levantam NotApplicable.
    """
    plan = _plan(eng, r, s, build)
    details = info if info is not None else {}
    details.update(variant=plan.variant, build=plan.build_name, partitions=0,
                   levels=0, skew_fallback=0, index_used=False)
    if plan.variant in ("bucket-join", "prepartitioned"):
        yield from _reuse_primary(eng, plan, details)
    else:
        yield from _join_pair(eng, plan.build, plan.probe, plan.build_name == "r", 0, details)


def hash_join_cost(eng, r, s, build="auto") -> Cost:
    """Estima transferências de blocos pelo mesmo plano, sem ler registros.

    `eng.M`: páginas WORK; build: auto/r/s, com a mesma validação do operador.
    Vazio por n_records: zero (D-P5-05). Direto: b_r+b_s.
    GRACE: (2L+1)(b_r+b_s)+4P, L rodadas e P total
    estimado de partições por lado, com dispersão uniforme e folga do guia.
    Após três níveis, acrescenta as releituras uniformes do laço em blocos.
    D-P5-04: não prevê skew, páginas retidas em cache nem bfr incorreto.
    Seeks são aproximação com uma página por saída (b_b=1), não previsão física.
    Levanta ValueError para M/build inválidos. Custo de execução: 0 blocos.
    """
    plan = _plan(eng, r, s, build)
    if plan.variant == "empty":
        return Cost(0, seeks=0, formula="empty: n_records=0 em um lado; 0 scans, 0 transferências (D-P5-05)")
    total = plan.build.blocks + plan.probe.blocks
    if plan.variant == "in-memory":
        return Cost(total, formula=f"in-memory: b_r+b_s = {total}; sem escrita de saída")
    if plan.variant == "bucket-join":
        return Cost(total, formula=f"bucket-join: b_r+b_s = {total}; sem temporários; sem prever releituras por skew")
    if plan.variant == "prepartitioned":
        hashed, other = (plan.build, plan.probe) if plan.build_hash is not None else (plan.probe, plan.build)
        n = plan.partitions
        return Cost(hashed.blocks + 2 * other.blocks + n, other.blocks + n,
                    formula=(f"prepartitioned: {hashed.blocks} + 3*{other.blocks} + 2*{n}; "
                             "sem prever cache, grupos vazios ou releituras por skew"))
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
