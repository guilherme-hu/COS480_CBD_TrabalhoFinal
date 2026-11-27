from collections import Counter
from contextlib import closing, contextmanager
from types import SimpleNamespace
from weakref import ref

import pytest

from cbd.contracts import (
    BufferConfig, BufferFullError, Field, FType, IndexKind, IndexSpec, JoinSide, Org, OrgSpec, PageType,
    Path, Policy, RecordTooLarge, Schema, StorageConfig, TableStats,
)
from cbd.exec import nested_loop
from cbd.exec.nested_loop import _plan, nl_join, nl_join_cost


class Data:
    def __init__(self, rows, org):
        self.rows = rows
        self.spec = OrgSpec(org, None if org is Org.HEAP else "key")
        self.active = 0
        self.cursor = None
        self.scans = 0
        self.probes = []

    def _cursor(self, rows):
        def stream():
            self.active += 1
            try:
                yield from rows
            finally:
                self.active -= 1

        # Mantém o cursor vivo para verificar close explícito.
        self.cursor = stream()
        return self.cursor

    def scan(self):
        self.scans += 1
        return self._cursor(enumerate(self.rows))

    def search(self, value):
        self.probes.append(value)
        return self._cursor((i, row) for i, row in enumerate(self.rows) if row[0] == value)

    def stats(self):
        return dict(levels=2, buckets=4, overflow_blocks=0)

    def fetch(self, ptr):
        return self.rows[ptr]


class Index:
    def __init__(self, table, data):
        self.spec = IndexSpec(table, "key", IndexKind.BTREE, False)
        self.data = data

    def search(self, value):
        return self.data._cursor(i for i, row in enumerate(self.data.rows) if row[0] == value)

    def stats(self):
        return dict(levels=2, overflow_blocks=0)


class FakeBuffer:
    block_size = 10

    def __init__(self):
        self.reserved = 0

    @contextmanager
    def reserve_work(self, pages):
        assert self.reserved == 0
        self.reserved = pages
        try:
            yield pages
        finally:
            self.reserved = 0


@pytest.fixture
def small_work(monkeypatch):
    # Este dublê usa o segundo campo como a carga codificada, incluindo o slot.
    monkeypatch.setattr(nested_loop, "_layout", lambda schema, size: (size, lambda row: row[1]))
    return SimpleNamespace(M=3, bp=FakeBuffer())


def make_side(name, rows, blocks, path, *, unique=False):
    schema = Schema(name, (Field("key", FType.INT, nullable=True, unique=unique), Field("id", FType.INT)))
    org = Org.BTREE if path is Path.PRIMARY else Org.HEAP
    keys = {row[0] for row in rows if row[0] is not None}
    stats = TableStats(len(rows), blocks, len(rows) / blocks if blocks else 0,
                       18, {"key": len(keys)}, {"key": min(keys, default=None)}, {"key": max(keys, default=None)})
    table = SimpleNamespace(name=name, schema=schema, data=Data(rows, org), indexes={}, stats=lambda: stats)
    if path is Path.SECONDARY:
        table.indexes["key"] = Index(name, table.data)
    return JoinSide(table, "key", path)


def reference(r, s):
    return Counter(rr + ss for rr in r for ss in s if rr[0] is not None and rr[0] == ss[0])


@pytest.mark.parametrize(
    "r_path,s_path,outer,expected,indexed",
    [(Path.SCAN, Path.SCAN, "auto", "s", False),
     (Path.SCAN, Path.PRIMARY, "auto", "r", True),
     (Path.PRIMARY, Path.SCAN, "auto", "s", True),
     (Path.PRIMARY, Path.PRIMARY, "auto", "r", True),
     (Path.PRIMARY, Path.SCAN, "r", "r", False),
     (Path.SCAN, Path.PRIMARY, "s", "s", False)],
)
def test_outer_choice_and_cost_use_the_same_plan(r_path, s_path, outer, expected, indexed):
    eng = SimpleNamespace(M=4)
    r = make_side("r", ((1, 11), (2, 12)), 3, r_path, unique=True)
    s = make_side("s", ((1, 20), (2, 21), (3, 22)), 2, s_path, unique=True)
    plan = _plan(eng, r, s, outer)
    assert plan.outer == expected
    assert plan.indexed is indexed
    cost = nl_join_cost(eng, r, s, outer)
    assert cost.formula.startswith(f"{'INL' if indexed else 'BNL'} ({expected} externo)")
    if expected == "r":
        assert cost.reads == 7  # Duas descidas de dois blocos ou duas passadas de dois blocos.
    else:
        assert cost.reads == (8 if indexed else 5)
    assert r.table.data.scans == s.table.data.scans == 0


