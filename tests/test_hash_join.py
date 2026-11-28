"""N1 de P5: dublês verificam lógica e reservas, nunca medem I/O físico."""
from collections import Counter
from contextlib import contextmanager
from math import ceil
import random
import sys
from types import ModuleType, SimpleNamespace

import pytest

from cbd.contracts import Field, FType, JoinSide, Org, OrgSpec, Path, Schema, TableStats
from cbd.exec.hash_join import hash_join, hash_join_cost
from cbd.records.hashing import hash_value


class _Work:
    """Contabiliza reserva + cursores WORK; DATA tem contagem separada."""
    def __init__(self, memory):
        self.memory = memory
        self.block_size = 4096
        self.reserved = self.work = self.data = self.peak = 0
        self.reservations = []

    def _check(self):
        used = self.reserved + self.work
        assert used <= self.memory, (used, self.memory)
        self.peak = max(self.peak, used)

    @contextmanager
    def reserve_work(self, number):
        self.reserved += number
        self.reservations.append(number)
        try:
            self._check()
            yield number
        finally:
            self.reserved -= number

    def pin_work(self):
        self.work += 1
        try:
            self._check()
        except BaseException:
            self.work -= 1
            raise

    def pinned(self):
        return self.work + self.data


class _Data:
    def __init__(self, rows, work):
        self.rows, self.work = rows, work
        self.spec = OrgSpec(Org.HEAP)
        self.fail_after = None
        self.scans = 0

    def scan(self):
        self.scans += 1
        self.work.data += 1
        try:
            for pos, record in enumerate(self.rows):
                if pos == self.fail_after:
                    raise RuntimeError("falha na entrada")
                yield pos, record
        finally:
            self.work.data -= 1


class _Temp:
    def __init__(self, catalog, schema, bfr):
        self.catalog, self.schema, self.bfr = catalog, schema, bfr
        self.rows = []
        self.output = self.readers = 0
        self.finished = self.dropped = False

    def append(self, record):
        assert not self.finished and not self.dropped
        if not self.output:
            self.catalog.work.pin_work()
            self.output = 1
        self.catalog.appends += 1
        if self.catalog.appends == self.catalog.fail_append:
            raise RuntimeError("falha no append")
        self.rows.append(record)

    def finish(self):
        self.catalog.finishes += 1
        if self.catalog.finishes == self.catalog.fail_finish:
            raise RuntimeError("falha no finish")
        self.catalog.work.work -= self.output
        self.output = 0
        self.finished = True

    def scan(self):
        assert self.finished and not self.dropped
        self.readers += 1
        self.catalog.work.pin_work()
        try:
            yield from self.rows
        finally:
            self.catalog.work.work -= 1
            self.readers -= 1

    def n_blocks(self):
        return ceil(len(self.rows) / self.bfr)

    def n_records(self):
        return len(self.rows)

    def drop(self):
        if self.dropped:
            return
        assert self.readers == 0, "drop antes de fechar o cursor"
        self.catalog.work.work -= self.output
        self.output = 0
        self.rows.clear()
        self.dropped = True
        self.catalog.live.remove(self)


class _HashData(_Data):
    """Dublê da interface pública HashFile; inclui registros de overflow no fluxo."""
    def __init__(self, rows, work, schema, key, buckets, seed):
        super().__init__(rows, work)
        self.spec = OrgSpec(Org.HASH, key=key, params={"buckets": buckets, "seed": seed})
        self.n_buckets, self.seed = buckets, seed
        self.column = schema.idx(key)
        self.bucket_scans = []
        self.fail_bucket = None

    def bucket_of(self, key):
        return hash_value(key, self.seed) % self.n_buckets

    def scan_bucket(self, bucket):
        assert 0 <= bucket < self.n_buckets
        self.bucket_scans.append(bucket)
        self.work.data += 1
        try:
            if bucket == self.fail_bucket:
                raise RuntimeError("falha no bucket")
            for pointer, record in enumerate(self.rows):
                if self.bucket_of(record[self.column]) == bucket:
                    yield pointer, record
        finally:
            self.work.data -= 1


