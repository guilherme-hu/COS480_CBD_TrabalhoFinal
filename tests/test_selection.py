from contextlib import closing, contextmanager
from types import SimpleNamespace

import pytest

from cbd.contracts import (
    BufferConfig, Field, FType, IndexKind, IndexSpec, NotApplicable, Org, OrgSpec,
    Path, RID, Schema, StorageConfig, TableStats,
)
from cbd.exec.selection import select_eq, select_eq_cost, select_range, select_range_cost


SCHEMA = Schema("aluno", (Field("dre", FType.INT, unique=True), Field("cr", FType.FLOAT, nullable=True)))
ROWS = ((1, 9.0), (2, 7.0), (3, None), (4, 7.0), (5, 8.0), (6, 9.0))


class FakeBuffer:
    block_size = 16
    reserved = 0

    @contextmanager
    def reserve_work(self, pages):
        self.reserved += pages
        try:
            yield pages
        finally:
            self.reserved -= pages


class FakeData:
    def __init__(self, org, key):
        self.spec = OrgSpec(org, key)
        self.height = 3
        self.ptr_size = 8
        self.active = 0
        self.visited = []
        self.fetched = []
        self.calls = []

    def pairs(self, rows):
        self.active += 1
        try:
            for row in rows:
                self.visited.append(row)
                yield self.pointer(row), row
        finally:
            self.active -= 1

    def pointer(self, row):
        i = ROWS.index(row)
        blocks = (2, 6, 3, 5, 4, 1)
        return (row[0], i) if self.spec.org is Org.BTREE else RID(blocks[i], i)

    def scan(self):
        self.calls.append("scan")
        return self.pairs(ROWS)

    def search(self, value):
        self.calls.append("search")
        pos = SCHEMA.idx(self.spec.key)
        return self.pairs(row for row in ROWS if row[pos] == value)

    def range(self, lo, hi, lo_inc, hi_inc):
        self.calls.append("range")
        assert self.spec.org is Org.BTREE
        return self.pairs(matching_range(self.spec.key, lo, hi, lo_inc, hi_inc))

    def fetch(self, ptr):
        self.fetched.append(ptr)
        return ROWS[ptr[1]]

    def stats(self):
        return dict(levels=3, leaf_blocks=8, overflow_blocks=2, buckets=4)


def matching_range(column, lo, hi, lo_inc, hi_inc):
    idx = SCHEMA.idx(column)
    non_null = sorted((row for row in ROWS if row[idx] is not None), key=lambda row: row[idx])
    return [row for row in non_null
            if (lo is None or row[idx] > lo or lo_inc and row[idx] == lo)
            and (hi is None or row[idx] < hi or hi_inc and row[idx] == hi)]


class FakeIndex:
    def __init__(self, data, column, kind):
        self.data = data
        self.spec = IndexSpec(SCHEMA.name, column, kind, column == "dre")
        self.active = 0
        self.yielded = 0
        self.calls = []

    def entries(self, rows):
        pos = SCHEMA.idx(self.spec.column)
        self.active += 1
        try:
            for row in rows:
                self.yielded += 1
                yield row[pos], self.data.pointer(row)
        finally:
            self.active -= 1

    def search(self, value):
        self.calls.append("search")
        pos = SCHEMA.idx(self.spec.column)
        with closing(self.entries(row for row in ROWS if row[pos] == value)) as entries:
            for _, ptr in entries:
                yield ptr

    def range(self, lo, hi, lo_inc, hi_inc):
        self.calls.append("range")
        assert self.spec.kind is IndexKind.BTREE
        return self.entries(matching_range(self.spec.column, lo, hi, lo_inc, hi_inc))

    def stats(self):
        return dict(levels=2, leaf_blocks=4, overflow_blocks=2, buckets=4)


def table(org, key=None, *, index=None):
    data = FakeData(org, key)
    indexes = {} if index is None else {index[0]: FakeIndex(data, *index)}
    stats = TableStats(6, 3, 2, 10, {"dre": 6, "cr": 3}, {"dre": 1, "cr": 7.0}, {"dre": 6, "cr": 9.0})
    return SimpleNamespace(name=SCHEMA.name, schema=SCHEMA, data=data, indexes=indexes, stats=lambda: stats)