@pytest.mark.parametrize("outer", ["r", "s"])
def test_indexed_preserves_multiplicity_and_orientation(outer):
    r_rows = ((1, 10), (None, 11), (2, 12), (1, 13))
    s_rows = ((1, 20), (None, 21), (1, 22), (3, 23))
    r = make_side("r", r_rows, 2, Path.PRIMARY)
    s = make_side("s", s_rows, 2, Path.PRIMARY)
    eng = SimpleNamespace(M=3)
    plan = _plan(eng, r, s, outer)
    info = {}
    assert Counter(nl_join(eng, r, s, outer, info)) == reference(r_rows, s_rows)
    assert plan.left.table.data.scans == 1
    assert plan.right.table.data.probes == ([1, 2, 1] if outer == "r" else [1, 1, 3])
    assert info == {"variant": "indexed", "outer": outer, "outer_blocks": 2, "passes": 3}
    assert r.table.data.active == s.table.data.active == 0


def test_indexed_closes_both_cursors_after_one_match():
    r = make_side("r", ((1, 10), (1, 11)), 1, Path.SCAN)
    s = make_side("s", ((1, 20), (1, 21)), 1, Path.PRIMARY)
    with closing(nl_join(SimpleNamespace(M=3), r, s)) as rows:
        assert next(rows) == (1, 10, 1, 20)
        assert r.table.data.active == s.table.data.active == 1
    assert r.table.data.active == s.table.data.active == 0


@pytest.mark.parametrize("r_path,s_path", [(Path.PRIMARY, Path.SECONDARY),
                                          (Path.SECONDARY, Path.SECONDARY)])
def test_indexed_supports_secondary_on_either_side(r_path, s_path):
    r_rows = ((1, 10), (2, 11), (None, 12))
    s_rows = ((1, 20), (1, 21), (3, 22), (None, 23))
    r = make_side("r", r_rows, 2, r_path)
    s = make_side("s", s_rows, 2, s_path)
    eng = SimpleNamespace(M=3)
    for outer in ("auto", "r", "s"):
        info = {}
        assert Counter(nl_join(eng, r, s, outer, info)) == reference(r_rows, s_rows)
        assert info["variant"] == "indexed"
        assert info["outer"] == ("r" if outer == "auto" else outer)
        assert r.table.data.active == s.table.data.active == 0


def test_block_plan_requires_minimum_work_and_outer_must_be_valid():
    r = make_side("r", ((1, 10),), 1, Path.SCAN)
    s = make_side("s", ((1, 20),), 1, Path.SCAN)
    with pytest.raises(BufferFullError, match="M >= 3"):
        nl_join_cost(SimpleNamespace(M=2), r, s)
    with pytest.raises(ValueError, match="outer"):
        nl_join_cost(SimpleNamespace(M=3), r, s, "left")


@pytest.mark.parametrize("outer", ["r", "s"])
def test_block_preserves_multiplicity_nulls_and_orientation(small_work, outer):
    r_rows = ((1, 7), (None, 2), (1, 2), (2, 4))
    s_rows = ((1, 8), (None, 3), (2, 4), (1, 2))
    r = make_side("r", r_rows, 1, Path.SCAN)
    s = make_side("s", s_rows, 1, Path.SCAN)
    info = {}
    assert Counter(nl_join(small_work, r, s, outer, info)) == reference(r_rows, s_rows)
    assert info["variant"] == "block"
    assert info["outer"] == outer
    assert info["passes"] == 2  # Cargas variáveis estouram a página mesmo com quatro linhas no limite médio.
    inside = s if outer == "r" else r
    assert inside.table.data.scans == 2
    assert small_work.bp.reserved == r.table.data.active == s.table.data.active == 0


def test_block_reserves_two_pages_without_splitting_records(small_work):
    small_work.M = 4
    r = make_side("r", ((1, 6), (1, 5), (1, 6)), 1, Path.SCAN)
    s = make_side("s", ((1, 1), (1, 2)), 1, Path.SCAN)
    info = {}
    assert len(list(nl_join(small_work, r, s, outer="r", info=info))) == 6
    # A soma é 17 bytes, mas nenhum par cabe na mesma página de 10 bytes.
    # Duas páginas comportam duas linhas. A terceira inicia outro lote.
    assert info["passes"] == 2


