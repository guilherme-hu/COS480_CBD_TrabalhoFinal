"""Estresse local e físico das primitivas P4, com SQLite como oráculo."""

from collections import Counter
from contextlib import closing, contextmanager
from random import Random
from time import perf_counter
from types import SimpleNamespace

import pytest

from ai.P4.raw import nested_loop_v1 as historical_join
from ai.P4.raw.nested_loop_unbounded_baseline import naive_bnl
from bench.schema import CURSO, DISCIPLINA
from bench.sqlite_ref import SQLiteReference, from_sql, output_signature, query_sql, quote, record_bytes
from cbd.contracts import BufferConfig, JoinSide, Org, OrgSpec, PageType, Path, StorageConfig, TableStats
from cbd.exec import external_sort as sort_module, nested_loop as join_module
from cbd.exec.access import read_all


pytestmark = pytest.mark.stress
M = 3
BLOCK_SIZE = 2048
PAYLOAD = BLOCK_SIZE - 28
BLOCKS = 50 * M
SEED = 48064
QUERY = dict(kind="join", r_table="curso", r_column="codigo",
             s_table="disciplina", s_column="curso")


def charge(schema, record):
    return record_bytes(schema, record) + 4


@pytest.fixture
def workload():
    rng = Random(SEED)
    hot = rng.randrange(1, BLOCKS * 3 + 1)
    courses = [(n, "c" * 60, "e" * 40, 360, n, f"c{n}@ufrj.br", "a" * 80, ("1" * 15,) * 16)
               for n in range(1, BLOCKS * 3 + 1)]
    details = [(n, hot, "d" * 60, "e" * 200, 60, ("00000001",) * 16)
               for n in range(1, BLOCKS * 4 + 1)]
    rng.shuffle(courses)
    rng.shuffle(details)

    # Três cursos ou quatro disciplinas por página no layout contratado.
    for schema, rows, per_block in ((CURSO, courses, 3), (DISCIPLINA, details, 4)):
        assert len(rows) == BLOCKS * per_block
        assert {PAYLOAD // charge(schema, row) for row in rows} == {per_block}

    with SQLiteReference(":memory:") as reference:
        reference.create_schema()
        reference.load("curso", courses)
        reference.load("disciplina", details)
        yield courses, details, reference


class WorkBudget:
    block_size = BLOCK_SIZE

    def __init__(self, capacity=M):
        self.capacity = capacity
        self.reserved = self.work_pins = self.data_pins = self.peak = 0

    def check(self):
        work = self.reserved + self.work_pins
        assert work <= self.capacity
        self.peak = max(self.peak, work)

    @contextmanager
    def reserve_work(self, pages):
        self.reserved += pages
        try:
            self.check()
            yield pages
        finally:
            self.reserved -= pages

    def pinned(self):
        return self.work_pins + self.data_pins


class TempFile:
    def __init__(self, catalog):
        self.catalog = catalog
        self.rows = []  # Arquivo do dublê, fora da memória de registros da P4.
        self.writer = False
        self.readers = 0

    def append(self, row):
        if not self.writer:
            self.catalog.bp.work_pins += 1
            self.writer = True
            self.catalog.bp.check()
        self.rows.append(row)
        self.catalog.appended += 1

    def finish(self):
        if self.writer:
            self.catalog.bp.work_pins -= 1
            self.writer = False

    def scan(self):
        assert not self.writer
        self.catalog.bp.work_pins += 1
        self.readers += 1
        try:
            self.catalog.bp.check()
            yield from self.rows
        finally:
            self.readers -= 1
            self.catalog.bp.work_pins -= 1

    def drop(self):
        assert not self.writer and self.readers == 0
        self.catalog.active.discard(self)
        self.rows.clear()


class Catalog:
    def __init__(self, bp):
        self.bp = bp
        self.active = set()
        self.appended = 0

    def temp(self, schema):
        run = TempFile(self)
        self.active.add(run)
        return run


def _scan(bp, rows):
    bp.data_pins += 1
    try:
        yield from enumerate(rows)
    finally:
        bp.data_pins -= 1


def _side(bp, schema, rows, column, blocks=BLOCKS):
    sizes = [record_bytes(schema, row) for row in rows]
    distinct = len({row[schema.idx(column)] for row in rows})
    stats = TableStats(len(rows), blocks, len(rows) / blocks, sum(sizes) / len(rows),
                       {column: distinct}, {}, {})
    data = SimpleNamespace(spec=OrgSpec(Org.HEAP), scans=0)

    def scan():
        data.scans += 1
        return _scan(bp, rows)

    data.scan = scan
    table = SimpleNamespace(name=schema.name, schema=schema, data=data, indexes={}, stats=lambda: stats)
    return JoinSide(table, column, Path.SCAN)


def _sqlite_join(reference):
    sql, params = query_sql(QUERY)
    width = len(CURSO.fields)
    with closing(reference.conn.execute(sql, params)) as rows:
        return Counter(from_sql(CURSO, row[:width]) + from_sql(DISCIPLINA, row[width:]) for row in rows)


def _sqlite_ordered_details(reference, column="curso"):
    # rowid representa a ordem de entrada para verificar a estabilidade do sort P4.
    sql = f"SELECT * FROM disciplina ORDER BY {quote(column)}, rowid"
    with closing(reference.conn.execute(sql)) as rows:
        return [from_sql(DISCIPLINA, row) for row in rows]


def _verify_join(eng, r, s, reference):
    expected = _sqlite_join(reference)
    signature = reference.result(QUERY)
    naive_info, historical_info, info, durations = {}, {}, {}, {}
    for name, rows in (("ingênuo", naive_bnl(r, s, naive_info)),
                       ("histórico", historical_join.nl_join(eng, r, s, outer="r", info=historical_info)),
                       ("revisado", join_module.nl_join(eng, r, s, outer="r", info=info))):
        started = perf_counter()
        actual = list(rows)  # O oráculo pode materializar a saída depois do operador.
        durations[name] = perf_counter() - started
        assert Counter(actual) == expected
        assert output_signature(QUERY, actual) == signature
        assert eng.bp.pinned() == 0

    assert signature[0] == 600  # Todas as FKs apontam para o mesmo mestre.
    assert naive_info == {"peak_outer_records": 450, "passes": 1}
    assert historical_info["passes"] == info["passes"] == 150
    assert s.table.data.scans == 1 + 150 + 150
    return signature, durations


def _observe_course_batches(module, monkeypatch):
    monkeypatch.setattr(module, "_layout", lambda schema, size: (PAYLOAD, lambda row: charge(schema, row)))
    scan_inner = module._scan_inner
    peak = [0, 0]

    def observe(plan, key, groups, info):
        peak[0] = max(peak[0], sum(len(rows) for rows in groups.values()))
        peak[1] = max(peak[1], sum(charge(CURSO, row) for rows in groups.values() for row in rows))
        yield from scan_inner(plan, key, groups, info)

    monkeypatch.setattr(module, "_scan_inner", observe)
    return peak


def test_p4_bnl_stress_sqlite_and_work_limit(workload, monkeypatch):
    courses, details, reference = workload
    bp = WorkBudget()
    eng = SimpleNamespace(M=M, bp=bp)
    r = _side(bp, CURSO, courses, "codigo")
    s = _side(bp, DISCIPLINA, details, "curso")
    historical_peak = _observe_course_batches(historical_join, monkeypatch)
    revised_peak = _observe_course_batches(join_module, monkeypatch)
    signature, durations = _verify_join(eng, r, s, reference)
    assert historical_peak == revised_peak
    peak_records, peak_bytes = revised_peak
    naive_bytes = sum(charge(CURSO, row) for row in courses)
    assert (peak_records, peak_bytes) == (3, 3 * charge(CURSO, courses[0]))
    assert bp.reserved == 0 and bp.peak == M - 2
    assert peak_bytes <= PAYLOAD < naive_bytes
    estimate = join_module.nl_join_cost(eng, r, s, outer="r")
    assert estimate.reads == BLOCKS + BLOCKS * BLOCKS
    print(f"BNL local: SQLite={signature}; pico={len(courses)} linhas/{naive_bytes} bytes "
          f"-> {peak_records} linhas/{peak_bytes} bytes; passadas=1/150/150; "
          f"segundos no dublê={durations}; estimativa={estimate.reads}")


def test_p4_bnl_stress_with_skew_on_outer_side(workload, monkeypatch):
    courses, details, reference = workload
    bp = WorkBudget()
    eng = SimpleNamespace(M=M, bp=bp)
    r = _side(bp, CURSO, courses, "codigo")
    s = _side(bp, DISCIPLINA, details, "curso")
    monkeypatch.setattr(join_module, "_layout", lambda schema, size: (PAYLOAD, lambda row: charge(schema, row)))
    scan_inner = join_module._scan_inner
    peak = 0

    def observe(plan, key, groups, info):
        nonlocal peak
        assert len(groups) == 1  # Toda chave externa é a mesma FK.
        peak = max(peak, len(next(iter(groups.values()))))
        yield from scan_inner(plan, key, groups, info)

    monkeypatch.setattr(join_module, "_scan_inner", observe)
    info = {}
    rows = list(join_module.nl_join(eng, r, s, outer="s", info=info))
    assert Counter(rows) == _sqlite_join(reference)
    assert output_signature(QUERY, rows) == reference.result(QUERY)
    assert info["outer"] == "s" and info["passes"] == 150
    assert peak == 4 and peak * charge(DISCIPLINA, details[0]) <= PAYLOAD
    assert bp.pinned() == bp.reserved == 0
    print(f"BNL detalhe externo: linhas={len(rows)}; pico={peak}; passadas={info['passes']}")


def test_p4_historical_bnl_extra_pass(workload, monkeypatch):
    _, details, _ = workload
    # 5 páginas de 9 registros a 224 bytes e 57 de 8 a 250 bytes.
    end_lengths = [39] * 45 + [65] * 456
    courses = [(n, "c" * 60, "e" * 40, 360, n, f"c{n}@ufrj.br", "a" * length, None)
               for n, length in enumerate(end_lengths, 1)]
    assert {charge(CURSO, row) for row in courses[:45]} == {224}
    assert {charge(CURSO, row) for row in courses[45:]} == {250}
    bp = WorkBudget(capacity=64)
    eng = SimpleNamespace(M=64, bp=bp)
    r = _side(bp, CURSO, courses, "codigo", blocks=62)
    s = _side(bp, DISCIPLINA, details[:4], "curso", blocks=1)
    for module in (historical_join, join_module):
        monkeypatch.setattr(module, "_layout", lambda schema, size: (PAYLOAD, lambda row: charge(schema, row)))
    with SQLiteReference(":memory:") as reference:
        reference.create_schema()
        reference.load("curso", courses)
        reference.load("disciplina", details[:4])
        expected = _sqlite_join(reference)
        for module, passes in ((historical_join, 2), (join_module, 1)):
            info = {}
            scans_before = s.table.data.scans
            result = list(module.nl_join(eng, r, s, outer="r", info=info))
            assert Counter(result) == expected
            assert len(result) == 4
            assert info["passes"] == passes
            assert s.table.data.scans - scans_before == passes
            assert module.nl_join_cost(eng, r, s, outer="r").reads == 63
            assert bp.pinned() == bp.reserved == 0
    print("BNL historico -> revisado: 2 -> 1 passadas no caso de 501 registros em 62 blocos")


@pytest.mark.parametrize("column", ["curso", "codigo"])
def test_p4_sort_stress_sqlite_and_work_limit(workload, monkeypatch, column):
    _, details, reference = workload
    bp = WorkBudget()
    eng = SimpleNamespace(M=M, bp=bp, catalog=Catalog(bp))
    monkeypatch.setattr(sort_module, "_layout", lambda schema, size: (PAYLOAD, lambda row: charge(schema, row)))

    def source():
        with closing(read_all(_side(bp, DISCIPLINA, details, column))) as rows:
            for n, row in enumerate(rows, 1):
                # Duas páginas de quatro registros e uma única antecipação.
                assert n - eng.catalog.appended <= 4 * (M - 1) + 1
                yield row

    info = {}
    expected = _sqlite_ordered_details(reference, column)
    if column == "codigo":
        assert details != expected
    with closing(sort_module.external_sort(eng, source(), DISCIPLINA, column, info=info)) as output:
        first = next(output)
        assert bp.work_pins == M  # A saída começa com os três runs finais ativos.
        assert [first, *output] == expected
    assert info == {"in_memory": False, "runs": 75, "passes": 6}
    assert bp.pinned() == bp.reserved == 0
    assert bp.peak == M and not eng.catalog.active
    estimate = sort_module.external_sort_cost(eng, BLOCKS)
    assert (estimate.reads, estimate.writes) == (1050, 900)
    print(f"Sort local por {column}: linhas={len(details)}; {info}; pico WORK={bp.peak}; "
          f"estimativa={estimate.reads}/{estimate.writes}")


@pytest.fixture
def real_engine():
    pytest.importorskip("cbd.storage.buffer_pool")
    pytest.importorskip("cbd.files.catalog")
    pytest.importorskip("cbd.records.codec")
    from cbd.engine import Engine

    return Engine


@pytest.fixture
def physical(real_engine, tmp_path, workload):
    courses, details, reference = workload
    areas = dict(BufferConfig().areas)
    areas.update({PageType.DATA: 2, PageType.WORK: M})
    with real_engine(StorageConfig(str(tmp_path), BLOCK_SIZE), BufferConfig(areas=areas)) as eng:
        for schema, rows in ((CURSO, courses), (DISCIPLINA, details)):
            eng.catalog.create_table(schema, OrgSpec(Org.HEAP))
            eng.catalog.load_table(schema.name, rows)
            assert eng.catalog.table(schema.name).stats().n_blocks == BLOCKS
        yield eng, reference
        assert eng.bp.pinned() == 0


def test_p4_bnl_stress_physical(physical):
    eng, reference = physical
    r = JoinSide(eng.catalog.table("curso"), "codigo", Path.SCAN)
    s = JoinSide(eng.catalog.table("disciplina"), "curso", Path.SCAN)
    expected = _sqlite_join(reference)
    expected_signature = reference.result(QUERY)
    for name, execute, expected_reads in (
        ("ingênuo", lambda: naive_bnl(r, s, {}), 2 * BLOCKS),
        ("histórico", lambda: historical_join.nl_join(eng, r, s, outer="r"), BLOCKS + BLOCKS * BLOCKS),
        ("revisado", lambda: join_module.nl_join(eng, r, s, outer="r"), BLOCKS + BLOCKS * BLOCKS),
    ):
        eng.cold()
        started = perf_counter()
        actual = list(execute())
        elapsed = perf_counter() - started
        metrics = eng.snapshot()
        assert eng.bp.pinned() == 0
        assert Counter(actual) == expected
        assert output_signature(QUERY, actual) == expected_signature
        assert metrics["io.reads"] == expected_reads
        assert metrics["io.writes"] == 0
        print(f"BNL físico {name}: {elapsed:.6f} s; "
              f"{metrics['io.reads']} leituras e {metrics['io.writes']} gravações")


def test_p4_sort_stress_physical(physical):
    eng, reference = physical
    side = JoinSide(eng.catalog.table("disciplina"), "curso", Path.SCAN)
    eng.cold()
    info = {}
    rows = list(sort_module.external_sort(eng, read_all(side), DISCIPLINA, "curso", info=info))
    metrics = eng.snapshot()
    assert rows == _sqlite_ordered_details(reference)
    assert info == {"in_memory": False, "runs": 75, "passes": 6}
    assert eng.bp.pinned() == 0
    estimate = sort_module.external_sort_cost(eng, BLOCKS)
    # Ao menos 147 dos 150 blocos dos runs iniciais precisam sair de WORK
    # e ser relidos, além da varredura da entrada. Os demais podem ter hits.
    assert 2 * BLOCKS - M <= metrics["io.reads"] <= estimate.reads
    assert BLOCKS - M <= metrics["io.writes"] <= estimate.writes
    print(f"Sort físico: blocos lidos {metrics['io.reads']}/{estimate.reads}, "
          f"gravados {metrics['io.writes']}/{estimate.writes} (medido/estimado)")