@pytest.mark.parametrize("org,path", [(Org.HEAP, Path.SCAN), (Org.BTREE, Path.PRIMARY),
                                      (Org.HASH, Path.PRIMARY), (Org.HEAP, Path.SECONDARY)])
def test_select_eq_matrix(org, path):
    t = table(org, "dre", index=("dre", IndexKind.HASH) if path is Path.SECONDARY else None)
    info = {}
    assert list(select_eq(None, t, "dre", 2, path, info=info)) == [(2, 7.0)]
    assert info["matches"] == 1
    if path is Path.SCAN:
        assert info["variant"] == "scan-stop"
        assert t.data.visited == list(ROWS[:2])
        assert t.data.calls == ["scan"]
    elif path is Path.PRIMARY:
        assert t.data.calls == ["search"]
    else:
        assert t.data.calls == []
        assert t.indexes["dre"].calls == ["search"]
        assert t.data.fetched == [t.data.pointer(ROWS[1])]
    assert list(select_eq(None, t, "dre", 99, path)) == []
    assert t.data.active == 0


@pytest.mark.parametrize("org,path", [(Org.HEAP, Path.SCAN), (Org.BTREE, Path.SCAN),
                                      (Org.HASH, Path.SCAN), (Org.BTREE, Path.PRIMARY),
                                      (Org.HEAP, Path.SECONDARY), (Org.HASH, Path.SECONDARY),
                                      (Org.BTREE, Path.SECONDARY)])
def test_select_range_matrix(org, path):
    t = table(org, "cr", index=("cr", IndexKind.BTREE) if path is Path.SECONDARY else None)
    for bounds in [(7.0, 8.0, True, False), (8.0, None, False, True),
                   (None, 7.0, True, True), (None, None, True, True), (9.0, 7.0, True, True)]:
        result = select_range(None, t, "cr", *bounds, path=path)
        assert sorted(result) == sorted(matching_range("cr", *bounds))
    assert t.data.active == 0
    assert all(ix.active == 0 for ix in t.indexes.values())
    expected = ["scan" if path is Path.SCAN else "range"] * 4
    if path is Path.SECONDARY:
        assert t.data.calls == []
        assert t.indexes["cr"].calls == expected
    else:
        assert t.data.calls == expected


@pytest.mark.parametrize("org,key,path,index", [(Org.HEAP, None, Path.PRIMARY, None),
    (Org.BTREE, "dre", Path.PRIMARY, None), (Org.HEAP, None, Path.SECONDARY, None),
    (Org.HASH, "cr", Path.PRIMARY, None), (Org.HEAP, None, Path.SECONDARY, ("cr", IndexKind.HASH))])
def test_incompatible_range_and_cost(org, key, path, index):
    t = table(org, key, index=index)
    with pytest.raises(NotApplicable):
        list(select_range(None, t, "cr", 7.0, 9.0, path=path))
    with pytest.raises(NotApplicable):
        select_range_cost(None, t, "cr", 7.0, 9.0, path=path)


def test_null_equality_and_non_unique_scan():
    t = table(Org.HEAP)
    assert list(select_eq(None, t, "cr", None, Path.SCAN)) == []
    assert t.data.visited == []
    assert list(select_eq(None, t, "cr", 7.0, Path.SCAN)) == [ROWS[1], ROWS[3]]


def test_sort_ptrs_batches_and_closes_early():
    eng = SimpleNamespace(M=1, bp=FakeBuffer())
    t = table(Org.HEAP, index=("cr", IndexKind.BTREE))
    ix = t.indexes["cr"]
    info = {}
    with closing(select_range(eng, t, "cr", None, None, path=Path.SECONDARY, sort_ptrs=True, info=info)) as rows:
        assert next(rows) == ROWS[3]
        assert ix.yielded == 2  # Um frame de 16 bytes comporta dois RIDs de 8 bytes.
        assert eng.bp.reserved == 1
    assert ix.active == eng.bp.reserved == 0
    result = list(select_range(eng, t, "cr", None, None, path=Path.SECONDARY, sort_ptrs=True, info=info))
    assert result == [ROWS[3], ROWS[1], ROWS[0], ROWS[4], ROWS[5]]
    assert info["sort_ptrs_applied"] is True
    assert info["matches"] == 5


