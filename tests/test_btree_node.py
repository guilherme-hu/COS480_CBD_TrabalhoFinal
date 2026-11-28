"""P3 N1: formatos aprovados; não pressupõe bloco 1 ou ocupação da carga."""
import struct
from bisect import bisect_right

import pytest

from cbd.contracts import Field, FType, RID, RecordTooLarge, Schema
from cbd.files.btree_node import _InternalPage, _PrimaryEntries, _decode_rid, _encode_rid
from cbd.records.codec import RecordCodec, encode_key
from cbd.records.slotted_page import SlottedPage


def test_internal_literal_bytes_and_equality_goes_right():
    # D06, sem depender do escritor para calcular os bytes esperados.
    buf = bytearray(4096)
    node = _InternalPage(buf, 4)
    node.write([12, 27, 41], [b"\x80\x00\x00\x14", b"\x80\x00\x00\x28"])
    assert bytes(buf[:22]) == (
        b"\x02\x00\x0c\x00\x00\x00\x80\x00\x00\x14"
        b"\x1b\x00\x00\x00\x80\x00\x00\x28\x29\x00\x00\x00"
    )
    key = Field("id", FType.INT)
    assert [node.child_for(encode_key(key, x)) for x in (-10, 19, 20, 39, 40, 90)] == [12, 12, 27, 27, 41, 41]
    assert len(buf) == 4096


@pytest.mark.parametrize("k,fanout", [(4, 512), (8, 341), (12, 256)])
def test_full_internal_page_and_overflow_does_not_modify(k, fanout):
    node = _InternalPage(bytearray(4096), k)
    assert node.max_children == fanout
    separators = [i.to_bytes(k, "big") for i in range(1, fanout)]
    children = list(range(2, fanout + 2))
    node.write(children, separators)
    assert node.count == fanout - 1
    assert [node.child(i) for i in range(fanout)] == children
    assert node.separator(fanout - 2) == separators[-1]
    for value in range(fanout + 1):
        probe = value.to_bytes(k, "big")
        assert node.child_for(probe) == children[bisect_right(separators, probe)]
    before = bytes(node.buf)
    with pytest.raises(ValueError):
        node.write(children + [900], separators + [fanout.to_bytes(k, "big")])
    assert bytes(node.buf) == before


@pytest.mark.parametrize("k", [8, 12])
def test_wide_internal_layout_has_independent_literal_boundaries(k):
    node = _InternalPage(bytearray(4096), k)
    first = b"\x01" * k
    second = b"\xfe" * k
    node.write([0x01020304, 0x11223344, 0x55667788], [first, second])
    expected = (b"\x02\x00\x04\x03\x02\x01" + first + b"\x44\x33\x22\x11"
                + second + b"\x88\x77\x66\x55")
    assert bytes(node.buf[:len(expected)]) == expected
    assert node.child_for(b"\x00" * k) == 0x01020304
    assert node.child_for(first) == 0x11223344
    assert node.child_for(b"\xff" * k) == 0x55667788


@pytest.mark.parametrize("children,separators", [
    ([2], [b"abcd"]), ([2, 3], [b"a"]), ([2, 0], [b"abcd"]),
    ([2, 2**32], [b"abcd"]), ([2, 3, 4], [b"bbbb", b"aaaa"]),
    ([2, 3, 4], [b"aaaa", b"aaaa"]),
])
def test_invalid_internal_write_is_non_destructive(children, separators):
    node = _InternalPage(bytearray(4096), 4)
    node.write([55], [])  # Formato de página, não regra de ocupação aprovada.
    before = bytes(node.buf)
    with pytest.raises(ValueError):
        node.write(children, separators)
    assert bytes(node.buf) == before


def test_corrupt_count_and_wrong_lookup_width_are_not_silently_used():
    node = _InternalPage(bytearray(4096), 4)
    with pytest.raises(ValueError):
        node.child_for(b"x")
    node.buf[:2] = b"\xff\xff"
    with pytest.raises(ValueError):
        node.child_for(b"abcd")


