import sys
from contextlib import closing, contextmanager
from types import SimpleNamespace

import pytest

from cbd.contracts import (
    BufferConfig, Field, FType, JoinSide, Org, OrgSpec, PageType, Path,
    RecordTooLarge, Schema, StorageConfig,
)
from cbd.exec import external_sort as sort_module
from cbd.exec.access import read_all
from cbd.exec.external_sort import external_sort, external_sort_cost


SCHEMA = Schema("demo", (Field("key", FType.INT, nullable=True), Field("id", FType.INT),
                         Field("charge", FType.INT)))


def order(row):
    return row[0] is None, row[0]


class WorkBudget:
    block_size = 10

    def __init__(self, capacity):
        self.capacity = capacity
        self.reserved = 0
        self.pins = 0
        self.peak = 0

    def check(self):
        used = self.pins + self.reserved
        assert used <= self.capacity
        self.peak = max(self.peak, used)

    @contextmanager
    def reserve_work(self, pages):
        self.reserved += pages
        try:
            self.check()
            yield pages
        finally:
            self.reserved -= pages

    def pin(self):
        self.pins += 1
        self.check()


class Temp:
    def __init__(self, catalog, name):
        self.catalog, self.name = catalog, name
        self.rows = []  # Representa o arquivo, não o lote mantido pelo operador.
        self.scans = []  # Mantém referência externa para testar fechamento explícito.
        self.readers = 0
        self.writer = False
        self.reserved_at_write = None
        self.readers_at_write = None

    def append(self, record):
        if not self.writer:
            self.readers_at_write = self.catalog.bp.pins
            self.catalog.bp.pin()
            self.writer = True
            self.reserved_at_write = self.catalog.bp.reserved
        if self.name == self.catalog.fail_append_in:
            raise RuntimeError("erro de gravação")
        self.rows.append(record)

    def finish(self):
        if self.writer:
            self.catalog.bp.pins -= 1
            self.writer = False

    def scan(self):
        assert not self.writer

        def source():
            self.catalog.bp.pin()
            self.readers += 1
            try:
                if self.name == self.catalog.fail_scan_in:
                    raise RuntimeError("erro de leitura")
                yield from self.rows
            finally:
                self.readers -= 1
                self.catalog.bp.pins -= 1

        cursor = source()
        self.scans.append(cursor)
        return cursor

    def drop(self):
        assert not self.writer and self.readers == 0
        self.catalog.active.pop(self.name, None)


class Catalog:
    def __init__(self, bp):
        self.bp = bp
        self.created = []
        self.active = {}
        self.fail_append_in = None
        self.fail_scan_in = None

    def temp(self, schema):
        number = len(self.created) + 1
        run = Temp(self, number)
        self.created.append(run)
        self.active[number] = run
        return run


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(sort_module, "_layout", lambda schema, size: (size, lambda row: row[2]))

    def create(mem=3):
        bp = WorkBudget(mem)
        return SimpleNamespace(M=mem, bp=bp, catalog=Catalog(bp))

    return create


def clean(eng):
    assert eng.bp.reserved == eng.bp.pins == 0
    assert eng.catalog.active == {}


def test_external_sort_small_M(engine):
    eng = engine()
    rows = [(i % 4, i, 10) for i in range(50, 0, -1)]
    info = {}
    out = list(external_sort(eng, iter(rows), SCHEMA, "key", 50, info=info))
    assert out == sorted(rows, key=order)
    # Dois blocos por run, depois 25 -> 13 -> 7 -> 4 -> 2 -> saída.
    assert info == {"in_memory": False, "runs": 25, "passes": 5}
    cost = external_sort_cost(eng, 50)
    assert (cost.reads, cost.writes) == (50 * 6, 50 * 5)
    assert cost.seeks == 2 * 25 + 50 * 9
    assert eng.bp.peak == 3
    assert all(run.reserved_at_write == 2 for run in eng.catalog.created[:25])
    assert all(run.reserved_at_write == 0 for run in eng.catalog.created[25:])
    assert all(1 <= run.readers_at_write <= 2 for run in eng.catalog.created[25:])
    assert eng.catalog.created[25].readers_at_write == 2
    clean(eng)