def _as_hash(side, eng, buckets=11, seed=17, key=None):
    key = side.column if key is None else key
    side.table.data = _HashData(side.table.data.rows, eng.bp, side.table.schema,
                                key, buckets, seed)
    side.path = Path.PRIMARY
    stats = side.table.stats()
    counts = Counter(side.table.data.bucket_of(rec[side.table.data.column])
                     for rec in side.table.data.rows)
    # Estatísticas do cenário N1: um bloco primário por bucket, mais os blocos
    # necessários aos registros do dublê; não são contadores de I/O medido.
    packing = max(1, stats.bfr)
    stats.n_blocks = sum(max(1, ceil(counts[i] / packing)) for i in range(buckets))
    stats.bfr = stats.n_records / stats.n_blocks
    return side.table.data


def _bucket_keys(buckets, seed, per_bucket=2):
    """Escolhe chaves com ocupação conhecida para calcular o cenário à mão."""
    found = [[] for _ in range(buckets)]
    key = 0
    while any(len(group) < per_bucket for group in found):
        group = found[hash_value(key, seed) % buckets]
        if len(group) < per_bucket:
            group.append(key)
        key += 1
    return [key for group in found for key in group]


class _Catalog:
    def __init__(self, work, bfr):
        self.work, self.bfr = work, bfr
        self.live = set()
        self.created = self.appends = self.finishes = 0
        self.schemas = []
        self.fail_append = self.fail_finish = None

    def temp(self, schema):
        part = _Temp(self, schema, self.bfr[schema.name])
        self.live.add(part)
        self.created += 1
        self.schemas.append(schema.name)
        return part


@pytest.fixture
def case(monkeypatch):
    access = ModuleType("cbd.exec.access")

    def read_all(side):
        cursor = side.table.data.scan()
        try:
            for _, rec in cursor:
                yield rec
        finally:
            cursor.close()

    access.read_all = read_all
    # Só o teste substitui o módulo ausente do P4; produção depende da interface.
    monkeypatch.setitem(sys.modules, "cbd.exec.access", access)
    engines = []

    def make(rrows, srows, memory=4, bfr=2, unique_r=True):
        work = _Work(memory)
        eng = SimpleNamespace(M=memory, bp=work, catalog=_Catalog(work, {"r": bfr, "s": bfr}))

        def side(name, rows, unique):
            schema = Schema(name, (Field("key", FType.INT, nullable=True, unique=unique),
                                   Field("payload", FType.INT)))
            rows = list(rows)
            stats = TableStats(len(rows), ceil(len(rows) / bfr), bfr, 16, {}, {}, {})
            table = SimpleNamespace(name=name, schema=schema, data=_Data(rows, work),
                                    indexes={}, stats=lambda: stats)
            return JoinSide(table, "key", Path.SCAN)

        r, s = side("r", rrows, unique_r), side("s", srows, False)
        engines.append((eng, r, s))
        return eng, r, s

    yield make
    for eng, r, s in engines:
        assert eng.bp.pinned() == 0
        assert eng.bp.reserved == 0
        assert not eng.catalog.live


def _expected(r, s):
    return Counter(a + b for a in r for b in s if a[0] is not None and a[0] == b[0])


@pytest.mark.parametrize("memory", [3, 4, 8, 64])
def test_hj_vs_bruteforce(case, memory):
    rng = random.Random(480)
    rr = [(i, 100 + i) for i in range(30)]
    ss = [(rng.choice([None, *range(40)]), i) for i in range(120)]
    rng.shuffle(rr)
    eng, r, s = case(rr, ss, memory)
    assert Counter(hash_join(eng, r, s)) == _expected(rr, ss)


@pytest.mark.parametrize("build", ["r", "s", "auto"])
def test_orientation(case, build):
    rr = [(i, i + 100) for i in range(12)]
    ss = [(i % 12, i) for i in range(90)]
    eng, r, s = case(rr, ss)
    info = {}
    assert Counter(hash_join(eng, r, s, build=build, info=info)) == _expected(rr, ss)
    assert info["build"] == ("r" if build == "auto" else build)


def test_in_memory_without_temporaries(case):
    eng, r, s = case([(1, 100), (2, 200)], [(1, i) for i in range(30)], memory=4)
    info = {}
    assert list(hash_join(eng, r, s, info=info)) == [(1, 100, 1, i) for i in range(30)]
    assert info["variant"] == "in-memory"
    assert info["index_used"] is False
    assert eng.catalog.created == 0
    assert eng.bp.reservations == [2]