def test_btree_logical_ptrs_are_forwarded_without_sorting():
    t = table(Org.BTREE, "dre", index=("cr", IndexKind.BTREE))
    info = {}
    result = list(select_range(None, t, "cr", 7.0, 7.0, path=Path.SECONDARY, sort_ptrs=True, info=info))
    assert result == [ROWS[1], ROWS[3]]
    assert t.data.fetched == [(2, 1), (4, 3)]
    assert info["sort_ptrs_applied"] is False


def test_empty_selection_did_not_sort_any_pointer():
    eng = SimpleNamespace(M=1, bp=FakeBuffer())
    t = table(Org.HEAP, index=("cr", IndexKind.BTREE))
    info = {}
    assert list(select_range(eng, t, "cr", 10.0, None, path=Path.SECONDARY, sort_ptrs=True, info=info)) == []
    assert info["sort_ptrs_applied"] is False


@pytest.mark.parametrize("path", [Path.PRIMARY, Path.SECONDARY])
def test_unbounded_range_filters_nullable_keys_even_if_cursor_yields_them(path, monkeypatch):
    t = table(Org.BTREE, "cr", index=("cr", IndexKind.BTREE))
    if path is Path.PRIMARY:
        monkeypatch.setattr(t.data, "range", lambda *bounds: t.data.pairs([ROWS[2], ROWS[0]]))
    else:
        ix = t.indexes["cr"]
        monkeypatch.setattr(ix, "range", lambda *bounds: ix.entries([ROWS[2], ROWS[0]]))
    assert list(select_range(None, t, "cr", None, None, path=path)) == [ROWS[0]]


def test_range_cost_counts_matches_at_an_included_extreme():
    t = table(Org.HEAP, index=("cr", IndexKind.BTREE))
    inclusive = select_range_cost(None, t, "cr", 9.0, None, path=Path.SECONDARY)
    exclusive = select_range_cost(None, t, "cr", 9.0, None, lo_inc=False, path=Path.SECONDARY)
    assert inclusive.reads >= exclusive.reads + 2  # 6 registros / 3 chaves = 2 fetches.


def test_select_costs_use_documented_block_models():
    assert select_eq_cost(None, table(Org.HEAP), "dre", 2, Path.SCAN).reads == 1.5
    assert select_eq_cost(None, table(Org.BTREE, "dre"), "dre", 2, Path.PRIMARY).reads == 3
    assert select_eq_cost(None, table(Org.HASH, "dre"), "dre", 2, Path.PRIMARY).reads == 1.5
    assert select_eq_cost(None, table(Org.BTREE, "cr"), "cr", 7.0, Path.PRIMARY).reads == 3
    assert select_range_cost(None, table(Org.BTREE, "cr"), "cr", 7.0, 8.0, path=Path.PRIMARY).reads == 4
    indexed = table(Org.BTREE, "dre", index=("cr", IndexKind.BTREE))
    # Um nó interno + duas folhas + metade dos 2 blocos de ponteiros
    # + três fetches de três blocos.
    assert select_range_cost(None, indexed, "cr", 7.0, 8.0, path=Path.SECONDARY).reads == 13


def test_hash_eq_cost_respects_the_largest_possible_chain(monkeypatch):
    t = table(Org.HASH, "cr")
    stats = TableStats(300, 3, 100, 10, {"cr": 1}, {"cr": 7.0}, {"cr": 7.0})
    t.stats = lambda: stats
    monkeypatch.setattr(t.data, "stats", lambda: dict(overflow_blocks=2, buckets=1))
    assert select_eq_cost(None, t, "cr", 7.0, Path.PRIMARY).reads == 3


def test_select_real_heap(tmp_path):
    pytest.importorskip("cbd.storage.buffer_pool")
    pytest.importorskip("cbd.files.catalog")
    from cbd.engine import Engine

    with Engine(StorageConfig(str(tmp_path), 2048), BufferConfig()) as eng:
        eng.catalog.create_table(SCHEMA, OrgSpec(Org.HEAP))
        eng.catalog.load_table(SCHEMA.name, ROWS)
        t = eng.catalog.table(SCHEMA.name)
        eng.cold()
        assert list(select_range(eng, t, "cr", 8.0, None)) == [ROWS[0], ROWS[4], ROWS[5]]
        # Seis tuplas de int e float cabem em uma página de dados.
        assert eng.snapshot()["io.reads"] == select_range_cost(eng, t, "cr", 8.0, None).reads == 1
        assert eng.bp.pinned() == 0
