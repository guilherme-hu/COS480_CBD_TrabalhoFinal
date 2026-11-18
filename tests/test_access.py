from contextlib import closing
from types import SimpleNamespace

import pytest

from cbd.contracts import (
    BufferConfig, Field, FType, IndexKind, IndexSpec, JoinSide, NotApplicable,
    Org, OrgSpec, Path, RID, Schema, StorageConfig,
)
from cbd.exec.access import can_lookup, can_order, fetch_cost, lookup, read_all, read_ordered


SCHEMA = Schema("demo", (Field("id", FType.INT, unique=True), Field("grupo", FType.INT)))
RECORDS = ((3, 2), (1, 2), (2, 1))


def pointer(org, slot):
    return (RECORDS[slot][0], slot) if org is Org.BTREE else RID(1, slot)


class CursorSource:
    def __init__(self):
        self.cursor = None
        self.active = 0
        self.calls = []

    def open(self, method, rows, *args):
        self.calls.append((method, *args))

        def stream():
            self.active += 1
            try:
                yield from rows
            finally:
                self.active -= 1

        # Segura o cursor para que o teste não dependa da coleta de lixo.
        self.cursor = stream()
        return self.cursor


class FakeData(CursorSource):
    def __init__(self, org):
        super().__init__()
        self.spec = OrgSpec(org, None if org is Org.HEAP else "id")
        self.height = 3

    def scan(self):
        return self.open("scan", enumerate(RECORDS))

    def range(self, lo, hi):
        assert self.spec.org is Org.BTREE
        ordered = sorted(enumerate(RECORDS), key=lambda pair: pair[1])
        return self.open("range", ordered, lo, hi)

    def search(self, value):
        assert self.spec.org is not Org.HEAP
        column_index = SCHEMA.idx(self.spec.key)
        matches = (pair for pair in enumerate(RECORDS) if pair[1][column_index] == value)
        return self.open("search", matches, value)

    def fetch(self, ptr):
        self.calls.append(("fetch", ptr))
        _, slot = ptr
        assert ptr == pointer(self.spec.org, slot)
        return RECORDS[slot]


class FakeIndex(CursorSource):
    def __init__(self, org, column, kind):
        super().__init__()
        self.spec = IndexSpec(SCHEMA.name, column, kind, column == "id")
        self.org = org
        self.column_index = SCHEMA.idx(column)

    def search(self, value):
        pointers = (
            pointer(self.org, slot) for slot, row in enumerate(RECORDS)
            if row[self.column_index] == value
        )
        return self.open("search", pointers, value)

    def range(self, lo, hi):
        assert self.spec.kind is IndexKind.BTREE
        entries = sorted(
            ((row[self.column_index], pointer(self.org, slot)) for slot, row in enumerate(RECORDS)),
            key=lambda pair: pair[0],
        )
        return self.open("range", entries, lo, hi)


def make_side(org, path, column, kind=None):
    indexes = {} if kind is None else {column: FakeIndex(org, column, kind)}
    table = SimpleNamespace(name=SCHEMA.name, schema=SCHEMA, data=FakeData(org), indexes=indexes)
    return JoinSide(table, column, path)


@pytest.mark.parametrize(
    "org, path, column, kind",
    [(Org.HEAP, Path.SCAN, "id", None), (Org.BTREE, Path.PRIMARY, "id", None),
     (Org.HASH, Path.PRIMARY, "id", None),
     (Org.HEAP, Path.SECONDARY, "grupo", IndexKind.BTREE)],
)
def test_scan_always_reads_physical_order(org, path, column, kind):
    side = make_side(org, path, column, kind)
    assert list(read_all(side)) == list(RECORDS)
    assert side.table.data.calls == [("scan",)]
    assert all(index.calls == [] for index in side.table.indexes.values())


@pytest.mark.parametrize(
    "org, path, column, kind, ordered, matches",
    [
        (Org.HEAP, Path.SCAN, "id", None, None, None),
        (Org.BTREE, Path.SCAN, "id", None, None, None),
        (Org.BTREE, Path.PRIMARY, "id", None, [1, 2, 3], [1]),
        (Org.HASH, Path.PRIMARY, "id", None, None, [1]),
        (Org.HEAP, Path.SECONDARY, "id", IndexKind.HASH, None, [1]),
        (Org.HEAP, Path.SECONDARY, "grupo", IndexKind.BTREE, [2, 3, 1], [2]),
        (Org.HASH, Path.SECONDARY, "grupo", IndexKind.BTREE, [2, 3, 1], [2]),
        (Org.BTREE, Path.SECONDARY, "grupo", IndexKind.BTREE, [2, 3, 1], [2]),
    ],
)
def test_access_paths(org, path, column, kind, ordered, matches):
    side = make_side(org, path, column, kind)
    assert can_order(side) is (ordered is not None)
    assert can_lookup(side) is (matches is not None)
    ordered_cursor = read_ordered(side)
    lookup_cursor = lookup(side, 1)
    assert side.table.data.calls == []
    assert all(index.calls == [] for index in side.table.indexes.values())

    records_by_id = {row[0]: row for row in RECORDS}
    if ordered is None:
        assert ordered_cursor is None
    else:
        assert list(ordered_cursor) == [records_by_id[key] for key in ordered]

    if matches is None:
        assert lookup_cursor is None
    else:
        assert list(lookup_cursor) == [records_by_id[key] for key in matches]
        assert list(lookup(side, 999)) == []

    if path is Path.SECONDARY:
        expected = [] if ordered is None else [("range", None, None)]
        assert side.table.indexes[column].calls == [*expected, ("search", 1), ("search", 999)]
        fetched_ids = [*(ordered or []), *matches]
        assert side.table.data.calls == [
            ("fetch", pointer(org, RECORDS.index(records_by_id[key]))) for key in fetched_ids
        ]
    else:
        expected = [] if ordered is None else [("range", None, None)]
        if matches is not None:
            expected.extend([("search", 1), ("search", 999)])
        assert side.table.data.calls == expected


