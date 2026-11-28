"""Caminhos B+ N1. O adaptador P4 é real, os cursores da árvore são dublês."""

from collections import Counter
from types import SimpleNamespace

import pytest

from cbd.contracts import FType, Field, IndexKind, JoinSide, Org, OrgSpec, Path, Schema
from cbd.exec.sort_merge import sort_merge_join, sort_merge_join_cost


def _ordered_side(name, rows, unique, pins, secondary=False):
    schema = Schema(name, (Field("k", FType.INT, unique=unique, nullable=True),
                           Field("value", FType.INT)))

    def iter_indexed():
        pins.append(name)
        try:
            yield from enumerate(rows)
        finally:
            pins.remove(name)

    def data_range(lo, hi):
        assert lo is hi is None
        return iter_indexed()

    def index_range(lo, hi):
        assert lo is hi is None
        for pos, row in iter_indexed():
            yield row[0], pos

    def unexpected_scan():
        pytest.fail("Um caminho B+ ordenado não deve varrer o arquivo")

    data = SimpleNamespace(spec=OrgSpec(Org.HEAP if secondary else Org.BTREE, "k"),
                           range=data_range, fetch=rows.__getitem__, height=2, scan=unexpected_scan)
    index = SimpleNamespace(spec=SimpleNamespace(kind=IndexKind.BTREE), range=index_range,
                            stats=lambda: {"levels": 2, "leaf_blocks": 2, "overflow_blocks": 1})
    table = SimpleNamespace(name=name, schema=schema, data=data,
                            stats=lambda: SimpleNamespace(n_blocks=2, n_records=len(rows)),
                            indexes={"k": index} if secondary else {})
    return JoinSide(table, "k", Path.SECONDARY if secondary else Path.PRIMARY)


@pytest.mark.parametrize("secondary", [False, True])
def test_smj_btree_no_sort(secondary):
    # Eng não fornece WORK/catálogo: qualquer tentativa de sort falha.
    eng = SimpleNamespace(M=3)
    pins = []
    rr, ss = [(1, 10), (2, 20), (8, 80)], [(1, 100), (1, 101), (3, 300), (8, 800), (None, 0)]
    r = _ordered_side("r", rr, True, pins)
    s = _ordered_side("s", ss, False, pins, secondary=secondary)
    info = {}
    assert Counter(sort_merge_join(eng, r, s, info)) == Counter({
        (1, 10, 1, 100): 1, (1, 10, 1, 101): 1, (8, 80, 8, 800): 1,
    })
    assert info["variant"] == "ordered"
    assert not info["sorted_r"] and not info["sorted_s"]
    assert info["index_used"]
    assert not pins
    assert sort_merge_join_cost(eng, r, s).reads == 3 + (9 if secondary else 3)


def test_smj_close_ordered_cursor():
    pins = []
    eng = SimpleNamespace(M=3)
    rows = [(1, 10), (2, 20)]
    r = _ordered_side("r", rows, True, pins)
    s = _ordered_side("s", rows, False, pins, secondary=True)
    query = sort_merge_join(eng, r, s)
    assert next(query) == (1, 10, 1, 10)
    assert pins == ["r", "s"]
    query.close()
    assert not pins