def test_external_sort_final_pass_accepts_all_m_inputs(engine):
    eng = engine()
    rows = [(key, key, 10) for key in range(6, 0, -1)]
    info = {}
    assert list(external_sort(eng, rows, SCHEMA, "key", info=info)) == sorted(rows)
    assert info == {"in_memory": False, "runs": 3, "passes": 1}
    assert len(eng.catalog.created) == 3  # Nenhuma saída temporária na fusão final.
    estimate = external_sort_cost(eng, 6)
    assert (estimate.reads, estimate.writes) == (12, 6)
    assert estimate.seeks == 2 * 3 + 6
    clean(eng)


@pytest.mark.parametrize("rows", [[], [(2, 1, 6), (1, 2, 4), (3, 3, 10)]])
def test_external_sort_in_memory(engine, rows):
    eng = engine()
    info = {}
    assert list(external_sort(eng, rows, SCHEMA, "key", n_blocks_hint=100, info=info)) == sorted(rows)
    assert info == {"in_memory": True, "runs": 0, "passes": 0}
    assert eng.catalog.created == []
    assert external_sort_cost(eng, 2).writes == 0
    clean(eng)


def test_external_sort_dups_nulls(engine):
    eng = engine()
    rows = [(None, 0, 7), (2, 1, 10), (1, 2, 5), (2, 3, 6), (None, 4, 6), (1, 5, 4)]
    assert list(external_sort(eng, rows, SCHEMA, "key")) == sorted(rows, key=order)
    clean(eng)


def test_external_sort_uses_requested_key_in_other_column(engine):
    eng = engine()
    rows = [(1, 6, 10), (2, 5, 10), (3, 4, 10), (4, 3, 10), (5, 2, 10), (6, 1, 10)]
    info = {}
    assert list(external_sort(eng, rows, SCHEMA, "id", info=info)) == rows[::-1]
    assert info["passes"] == 1
    clean(eng)


def test_external_sort_does_not_split_rows_to_fill_batch(engine):
    eng = engine()
    rows = [(3, 0, 6), (2, 1, 5), (1, 2, 6)]
    info = {}
    assert list(external_sort(eng, rows, SCHEMA, "key", info=info)) == sorted(rows)
    assert info["runs"] == 2  # Soma 17, mas cada linha exige uma página separada.
    clean(eng)


def test_external_sort_charges_page_header_and_record_slot(monkeypatch):
    codec = SimpleNamespace(encode=lambda record: bytes(record[2]))
    record_codec = SimpleNamespace(RecordCodec=lambda schema: codec)
    page = SimpleNamespace(HEADER=10, max_record=lambda block_size: block_size - 14)
    monkeypatch.setitem(sys.modules, "cbd.records.codec", record_codec)
    monkeypatch.setitem(sys.modules, "cbd.records.slotted_page", SimpleNamespace(SlottedPage=page))
    payload, charge = sort_module._layout(SCHEMA, 30)
    assert payload == 20
    assert charge((1, 1, 7)) == 11
    assert 2 * charge((1, 1, 7)) > payload


@pytest.mark.parametrize("count", [2, 18])
def test_external_sort_closes_after_first_result(engine, count):
    eng = engine()
    rows = [(i, i, 10) for i in range(count, 0, -1)]
    with closing(external_sort(eng, rows, SCHEMA, "key")) as out:
        assert next(out) == (1, 1, 10)
        if count == 2:
            assert (eng.bp.reserved, eng.bp.pins) == (2, 0)
        else:
            assert (eng.bp.reserved, eng.bp.pins) == (0, 3)
    clean(eng)


def test_external_sort_two_interleaved_sorts_share_the_budget(engine):
    eng = engine(6)
    left = [(i, i, 10) for i in range(6, 0, -1)]
    right = [(i, i, 10) for i in range(16, 10, -1)]
    with (closing(external_sort(eng, left, SCHEMA, "key", mem=3)) as lhs,
          closing(external_sort(eng, right, SCHEMA, "key", mem=3)) as rhs):
        assert next(lhs) == left[-1]
        assert next(rhs) == right[-1]
        assert eng.bp.pins == 6
        assert next(lhs) == left[-2]
    clean(eng)


