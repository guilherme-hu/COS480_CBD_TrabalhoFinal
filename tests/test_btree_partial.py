"""P3 N2: componentes reais, não uma árvore criável pelo catálogo.

Páginas de teste fornecem raiz/altura/área explicitamente; não aprovam bloco1,
preenchimento ou política de construção. Testes pendentes não passam com stubs.
"""
import inspect

import pytest

from cbd.contracts import Field, FType, PageType, RID, Schema
from cbd.files.btree_core import _descend, _fetch_primary, expected_height
from cbd.files.btree_file import BTreeFile
from cbd.files.btree_index import BTreeIndex
from cbd.files.btree_node import _InternalPage, _PrimaryEntries
from cbd.records.codec import encode_key
from cbd.records.slotted_page import SlottedPage


SCHEMA = Schema("folhas", (Field("cr", FType.FLOAT), Field("nome", FType.VARCHAR, 80)))


def save(bp, fid, fill):
    # Área INDEX escolhida pelo teste; a política de arquivos P3 continua L04.
    page = bp.new_page(fid, PageType.INDEX)
    try:
        fill(page.data)
        page.mark_dirty()
        return page.bno
    finally:
        bp.unpin(page)


def cold(bp):
    bp.clear()  # Escreve a preparação; zerar depois exclui essas gravações.
    bp.fm.reset_stats()
    bp.reset_stats()


@pytest.mark.parametrize("unique", [True, False])
def test_primary_page_fetch_after_buffer_close_and_reopen(bp, unique):
    entries = _PrimaryEntries(SCHEMA, "cr", unique, bp.block_size)
    rows = [(7.0, "Ana"), (8.0, "Bia"), (9.0 if unique else 8.0, "Caio")]
    fid = bp.fm.create("primary_parts")

    def fill(buf):
        sp = SlottedPage(buf)
        sp.init()
        for i, rec in enumerate(rows, 1):
            assert sp.insert_at(sp.n_slots(), entries.encode(rec, None if unique else i))

    block = save(bp, fid, fill)
    bp.close_file(fid)
    fid = bp.fm.open("primary_parts")  # Só reabre P1, não BTreeFile.open.
    cold(bp)
    record = _fetch_primary(bp, fid, PageType.INDEX, RID(block, 1), entries)
    assert record == rows[1]
    # RID físico exige UMA folha, independentemente da altura; sem meta carregada.
    assert bp.fm.io.reads == 1
    assert bp.fm.io.writes == 0
    assert bp.pinned() == 0
    assert [_fetch_primary(bp, fid, PageType.INDEX, RID(block, i), entries) for i in range(3)] == rows
    assert record == rows[1]  # Registro independe da view/pin anterior.


@pytest.mark.parametrize("value,expected_leaf", [(1, 0), (20, 1), (40, 2), (60, 3), (99, 3)])
def test_multilevel_descent_reads_only_path_and_fetches_leaf(bp, value, expected_leaf):
    schema = Schema("ints", (Field("id", FType.INT),))
    entries = _PrimaryEntries(schema, "id", True, 4096)
    field = schema.get("id")
    fid = bp.fm.create("three_levels")
    leaf_min = [1, 20, 40, 60]
    leaves = []
    for minimum in leaf_min:
        def fill(buf, minimum=minimum):
            sp = SlottedPage(buf)
            sp.init()
            assert sp.insert_at(0, entries.encode((minimum,)))
        leaves.append(save(bp, fid, fill))
    left = save(bp, fid, lambda b: _InternalPage(b, 4).write(leaves[:2], [encode_key(field, 20)]))
    right = save(bp, fid, lambda b: _InternalPage(b, 4).write(leaves[2:], [encode_key(field, 60)]))
    root = save(bp, fid, lambda b: _InternalPage(b, 4).write([left, right], [encode_key(field, 40)]))
    cold(bp)
    block = _descend(bp, fid, PageType.INDEX, root, 3, encode_key(field, value), 4)
    assert block == leaves[expected_leaf]
    assert bp.pinned() == 0
    # Altura3: raiz + um interno = 2 leituras. A descida ainda não lê a folha.
    assert bp.fm.io.reads == 2
    assert _fetch_primary(bp, fid, PageType.INDEX, RID(block, 0), entries) == (leaf_min[expected_leaf],)
    assert bp.fm.io.reads == 3
    assert bp.pinned() == 0


def test_height_one_needs_no_internal_read(bp):
    cold(bp)
    assert _descend(bp, 1, PageType.INDEX, 8, 1, b"abcd", 4) == 8
    assert bp.fm.io.reads == 0  # Só calcula bloco; o caller ainda precisa ler a folha.
    assert bp.pinned() == 0