@pytest.mark.parametrize("rr,ss", [([], []), ([], [(1, 5)]), ([(1, 4)], []),
                                     ([(None, 1)], [(None, 2)]), ([(1, 1)], [(2, 2)])])
def test_empty_null_and_missing(case, rr, ss):
    eng, r, s = case(rr, ss)
    assert list(hash_join(eng, r, s)) == []


def test_hj_recursion(case):
    # Build: 160 registros / 2 por bloco = 80 blocos, isto é, 20*M com M=4.
    rr, ss = [(i, i + 1000) for i in range(160)], [(i, -i) for i in range(180)]
    eng, r, s = case(rr, ss, memory=4)
    info = {}
    assert Counter(hash_join(eng, r, s, build="r", info=info)) == _expected(rr, ss)
    assert info["variant"] == "grace"
    assert 1 <= info["levels"] <= 3
    assert info["partitions"] >= 3
    assert eng.bp.peak <= eng.M


def test_hj_skew(case):
    rr = [(7, 999)]
    ss = [(7, i) for i in range(30)]
    eng, r, s = case(rr, ss, memory=3)
    info = {}
    assert Counter(hash_join(eng, r, s, build="s", info=info)) == _expected(rr, ss)
    assert info["skew_fallback"] >= 1
    assert info["levels"] == 3
    assert eng.bp.peak == eng.M


def test_skew_cartesian_regression(case):
    # Caso adversarial da issue: ambos repetidos, sem adicionar variante opcional.
    rr, ss = [(7, i) for i in range(11)], [(7, i + 100) for i in range(13)]
    eng, r, s = case(rr, ss, memory=3, unique_r=False)
    info = {}
    result = list(hash_join(eng, r, s, info=info))
    assert len(result) == 11 * 13
    assert Counter(result) == _expected(rr, ss)
    assert info["skew_fallback"] >= 1


def test_abandon(case):
    eng, r, s = case([(i, i) for i in range(80)], [(i % 80, i) for i in range(640)])
    query = hash_join(eng, r, s)
    for _ in range(5):
        next(query)
    assert eng.catalog.live
    query.close()
    assert eng.bp.pinned() == eng.bp.reserved == 0
    assert not eng.catalog.live


def test_abandon_before_start(case):
    eng, r, s = case([(1, 1)], [(1, 2)])
    query = hash_join(eng, r, s)
    query.close()
    assert eng.catalog.created == 0
    assert r.table.data.scans == s.table.data.scans == 0


@pytest.mark.parametrize("failure", ["build", "probe", "append", "finish"])
def test_cleanup_on_partition_error(case, failure):
    eng, r, s = case([(i, i) for i in range(30)], [(i, -i) for i in range(30)])
    if failure in ("build", "probe"):
        (r if failure == "build" else s).table.data.fail_after = 5
    elif failure == "append":
        eng.catalog.fail_append = 5
    else:
        eng.catalog.fail_finish = 2
    with pytest.raises(RuntimeError, match="falha"):
        list(hash_join(eng, r, s, build="r"))
    assert eng.bp.pinned() == eng.bp.reserved == 0
    assert not eng.catalog.live


def test_cleanup_on_in_memory_probe_error(case):
    eng, r, s = case([(1, 1)], [(1, i) for i in range(15)])
    s.table.data.fail_after = 8
    query = hash_join(eng, r, s)
    assert next(query) == (1, 1, 1, 0)
    with pytest.raises(RuntimeError, match="entrada"):
        list(query)
    assert eng.bp.pinned() == eng.bp.reserved == 0


def test_underestimated_build_restarts_before_output(case):
    rr, ss = [(i, 100 + i) for i in range(25)], [(i, i) for i in range(25)]
    eng, r, s = case(rr, ss)
    r.table.stats().n_blocks = 1
    info = {}
    assert Counter(hash_join(eng, r, s, info=info)) == _expected(rr, ss)
    assert info["variant"] == "grace"