@pytest.mark.parametrize("org, path", [(Org.HASH, Path.PRIMARY), (Org.BTREE, Path.PRIMARY),
                                       (Org.HEAP, Path.SECONDARY)])
def test_lookup_keeps_all_rows_for_a_non_unique_key(org, path):
    kind = IndexKind.BTREE if path is Path.SECONDARY else None
    side = make_side(org, path, "grupo", kind)
    if path is Path.PRIMARY:
        side.table.data.spec = OrgSpec(org, "grupo")

    assert list(lookup(side, 2)) == [(3, 2), (1, 2)]


@pytest.mark.parametrize("operation", ["read_ordered", "lookup"])
def test_cursor_keeps_the_validated_access_path(operation):
    if operation == "read_ordered":
        side = make_side(Org.BTREE, Path.PRIMARY, "id")
        cursor = read_ordered(side)
        side.path = Path.SCAN
        expected = sorted(RECORDS)
    else:
        side = make_side(Org.HEAP, Path.SECONDARY, "grupo", IndexKind.BTREE)
        cursor = lookup(side, 2)
        side.column = "id"
        expected = [(3, 2), (1, 2)]

    assert list(cursor) == expected


@pytest.mark.parametrize(
    "org, path, column",
    [(Org.HEAP, Path.PRIMARY, "id"), (Org.HASH, Path.PRIMARY, "grupo"),
     (Org.BTREE, Path.PRIMARY, "grupo"), (Org.HEAP, Path.SECONDARY, "grupo")],
)
def test_incompatible_paths_raise_before_opening_a_cursor(org, path, column):
    side = make_side(org, path, column)
    for check in (can_lookup, can_order, read_ordered):
        with pytest.raises(NotApplicable, match=f"demo\\.{column}"):
            check(side)
    with pytest.raises(NotApplicable, match=f"demo\\.{column}"):
        lookup(side, 1)
    assert side.table.data.calls == []


@pytest.mark.parametrize("org, expected", [(Org.HEAP, 1), (Org.HASH, 1), (Org.BTREE, 3)])
def test_fetch_cost_uses_storage_layout(org, expected):
    side = make_side(org, Path.SCAN, "id")
    assert fetch_cost(side.table) == expected
    assert side.table.data.calls == []


@pytest.mark.parametrize("operation", ["scan", "ordered_primary", "ordered_index", "primary", "index"])
def test_close_releases_source_cursor_after_one_record(operation):
    if operation in ("scan", "ordered_primary", "primary"):
        side = make_side(Org.BTREE, Path.PRIMARY, "id")
        source = side.table.data
    else:
        side = make_side(Org.HEAP, Path.SECONDARY, "grupo", IndexKind.BTREE)
        source = side.table.indexes["grupo"]

    if operation == "scan":
        cursor = read_all(side)
    elif operation in ("ordered_primary", "ordered_index"):
        cursor = read_ordered(side)
    else:
        cursor = lookup(side, 2)

    with closing(cursor):
        next(cursor)
        assert source.active == 1

    assert source.active == 0
    assert list(source.cursor) == []


def test_secondary_fetch_error_closes_index_cursor(monkeypatch):
    side = make_side(Org.HEAP, Path.SECONDARY, "grupo", IndexKind.BTREE)

    def bad_fetch(ptr):
        raise KeyError(ptr)

    monkeypatch.setattr(side.table.data, "fetch", bad_fetch)
    with pytest.raises(KeyError):
        next(lookup(side, 2))
    assert side.table.indexes["grupo"].active == 0


def test_access_with_real_heap_and_btree(tmp_path):
    pytest.importorskip("cbd.storage.buffer_pool")
    pytest.importorskip("cbd.files.catalog")
    pytest.importorskip("cbd.files.btree_file")
    from cbd.engine import Engine

    with Engine(StorageConfig(str(tmp_path), 2048), BufferConfig()) as eng:
        eng.catalog.create_table(SCHEMA, OrgSpec(Org.HEAP))
        eng.catalog.load_table(SCHEMA.name, RECORDS)
        tree_schema = Schema("demo_btree", SCHEMA.fields)
        eng.catalog.create_table(tree_schema, OrgSpec(Org.BTREE, "id", unique=True))
        eng.catalog.load_table(tree_schema.name, sorted(RECORDS))
        heap = JoinSide(eng.catalog.table(SCHEMA.name), "id", Path.SCAN)
        tree = JoinSide(eng.catalog.table(tree_schema.name), "id", Path.PRIMARY)
        try:
            eng.cold()
            assert list(read_all(heap)) == list(RECORDS)
            assert read_ordered(heap) is None
            # Três tuplas de dois inteiros cabem em um bloco de 2 KiB.
            assert eng.snapshot()["io.reads"] == 1
            assert eng.bp.pinned() == 0

            eng.cold()
            assert list(read_ordered(tree)) == sorted(RECORDS)
            # Toda a B+ cabe na raiz, que também é a única folha de dados.
            assert eng.snapshot()["io.reads"] == 1
            assert eng.bp.pinned() == 0

            eng.cold()
            with closing(lookup(tree, 3)) as cursor:
                assert next(cursor) == (3, 2)
            assert eng.snapshot()["io.reads"] == 1
        finally:
            assert eng.bp.pinned() == 0