def test_duplicate_primary_keys_compare_full_identifier_across_leaves():
    field = Field("cr", FType.FLOAT)
    key = encode_key(field, 8.0)
    node = _InternalPage(bytearray(4096), 12)
    node.write([17, 23], [key + b"\x00\x00\x00\x03"])
    assert node.child_for(key + b"\x00\x00\x00\x01") == 17
    assert node.child_for(key + b"\x00\x00\x00\x02") == 17
    assert node.child_for(key + b"\x00\x00\x00\x03") == 23
    assert node.child_for(key + b"\xff\xff\xff\xff") == 23


SCHEMA = Schema("aluno", (Field("dre", FType.INT), Field("cr", FType.FLOAT),
                          Field("nome", FType.VARCHAR, 120, nullable=True)))


@pytest.mark.parametrize("unique", [True, False])
@pytest.mark.parametrize("record", [(123456789, 7.0, "Ana"), (18, 8.0, "José"), (19, 8.0, None)])
def test_primary_reuses_exact_p2_record_bytes(unique, record):
    entries = _PrimaryEntries(SCHEMA, "cr", unique, 4096)
    data = entries.encode(record, None if unique else 0x01020304)
    prefix = b"" if unique else b"\x01\x02\x03\x04"
    assert data == prefix + RecordCodec(SCHEMA).encode(record)
    assert entries.decode(memoryview(data)) == record
    assert entries.order_key(data) == encode_key(SCHEMA.get("cr"), record[1]) + prefix


@pytest.mark.parametrize("identifier", [None, 0, -1, 2**32])
def test_nonunique_identifier_has_no_silent_wrap(identifier):
    entries = _PrimaryEntries(SCHEMA, "dre", False, 4096)
    with pytest.raises(ValueError):
        entries.encode((8, 7.0, "Ana"), identifier)


def test_unique_has_no_identifier_and_nonunique_accepts_full_unsigned_domain():
    entries = _PrimaryEntries(SCHEMA, "dre", True, 4096)
    with pytest.raises(ValueError):
        entries.encode((8, 7.0, "Ana"), 1)
    entries = _PrimaryEntries(SCHEMA, "dre", False, 4096)
    assert entries.encode((8, 7.0, "Ana"), 2**32 - 1)[:4] == b"\xff" * 4


@pytest.mark.parametrize("unique,limit", [(True, 4082), (False, 4078)])
def test_record_limit_includes_prefix_and_slot(unique, limit):
    # RecordCodec = INT4 + VARCHAR length2 + conteúdo. Escolha de preenchimento
    # não interfere no limite físico de UMA entrada numa página vazia.
    schema = Schema("wide", (Field("id", FType.INT), Field("texto", FType.VARCHAR, 5000)))
    entries = _PrimaryEntries(schema, "id", unique, 4096)
    assert entries.max_record == limit
    record = (1, "x" * (limit - 6))
    raw = entries.encode(record, None if unique else 1)
    sp = SlottedPage(bytearray(4096))
    sp.init()
    assert sp.insert_at(0, raw)
    assert sp.free_space() == 0
    assert entries.decode(sp.get(0)) == record
    with pytest.raises(RecordTooLarge):
        entries.encode((1, record[1] + "x"), None if unique else 2)


def test_rid_literal_bytes_and_extremes_match_p2():
    assert _encode_rid(RID(0x01020304, 0x0506)) == b"\x04\x03\x02\x01\x06\x05"
    for ptr in (RID(2, 0), RID(2**32 - 1, 65535)):
        assert _decode_rid(_encode_rid(ptr)) == ptr
    with pytest.raises(struct.error):
        _decode_rid(b"short")


def test_returned_separator_is_not_a_view():
    node = _InternalPage(bytearray(4096), 4)
    node.write([2, 3], [b"abcd"])
    copied = node.separator(0)
    node.buf[6:10] = b"zzzz"
    assert copied == b"abcd"