@pytest.mark.parametrize("memory", [3, 64])
@pytest.mark.parametrize("build", ["r", "s"])
def test_join_columns_at_different_positions(case, memory, build):
    eng, r, s = case([(i, i) for i in range(10)], [(i, i) for i in range(25)], memory)
    rr = [(100 + i, i) for i in range(10)]
    ss = [(200 + i, 300 + i, i % 12 if i != 3 else None) for i in range(25)]
    r.table.schema = Schema("r", (Field("payload", FType.INT), Field("pk", FType.INT, unique=True)))
    s.table.schema = Schema("s", (Field("one", FType.INT), Field("two", FType.INT),
                                  Field("fk", FType.INT, nullable=True)))
    r.column, s.column = "pk", "fk"
    r.table.data.rows, s.table.data.rows = rr, ss
    expected = Counter(a + b for a in rr for b in ss if b[2] is not None and a[1] == b[2])
    assert Counter(hash_join(eng, r, s, build=build)) == expected


def test_auto_chooses_smaller_s(case):
    rr, ss = [(i, 100 + i) for i in range(80)], [(i, 200 + i) for i in range(10)]
    eng, r, s = case(rr, ss)
    info = {}
    assert Counter(hash_join(eng, r, s, info=info)) == _expected(rr, ss)
    assert info["build"] == "s"


def test_auto_chooses_in_memory_s(case):
    rr, ss = [(i, i) for i in range(40)], [(1, 100)]
    eng, r, s = case(rr, ss)
    info = {}
    assert Counter(hash_join(eng, r, s, info=info)) == _expected(rr, ss)
    assert info["build"] == "s"
    assert info["variant"] == "in-memory"
    assert eng.catalog.created == 0
    assert hash_join_cost(eng, r, s).writes == 0


@pytest.mark.parametrize("bfr,limit", [(1.25, 2), (1.6, 3), (2, 4), (2.25, 4)])
@pytest.mark.parametrize("extra", [0, 1])
def test_underestimated_build_boundary(case, bfr, limit, extra):
    rr = [(i, 100 + i) for i in range(limit + extra)]
    ss = [(i, i) for i in range(limit + extra)]
    eng, r, s = case(rr, ss, memory=4, bfr=bfr)
    r.table.stats().n_blocks = 1
    info = {}
    assert Counter(hash_join(eng, r, s, info=info)) == _expected(rr, ss)
    # M−2=2: floor(2×1,25)=2; floor(2×1,6)=3; floor(2×2)=4;
    # floor(2×2,25)=4. Um registro além desses tetos deve provocar overflow.
    assert info["variant"] == ("in-memory" if extra == 0 else "grace")


def test_abandon_during_skew_fallback(case):
    eng, r, s = case([(1, 100)], [(1, i) for i in range(30)], memory=3)
    info = {}
    query = hash_join(eng, r, s, build="s", info=info)
    for _ in range(5):
        next(query)
    assert info["skew_fallback"] >= 1
    assert eng.bp.reserved == 1
    query.close()
    assert eng.bp.reserved == eng.bp.pinned() == 0
    assert not eng.catalog.live


def test_cleanup_on_temporary_creation_error(case, monkeypatch):
    eng, r, s = case([(i, i) for i in range(30)], [(i, -i) for i in range(30)])
    temp = eng.catalog.temp
    calls = 0

    def fail(schema):
        nonlocal calls
        calls += 1
        if calls == 5:
            raise RuntimeError("falha na criação")
        return temp(schema)

    monkeypatch.setattr(eng.catalog, "temp", fail)
    with pytest.raises(RuntimeError, match="criação"):
        list(hash_join(eng, r, s))
    assert not eng.catalog.live


@pytest.mark.parametrize("memory", [3, 64])
def test_throw_into_suspended_join(case, memory):
    eng, r, s = case([(i, i) for i in range(30)], [(i % 30, i) for i in range(120)], memory)
    query = hash_join(eng, r, s)
    next(query)
    with pytest.raises(RuntimeError, match="consumidor"):
        query.throw(RuntimeError("falha do consumidor"))
    assert not eng.catalog.live
    assert eng.bp.pinned() == eng.bp.reserved == 0


def test_empty_with_allocated_block(case):
    eng, r, s = case([], [(1, 10)])
    r.table.stats().n_blocks = 1
    r.table.stats().bfr = 0
    assert list(hash_join(eng, r, s, build="r")) == []