def test_decode_error_releases_pin(bp):
    entries = _PrimaryEntries(SCHEMA, "cr", False, 4096)
    fid = bp.fm.create("broken_record")

    def fill(buf):
        sp = SlottedPage(buf)
        sp.init()
        assert sp.insert_at(0, b"x")

    block = save(bp, fid, fill)
    with pytest.raises(ValueError):
        _fetch_primary(bp, fid, PageType.INDEX, RID(block, 0), entries)
    assert bp.pinned() == 0


def test_bad_internal_count_releases_pin(bp):
    fid = bp.fm.create("broken_internal")
    block = save(bp, fid, lambda buf: buf.__setitem__(slice(0, 2), b"\xff\xff"))
    with pytest.raises(ValueError):
        _descend(bp, fid, PageType.INDEX, block, 2, b"abcd", 4)
    assert bp.pinned() == 0


def test_linked_nonunique_leaves_keep_duplicate_order_and_physical_rids(bp):
    entries = _PrimaryEntries(SCHEMA, "cr", False, 4096)
    rows = [(7.0, "Ana"), (8.0, "Bia"), (8.0, "Caio"), (8.0, "Dani")]
    fid = bp.fm.create("linked_parts")
    blocks = []
    for start in (0, 2):
        def fill(buf, start=start):
            sp = SlottedPage(buf)
            sp.init()
            for i in range(start, start + 2):
                assert sp.insert_at(sp.n_slots(), entries.encode(rows[i], i + 1))
        blocks.append(save(bp, fid, fill))
    with bp.page(fid, blocks[0], PageType.INDEX) as page:
        SlottedPage(page.data).next = blocks[1]
        page.mark_dirty()
    cold(bp)
    found, keys, pointers = [], [], []
    block = blocks[0]
    while block:
        with bp.page(fid, block, PageType.INDEX) as page:
            sp = SlottedPage(page.data)
            for slot in range(sp.n_slots()):
                raw = sp.get(slot)
                found.append(entries.decode(raw))
                keys.append(entries.order_key(raw))
                pointers.append(RID(block, slot))
            block = sp.next
    assert found == rows
    assert all(a < b for a, b in zip(keys, keys[1:]))
    assert pointers == [RID(blocks[0], 0), RID(blocks[0], 1), RID(blocks[1], 0), RID(blocks[1], 1)]
    # Duas folhas reais, nenhum interno/meta: 2 leituras frias, sem perder os 3 CR8.
    assert bp.fm.io.reads == 2
    assert bp.pinned() == 0
    assert [_fetch_primary(bp, fid, PageType.INDEX, ptr, entries) for ptr in pointers] == rows


class Untouchable:
    def __getattr__(self, name):
        raise AssertionError(f"esqueleto não deve acessar {name}")


@pytest.mark.parametrize("cls,args", [(BTreeFile, (None, None)), (BTreeIndex, (None, None, None))])
@pytest.mark.parametrize("operation", ["create", "open"])
def test_public_creation_is_explicitly_pending_before_io(cls, args, operation):
    with pytest.raises(NotImplementedError, match="L02"):
        getattr(cls, operation)(Untouchable(), "no_file", *args)


def test_unfinished_methods_do_not_consume_input_or_return_fake_results():
    def forbidden_input():
        raise AssertionError("não consumir antes de resolver lacunas")
        yield  # pragma: no cover
    for cls in (BTreeFile, BTreeIndex):
        obj = object.__new__(cls)  # Só exercita esqueletos, não representa arquivo aberto.
        with pytest.raises(NotImplementedError, match="L03/L04"):
            obj.bulk_load(forbidden_input())
        with pytest.raises(NotImplementedError):
            obj.search(7)
        with pytest.raises(NotImplementedError):
            obj.stats()
    with pytest.raises(NotImplementedError, match="L03/L05"):
        expected_height(10, 10, .9)


def test_existing_public_call_shapes_are_preserved():
    assert list(inspect.signature(BTreeFile.create).parameters) == ["bp", "name", "schema", "spec"]
    assert list(inspect.signature(BTreeIndex.create).parameters) == ["bp", "name", "spec", "key_field", "data"]
    for cls in (BTreeFile, BTreeIndex):
        sig = inspect.signature(cls.range)
        assert list(sig.parameters) == ["self", "lo", "hi", "lo_inc", "hi_inc"]
        assert sig.parameters["lo_inc"].default is True
        assert sig.parameters["hi_inc"].default is True