@pytest.mark.parametrize("outer", ["r", "s"])
def test_block_uses_the_key_position_of_each_side(small_work, outer):
    r = make_side("r", ((1, 4),), 1, Path.SCAN)
    s = make_side("s", ((2, 1),), 1, Path.SCAN)
    s.table.schema = Schema("s", (Field("id", FType.INT), Field("key", FType.INT)))
    assert list(nl_join(small_work, r, s, outer=outer)) == [(1, 4, 2, 1)]


def test_block_accepts_exact_fit(small_work):
    r = make_side("r", ((1, 5), (1, 5)), 1, Path.SCAN)
    s = make_side("s", ((1, 1),), 1, Path.SCAN)
    info = {}
    assert len(list(nl_join(small_work, r, s, outer="r", info=info))) == 2
    assert info["passes"] == 1


@pytest.mark.parametrize("outer_blocks", [0, 2])
def test_block_skips_the_inner_when_outer_is_empty(small_work, outer_blocks):
    r = make_side("r", (), outer_blocks, Path.SCAN)
    s = make_side("s", ((1, 1),), 1, Path.SCAN)
    info = {}
    assert list(nl_join(small_work, r, s, outer="r", info=info)) == []
    assert s.table.data.scans == info["passes"] == 0
    estimate = nl_join_cost(small_work, r, s, outer="r")
    assert estimate.reads == outer_blocks
    assert estimate.seeks == int(outer_blocks > 0)


def test_block_releases_the_previous_batch_before_another_scan(small_work, monkeypatch):
    class Payload:
        pass

    r = make_side("r", ((1, 6),) * 12, 12, Path.SCAN)
    s = make_side("s", ((1, 1),), 1, Path.SCAN)
    references = []

    def source():
        for i in range(12):
            payload = Payload()
            references.append(ref(payload))
            yield i, (1, payload)

    scan_inner = nested_loop._scan_inner

    def check_live_rows(*args):
        # A carga 6 deixa espaço para uma linha. A segunda pode estar no
        # cursor DATA enquanto o primeiro lote é processado.
        assert sum(pointer() is not None for pointer in references) <= 2
        yield from scan_inner(*args)

    monkeypatch.setattr(r.table.data, "scan", source)
    monkeypatch.setattr(nested_loop, "_layout", lambda schema, size: (size, lambda row: 6))
    monkeypatch.setattr(nested_loop, "_scan_inner", check_live_rows)
    with closing(nl_join(small_work, r, s, outer="r")) as joined:
        for _ in range(12):
            next(joined)
        with pytest.raises(StopIteration):
            next(joined)


def test_block_cost_accounts_for_rounded_record_limit(small_work):
    r = make_side("r", ((1, 4), (1, 4), (1, 4), (1, 4)), 3, Path.SCAN)
    s = make_side("s", ((1, 1), (1, 2)), 2, Path.SCAN)
    info = {}
    assert len(list(nl_join(small_work, r, s, outer="r", info=info))) == 8
    assert info["passes"] == 4
    # floor(1 × 4/3) = 1 linha no lote. Três blocos externos e quatro
    # varreduras de dois blocos internos no modelo sem reuso: 3 + 4 × 2.
    estimate = nl_join_cost(small_work, r, s, outer="r")
    assert estimate.reads == 11
    assert estimate.seeks == 8


def test_block_limit_does_not_round_down_twice(small_work):
    small_work.M = 64
    r = make_side("r", ((1, 1),) * 501, 62, Path.SCAN)
    s = make_side("s", ((1, 1),), 1, Path.SCAN)
    info = {}
    assert sum(1 for _ in nl_join(small_work, r, s, outer="r", info=info)) == 501
    assert info["passes"] == 1  # 62 × (501 / 62) = 501 exatamente.


def test_block_close_releases_input_and_memory(small_work):
    r = make_side("r", ((1, 7), (1, 7)), 2, Path.SCAN)
    s = make_side("s", ((1, 2), (1, 3)), 1, Path.SCAN)
    with closing(nl_join(small_work, r, s, outer="r")) as rows:
        assert next(rows) == (1, 7, 1, 2)
        assert small_work.bp.reserved == r.table.data.active == s.table.data.active == 1
    assert small_work.bp.reserved == r.table.data.active == s.table.data.active == 0


def test_block_rejects_a_record_larger_than_page(small_work):
    r = make_side("r", ((1, 11),), 1, Path.SCAN)
    s = make_side("s", ((1, 2),), 1, Path.SCAN)
    with pytest.raises(RecordTooLarge):
        list(nl_join(small_work, r, s))
    assert small_work.bp.reserved == r.table.data.active == s.table.data.active == 0