@pytest.mark.parametrize("bfr", [0.125, 0.5, 0.99, 1, 1.5])
def test_sparse_fractional_bfr(case, bfr):
    eng, r, s = case([(1, 100)], [(1, 200)], memory=3, bfr=bfr)
    assert list(hash_join(eng, r, s)) == [(1, 100, 1, 200)]


@pytest.mark.parametrize("bfr", [0, -1, float("inf"), float("nan")])
def test_invalid_bfr_in_nonempty_build(case, bfr):
    eng, r, s = case([(1, 100)], [(1, 200)])
    r.table.stats().bfr = bfr
    with pytest.raises(ValueError, match="bfr"):
        list(hash_join(eng, r, s, build="r"))


def test_recursion_changes_seed(case, monkeypatch):
    import cbd.exec.hash_join as module
    original = module.hash_value
    seeds = set()

    def recording(value, seed):
        seeds.add(seed)
        return original(value, seed)

    monkeypatch.setattr(module, "hash_value", recording)
    eng, r, s = case([(1, 100)], [(1, i) for i in range(20)], memory=3)
    list(hash_join(eng, r, s, build="s"))
    assert seeds == {100, 101, 102}


@pytest.mark.parametrize("memory,build", [(2, "auto"), (4, "bad")])
def test_invalid_plan(case, memory, build):
    eng, r, s = case([(1, 1)], [(1, 2)], memory=memory)
    with pytest.raises(ValueError):
        list(hash_join(eng, r, s, build=build))
    assert r.table.data.scans == s.table.data.scans == 0


def test_cost_in_memory_reads_no_records(case):
    eng, r, s = case([(1, 1)], [(1, i) for i in range(30)])
    cost = hash_join_cost(eng, r, s)
    assert cost.reads == 1 + 15
    assert cost.writes == 0
    assert r.table.data.scans == s.table.data.scans == eng.catalog.created == 0


@pytest.mark.parametrize("build", ["auto", "r", "s"])
@pytest.mark.parametrize("empty_r", [True, False])
@pytest.mark.parametrize("allocated", [0, 1])
def test_cost_and_operator_empty_input(case, build, empty_r, allocated):
    rows = [(i, i) for i in range(30)]
    rr, ss = ([], rows) if empty_r else (rows, [])
    eng, r, s = case(rr, ss)
    empty = r if empty_r else s
    empty.table.stats().n_blocks = allocated
    empty.table.stats().bfr = 0
    info = {}
    assert list(hash_join(eng, r, s, build=build, info=info)) == []
    assert info["variant"] == "empty"
    assert eng.bp.reserved == 0
    assert r.table.data.scans == s.table.data.scans == eng.catalog.created == 0
    cost = hash_join_cost(eng, r, s, build=build)
    assert (cost.reads, cost.writes, cost.seeks) == (0, 0, 0)


def test_cost_grace_one_round_formula(case):
    eng, r, s = case([(i, i) for i in range(12)], [(i, -i) for i in range(16)], memory=5)
    # b_r=6, b_s=8, M−2=3, n=ceil(1,2×6/3)=3. Uma rodada uniforme:
    # reads=2×14+2×3=34, writes=14+2×3=20, total=3×14+4×3=54.
    cost = hash_join_cost(eng, r, s)
    assert (cost.reads, cost.writes, cost.transfers) == (34, 20, 54)
    assert cost.seeks == 2 * 14 + 2 * 3
    assert "L=1, P=3" in cost.formula
    assert "não prevê skew/cache" in cost.formula
    assert eng.catalog.created == r.table.data.scans == s.table.data.scans == 0


def test_cost_multiple_rounds_and_forced_orientation(case):
    eng, r, s = case([(1, 100)], [(i, i) for i in range(160)], memory=4)
    assert hash_join_cost(eng, r, s, build="r").writes == 0
    cost = hash_join_cost(eng, r, s, build="s")
    # Construtor=80 blocos, fan-out=3: 80→27→9→3. L=3 e P=3+9+27=39.
    # Ainda há 3>2 blocos por folha: o modelo acrescenta uma sondagem por folha.
    assert "L=3, P=39" in cost.formula
    assert cost.writes == 3 * 81 + 2 * 39
    assert cost.reads == 4 * 81 + 2 * 39 + 27
    assert r.table.data.scans == s.table.data.scans == 0