def test_external_sort_budgets_separately_from_a_work_source(engine):
    eng = engine(4)
    source = eng.catalog.temp(SCHEMA)
    rows = [(i, i, 10) for i in range(6, 0, -1)]
    for row in rows:
        source.append(row)
    source.finish()
    assert list(external_sort(eng, source.scan(), SCHEMA, "key", mem=3)) == sorted(rows)
    assert eng.bp.peak == 4
    assert eng.catalog.active == {source.name: source}
    source.drop()  # O chamador continua dono do temporário de entrada.
    clean(eng)


@pytest.mark.parametrize("error", ["source", "large_record", "write", "read"])
def test_external_sort_releases_resources_on_error(engine, error):
    eng = engine()
    source_closed = False

    def source():
        nonlocal source_closed
        try:
            for i in range(10):
                if i == 7 and error == "source":
                    raise RuntimeError("erro na entrada")
                yield (i, i, 11 if i == 7 and error == "large_record" else 10)
        finally:
            source_closed = True

    if error == "write":
        eng.catalog.fail_append_in = 6  # Primeira fusão intermediária, com leitores ativos.
    if error == "read":
        eng.catalog.fail_scan_in = 2
    rows = source()
    expected = RecordTooLarge if error == "large_record" else RuntimeError
    with pytest.raises(expected):
        list(external_sort(eng, rows, SCHEMA, "key"))
    assert source_closed
    clean(eng)


@pytest.mark.parametrize("mem", [0, 2, 4])
def test_external_sort_rejects_budget_outside_work(engine, mem):
    eng = engine()
    with pytest.raises(ValueError, match="mem"):
        list(external_sort(eng, [], SCHEMA, "key", mem=mem))
    with pytest.raises(ValueError, match="mem"):
        external_sort_cost(eng, 0, mem=mem)


@pytest.mark.parametrize("key,mem,error", [("key", 2, ValueError), ("wrong", 3, KeyError)])
def test_external_sort_validation_closes_started_input(engine, key, mem, error):
    eng = engine()
    source_closed = False

    def source():
        nonlocal source_closed
        try:
            yield (1, 1, 10)
            yield (2, 2, 10)
        finally:
            source_closed = True

    rows = source()
    next(rows)  # Simula um cursor suspenso com página fixada.
    with pytest.raises(error):
        next(external_sort(eng, rows, SCHEMA, key, mem=mem))
    assert source_closed
    clean(eng)


@pytest.mark.parametrize("n,expected_runs,expected_passes", [(6, 3, 1), (50, 25, 5)])
def test_external_sort_with_real_storage(tmp_path, n, expected_runs, expected_passes):
    pytest.importorskip("cbd.storage.buffer_pool")
    pytest.importorskip("cbd.files.catalog")
    pytest.importorskip("cbd.records.codec")
    from cbd.engine import Engine

    schema = Schema("wide", (Field("key", FType.INT), Field("text", FType.CHAR, 1200)))
    rows = [(i % 4, str(i)) for i in range(n, 0, -1)]
    areas = dict(BufferConfig().areas)
    areas[PageType.WORK] = 3
    with Engine(StorageConfig(str(tmp_path), 2048), BufferConfig(areas=areas)) as eng:
        eng.catalog.create_table(schema, OrgSpec(Org.HEAP))
        eng.catalog.load_table(schema.name, rows)
        side = JoinSide(eng.catalog.table(schema.name), "key", Path.SCAN)
        eng.cold()
        info = {}
        out = list(external_sort(eng, read_all(side), schema, "key", info=info))
        assert out == sorted(rows, key=order)
        # 1.209 bytes por registro antes do slot. Cada página de 2 KiB
        # comporta um registro, tanto no heap quanto nos temporários.
        assert info == {"in_memory": False, "runs": expected_runs, "passes": expected_passes}
        measured = eng.snapshot()
        estimate = external_sort_cost(eng, n)
        if n == 6:
            # A geração reserva 2 frames e usa só 1 para a escrita. A fusão
            # abre 3 primeiros blocos e expulsa o último bloco sujo da geração.
            assert measured["io.reads"] == estimate.reads == 12
            assert measured["io.writes"] == estimate.writes == 6
        else:
            assert 50 < measured["io.reads"] <= estimate.reads
            assert 0 < measured["io.writes"] <= estimate.writes
        assert eng.bp.pinned() == 0