def _run_real_heap_join(root, data_pages, policy, inner_size=4):
    pytest.importorskip("cbd.storage.buffer_pool")
    pytest.importorskip("cbd.files.catalog")
    from cbd.engine import Engine
    areas = dict(BufferConfig().areas)
    areas.update({PageType.DATA: data_pages, PageType.WORK: 3})
    cfg = BufferConfig(areas=areas, policy=policy, toss_immediate=False, prefetch=0)
    fields = (Field("key", FType.INT), Field("payload", FType.CHAR, 1200))
    r_rows = ((1, "r1"), (2, "r2"), (3, "r3"))
    s_rows = tuple((i % 3 + 1, f"s{i}") for i in range(inner_size))
    with Engine(StorageConfig(str(root), 2048), cfg) as eng:
        for name, rows in (("ext", r_rows), ("inner", s_rows)):
            eng.catalog.create_table(Schema(name, fields), OrgSpec(Org.HEAP))
            eng.catalog.load_table(name, rows)
        r = JoinSide(eng.catalog.table("ext"), "key", Path.SCAN)
        s = JoinSide(eng.catalog.table("inner"), "key", Path.SCAN)
        estimated = nl_join_cost(eng, r, s, outer="r")
        eng.cold()
        info = {}
        assert Counter(nl_join(eng, r, s, outer="r", info=info)) == reference(r_rows, s_rows)
        reads = eng.snapshot()["io.reads"]
        assert info["passes"] == 3
        assert eng.bp.pinned() == 0
        return reads, estimated.reads


def test_bnl_exact_cost(tmp_path):
    # Cada registro codificado ocupa mais de metade da página. São três
    # blocos externos, quatro internos e um externo por lote: 3 + 3 × 4.
    measured, estimated = _run_real_heap_join(tmp_path, 2, Policy.LRU)
    assert measured == estimated == 15


def test_bnl_flooding(tmp_path):
    # Seis blocos internos para cinco frames DATA, com um frame externo
    # fixado em parte da execução. Sem reuso, a conta é 3 + 3 × 6.
    lru, estimate = _run_real_heap_join(tmp_path / "lru", 5, Policy.LRU, inner_size=6)
    mru, same_estimate = _run_real_heap_join(tmp_path / "mru", 5, Policy.MRU, inner_size=6)
    assert estimate == same_estimate == 21
    assert lru == estimate
    assert mru < lru


def test_inl_real_index_counts_and_agrees_with_block(tmp_path):
    pytest.importorskip("cbd.storage.buffer_pool")
    pytest.importorskip("cbd.files.catalog")
    pytest.importorskip("cbd.files.hash_index")
    from cbd.engine import Engine

    with Engine(StorageConfig(str(tmp_path), 2048), BufferConfig()) as eng:
        r_fields = (Field("key", FType.INT), Field("id", FType.INT))
        s_fields = (Field("key", FType.INT, unique=True), Field("id", FType.INT))
        for schema, rows in ((Schema("ext", r_fields), ((1, 10), (1, 11))),
                             (Schema("inner", s_fields), ((1, 20), (2, 21)))):
            eng.catalog.create_table(schema, OrgSpec(Org.HEAP))
            eng.catalog.load_table(schema.name, rows)
        eng.catalog.create_index(IndexSpec("inner", "key", IndexKind.HASH, True, {"buckets": 1}))
        r = JoinSide(eng.catalog.table("ext"), "key", Path.SCAN)
        s = JoinSide(eng.catalog.table("inner"), "key", Path.SECONDARY)
        expected = Counter({(1, 10, 1, 20): 1, (1, 11, 1, 20): 1})
        estimate = nl_join_cost(eng, r, s)

        eng.cold()
        assert Counter(nl_join(eng, r, s)) == expected
        # 1 bloco externo, 1 balde, 1 bloco de dados do interno. A segunda
        # busca reutiliza as duas páginas. O modelo sem reuso estima 1 + 2 × 2.
        assert estimate.reads == 5
        assert eng.snapshot()["io.reads"] == 3
        assert eng.bp.pinned() == 0

        eng.cold()
        with closing(nl_join(eng, r, s)) as rows:
            next(rows)
        assert eng.bp.pinned() == 0

        eng.cold()
        scan_side = JoinSide(s.table, s.column, Path.SCAN)
        assert Counter(nl_join(eng, r, scan_side, outer="r")) == expected
        assert eng.bp.pinned() == 0