@pytest.mark.parametrize("memory,build", [(2, "auto"), (4, "bad")])
def test_cost_same_validation_as_operator(case, memory, build):
    eng, r, s = case([(1, 1)], [(1, 2)], memory=memory)
    with pytest.raises(ValueError):
        hash_join_cost(eng, r, s, build)


def test_block_join_releases_previous_chunk_records():
    """Uma referência de laço não pode manter o lote antigo junto do próximo."""
    from cbd.exec.hash_join import _Source, _block_join
    live = set()

    class TrackedRecord(tuple):
        def __new__(cls, value):
            obj = super().__new__(cls, value)
            live.add(id(obj))
            return obj

        def __del__(self):
            live.discard(id(self))

    def build_rows():
        for i in range(6):
            yield TrackedRecord((i, i))

    observed = []

    def probe_rows():
        observed.append(len(live))
        yield (0, 100)

    eng = SimpleNamespace(M=4, bp=_Work(4))
    schema = Schema("r", (Field("key", FType.INT), Field("payload", FType.INT)))
    b = _Source(schema, 0, build_rows, 3, 1)
    p = _Source(schema, 0, probe_rows, 1, 1)
    assert list(_block_join(eng, b, p, True)) == [(0, 0, 0, 100)]
    # M−2=2 registros reservados; a entrada produz tuplas novas sem lista externa.
    assert observed == [2, 2, 2]
    assert not live
    assert eng.bp.reserved == eng.bp.pinned() == 0


def test_build_overflows_page_bytes_before_bfr_limit():
    from cbd.exec.hash_join import _Source, _Overflow, _build_probe

    schema = Schema("wide", (Field("key", FType.INT), Field("v", FType.VARCHAR, 1200)))
    rows = [(1, "a" * 1100), (2, "b" * 1100)]
    # O bfr supõe 10 registros, mas dois registros de 1100 bytes e slots
    # não cabem na única página de 2048 B reservada.
    build = _Source(schema, 0, lambda: iter(rows), 1, 10)
    probe = _Source(schema, 0, lambda: iter(rows), 1, 10)
    work = _Work(3)
    work.block_size = 2048
    eng = SimpleNamespace(M=3, bp=work)
    with pytest.raises(_Overflow):
        next(_build_probe(eng, build, probe, True))
    assert work.reserved == work.pinned() == 0


@pytest.mark.parametrize("different", ["buckets", "seed"])
@pytest.mark.parametrize("build", ["r", "s"])
def test_hj_incompatible_hash_falls_back_to_grace(case, different, build):
    keys = _bucket_keys(11, 17)
    rr, ss = [(key, i) for i, key in enumerate(keys)], [(key, -i) for i, key in enumerate(keys)]
    eng, r, s = case(rr, ss, memory=4)
    rh = _as_hash(r, eng, buckets=11, seed=17)
    sh = _as_hash(s, eng, buckets=13 if different == "buckets" else 11,
                  seed=19 if different == "seed" else 17)
    info = {}
    assert Counter(hash_join(eng, r, s, build=build, info=info)) == _expected(rr, ss)
    assert info["variant"] == "grace"
    assert info["index_used"] is False
    assert not rh.bucket_scans and not sh.bucket_scans
    assert r.table.data.scans > 0 and s.table.data.scans > 0
    assert hash_join_cost(eng, r, s, build=build).writes > 0


@pytest.mark.parametrize("s_buckets,s_seed", [(11, 17), (13, 17), (11, 19)])
def test_in_memory_precedes_primary_hash_reuse(case, s_buckets, s_seed):
    keys = _bucket_keys(11, 17)
    rr, ss = [(key, i) for i, key in enumerate(keys)], [(key, -i) for i, key in enumerate(keys)]
    eng, r, s = case(rr, ss, memory=64)
    rh = _as_hash(r, eng)
    sh = _as_hash(s, eng, buckets=s_buckets, seed=s_seed)
    info = {}
    assert Counter(hash_join(eng, r, s, info=info)) == _expected(rr, ss)
    assert info["variant"] == "in-memory"
    assert info["index_used"] is False
    assert not rh.bucket_scans and not sh.bucket_scans
    assert eng.catalog.created == 0
    assert hash_join_cost(eng, r, s).writes == 0
