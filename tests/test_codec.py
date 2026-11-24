"""Testes do codec de registros e das chaves que preservam a ordem (P2, issue #39).

Decisões testadas: D-P2-01 (prefixo u16), D-P2-02 (sentinelas), D-P2-03 (LIST sem contagem),
D-P2-04 (endereço como string), D-P2-05 (chaves em bytes). Ver docs/decisoes/P2.md.
"""
from datetime import date, datetime
import math
import random
import struct

import pytest

from bench.generator import BASE_COUNTS, Dataset
from bench.schema import SCHEMAS
from cbd.contracts import Field, FType, Schema
from cbd.records.codec import MAX_LIST, RecordCodec, decode_key, encode_key, key_size
from cbd.records.slotted_page import SlottedPage

ALL = Schema("t", (
    Field("i", FType.INT), Field("f", FType.FLOAT), Field("d", FType.DATE), Field("c", FType.CHAR, 4),
    Field("v", FType.VARCHAR, 10), Field("l", FType.LIST, 5),
    Field("ni", FType.INT, nullable=True), Field("nf", FType.FLOAT, nullable=True),
    Field("nd", FType.DATE, nullable=True), Field("nc", FType.CHAR, 3, nullable=True),
    Field("nv", FType.VARCHAR, 10, nullable=True), Field("nl", FType.LIST, 5, nullable=True),
))
ROW = (7, -1.5, date(2001, 2, 3), "ab", "olá", ("x", "yy"), 1, 2.0, date(1999, 1, 1), "z", "", ())


# ------------------------------------------------------------------ layout (N1)
def test_layout_fixed_part_first_then_length_prefixed_variables():
    # D-P2-01/02/03: fixos em ordem (INT 8, FLOAT 8, DATE 4, CHAR size), sem bitmap; depois cada
    # variável com u16 de tamanho; LIST = u16 total + itens (u8 len, bytes), sem contagem.
    s = Schema("s", (Field("a", FType.INT), Field("n", FType.VARCHAR, 9), Field("c", FType.CHAR, 3),
                     Field("l", FType.LIST, 4)))
    b = RecordCodec(s).encode((5, "ab", "x", ("p", "qq")))
    expected = (struct.pack("<q", 5) + b"x\x00\x00"          # parte fixa: a, c
                + struct.pack("<H", 2) + b"ab"                # n
                + struct.pack("<H", 5) + b"\x01p\x02qq")      # l: 1+1 + 1+2 = 5 bytes
    assert b == expected


def test_null_sentinels_take_the_field_space():
    # D-P2-02: NULL ocupa o espaço do campo; trocar NULL por valor não muda o tamanho dos fixos.
    s = Schema("s", (Field("i", FType.INT, nullable=True), Field("f", FType.FLOAT, nullable=True),
                     Field("d", FType.DATE, nullable=True), Field("c", FType.CHAR, 2, nullable=True),
                     Field("v", FType.VARCHAR, 5, nullable=True)))
    c = RecordCodec(s)
    b = c.encode((None, None, None, None, None))
    assert b[:8] == struct.pack("<q", -2**63)
    assert math.isnan(struct.unpack("<d", b[8:16])[0])
    assert b[16:20] == struct.pack("<i", 0)
    assert b[20] == 0xFF
    assert b[22:] == b"\xff\xff"                     # VARCHAR NULL: só o u16 0xFFFF
    assert len(b) == len(c.encode((1, 1.0, date.today(), "a", ""))) == 24


# ------------------------------------------------------------------ roundtrip (N1)
def test_codec_roundtrip():
    c = RecordCodec(ALL)
    assert c.decode(c.encode(ROW)) == ROW
    nulls = ROW[:6] + (None,) * 6
    assert c.decode(c.encode(nulls)) == nulls
    # LIST vazia e VARCHAR vazio não são NULL; LIST cheia (MAX_LIST itens de `size`); limites
    edge = (2**63 - 1, float("inf"), date.max, "ãé", "x" * 10, ("abcde",) * MAX_LIST,
            -2**63 + 1, -0.0, date.min, "", "1234567890", ())
    got = c.decode(c.encode(edge))
    assert got == edge and math.copysign(1, got[7]) == -1.0      # −0,0 preservado no registro
    assert len(c.encode(edge)) <= c.max_size
    # memoryview também é aceito
    assert c.decode(memoryview(c.encode(ROW))) == ROW
    # LIST pode chegar como list; volta como tuple
    assert c.decode(c.encode(ROW[:5] + (["x", "yy"],) + ROW[6:]))[5] == ("x", "yy")


def test_max_size_is_the_largest_possible_record():
    c = RecordCodec(ALL)
    biggest = (1, 1.0, date.today(), "abcd", "x" * 10, ("y" * 5,) * MAX_LIST,
               1, 1.0, date.today(), "abc", "x" * 10, ("y" * 5,) * MAX_LIST)
    # fixos: 8+8+4+4 + 8+8+4+3 = 47; VARCHAR 2+10 (×2); LIST 2 + 16·(1+5) = 98 (×2)
    assert c.max_size == len(c.encode(biggest)) == 47 + 2 * 12 + 2 * 98


def test_codec_has_no_block_limit():
    # D-P2-06: o codec não conhece o bloco; quem levanta RecordTooLarge é o heap/hash ao inserir
    # (testado na #43). Aqui, um registro maior que qualquer bloco é codificado sem erro.
    s = Schema("s", (Field("v", FType.VARCHAR, 9000),))
    assert len(RecordCodec(s).encode(("x" * 9000,))) == 9002
    with pytest.raises(TypeError):                     # assinatura da arquitetura §5.1: só o esquema
        RecordCodec(s, block_size=4096)


@pytest.mark.parametrize("table", sorted(SCHEMAS))
def test_every_schema_fits_the_smallest_block(table):
    # D-P2-06: o maior registro possível de cada tabela (VARCHAR cheio, LIST com 16 itens cheios)
    # cabe numa página de 2 048 B (max_record = 2048 − 10 − 4 = 2 034). Hoje o maior é
    # detalhes com 989 B; então RecordTooLarge nunca dispara na bancada.
    assert RecordCodec(SCHEMAS[table]).max_size <= SlottedPage.max_record(2048)


@pytest.mark.parametrize("bad", [
    (None,) + ROW[1:],                                 # None em campo obrigatório
    (-2**63,) + ROW[1:],                               # sentinela de INT como dado
    (2**63,) + ROW[1:],                                # fora de 64 bits
    ROW[:1] + (math.nan,) + ROW[2:],                   # NaN é a sentinela de FLOAT
    ROW[:1] + (10**400,) + ROW[2:],                    # int maior que o maior double
    ROW[:2] + (datetime(2001, 2, 3, 4),) + ROW[3:],    # datetime perderia a hora
    ROW[:3] + ("abcde",) + ROW[4:],                    # CHAR maior que size
    ROW[:3] + ("éé€",) + ROW[4:],                      # CHAR: 7 bytes utf-8 > 4
    ROW[:3] + ("a\x00",) + ROW[4:],                    # CHAR com \x00 (seria cortado no decode)
    ROW[:4] + ("x" * 11,) + ROW[5:],                   # VARCHAR maior que size
    ROW[:5] + (("x",) * (MAX_LIST + 1),) + ROW[6:],    # LIST com itens demais
    ROW[:5] + (("abcdef",),) + ROW[6:],                # item maior que size
    ROW[:5] + ("abc",) + ROW[6:],                      # LIST que não é sequência de str
    ROW[:-1],                                          # aridade errada
])
def test_encode_rejects_invalid_values(bad):
    with pytest.raises(ValueError):
        RecordCodec(ALL).encode(bad)


def test_schema_limits_are_checked_once():
    with pytest.raises(ValueError):
        RecordCodec(Schema("s", (Field("l", FType.LIST, 256),)))       # item com u8 de tamanho
    with pytest.raises(ValueError):
        RecordCodec(Schema("s", (Field("c", FType.CHAR, 0),)))
    with pytest.raises(ValueError):
        RecordCodec(Schema("s", (Field("v", FType.VARCHAR, 0xFFFF),)))  # 0xFFFF é o NULL
    with pytest.raises(ValueError):
        RecordCodec(Schema("s", (Field("a", "INT"),)))                  # ftype em string (JSON cru)


# ------------------------------------------------------------------ dados corrompidos (N1)
def test_codec_corrupt():
    c = RecordCodec(ALL)
    good = c.encode(ROW)
    cases = [good[:n] for n in range(len(good))]             # todo truncamento
    cases.append(good + b"\x00")                              # byte sobrando
    fixed = 8 + 8 + 4 + 4 + 8 + 8 + 4 + 3                     # início do 1º variável (v)
    cases.append(good[:fixed] + struct.pack("<H", 11) + good[fixed + 2:])     # VARCHAR > size
    cases.append(good[:fixed] + struct.pack("<H", 900) + good[fixed + 2:])    # tamanho fora do registro
    lpos = fixed + 2 + len("olá".encode())                    # LIST l: u16 total, 1+1, 1+2
    cases.append(good[:lpos] + struct.pack("<H", 5) + b"\x09x\x02yy" + good[lpos + 7:])   # item maior que size
    cases.append(good[:lpos] + struct.pack("<H", 5) + b"\x01x\x06yy" + good[lpos + 7:])   # idem
    cases.append(good[:fixed] + b"\x02\x00\xff\xfe" + good[fixed + 4:])       # utf-8 inválido
    cases.append(struct.pack("<q", -2**63) + good[8:])        # NULL em INT obrigatório
    cases.append(good[:20] + b"\xff" + good[21:])             # NULL em CHAR obrigatório
    cases.append(good[:20] + b"a\x00b\x00" + good[24:])       # CHAR com \x00 no meio
    cases.append(good[:16] + struct.pack("<i", 2**31 - 1) + good[20:])    # DATE além de date.max
    for bad in cases:
        with pytest.raises(ValueError, match="registro corrompido"):
            c.decode(bad)
    many = Schema("s", (Field("l", FType.LIST, 1),))
    over = struct.pack("<H", 2 * (MAX_LIST + 1)) + b"\x01a" * (MAX_LIST + 1)
    with pytest.raises(ValueError):
        RecordCodec(many).decode(over)                        # mais de MAX_LIST itens


# Casos de um defeito só cada (achados pelo revisor de testes com mutação, ver ai/P2/review.md).
S2 = Schema("s", (Field("l", FType.LIST, 5), Field("v", FType.VARCHAR, 3)))


def test_decode_list_item_crossing_field_end_is_corrupt():
    # l declara 2 bytes, mas o item diz ter 3 (≤ size 5): sem checar o fim do campo, o byte que
    # falta seria lido de v (o "lixo do vizinho" do enunciado §7.1).
    good = RecordCodec(S2).encode((("a",), "xy"))            # 02 00 | 01 61 | 02 00 | 78 79
    bad = struct.pack("<H", 2) + b"\x03a" + good[4:]
    with pytest.raises(ValueError, match="registro corrompido"):
        RecordCodec(S2).decode(bad)


def test_decode_varchar_longer_than_size_is_corrupt():
    s = Schema("s", (Field("v", FType.VARCHAR, 3),))            # registro coerente, só len > size
    with pytest.raises(ValueError, match="registro corrompido"):
        RecordCodec(s).decode(struct.pack("<H", 4) + b"abcd")


@pytest.mark.parametrize("ftype", [FType.VARCHAR, FType.LIST])
def test_null_in_required_variable_field(ftype):
    c = RecordCodec(Schema("s", (Field("v", ftype, 3),)))
    with pytest.raises(ValueError):
        c.encode((None,))
    with pytest.raises(ValueError, match="registro corrompido"):
        c.decode(b"\xff\xff")


@pytest.mark.parametrize("ftype,v", [(FType.INT, 1.5), (FType.INT, "1"), (FType.FLOAT, "1.5"),
                                     (FType.CHAR, 5), (FType.VARCHAR, b"ab"),
                                     (FType.INT, True), (FType.FLOAT, False),     # bool é subclasse de int
                                     (FType.FLOAT, 2**53 + 1)])    # o double arredondaria para 2⁵³
def test_wrong_python_type_is_value_error(ftype, v):
    with pytest.raises(ValueError):
        RecordCodec(Schema("s", (Field("x", ftype, 4),))).encode((v,))
    with pytest.raises(ValueError):
        encode_key(Field("x", ftype, 4), v)


def test_decode_fuzz_only_value_error():
    # Critério da #39: bytes corrompidos dão ValueError, nunca IndexError, struct.error ou lixo.
    # Qualquer outra exceção derruba o teste.
    c, rng = RecordCodec(ALL), random.Random(39)
    good = c.encode(ROW)
    for _ in range(20_000):
        b = bytearray(good)
        for _ in range(rng.randint(1, 4)):
            b[rng.randrange(len(b))] = rng.randrange(256)
        try:
            c.decode(bytes(b[:rng.randint(0, len(b))]))
        except ValueError:
            pass


def test_random_records_roundtrip():
    # N5 leve: 3 000 registros aleatórios (seed fixa) com todos os tipos e nulos
    rng = random.Random(39)
    c = RecordCodec(ALL)
    alphabet = "aé€𝄞z|0 "

    def text(n):
        s = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, n)))
        while len(s.encode()) > n:
            s = s[:-1]
        return s

    def value(f):
        if f.nullable and rng.random() < 0.2:
            return None
        if f.ftype is FType.INT:
            return rng.randint(-2**63 + 1, 2**63 - 1)
        if f.ftype is FType.FLOAT:
            return _rand_float(rng)
        if f.ftype is FType.DATE:
            return date.fromordinal(rng.randint(1, date.max.toordinal()))
        if f.ftype is FType.LIST:
            return tuple(text(f.size) for _ in range(rng.randint(0, MAX_LIST)))
        return text(f.size).rstrip("\x00")

    for _ in range(3000):
        rec = tuple(value(f) for f in ALL.fields)
        assert c.decode(c.encode(rec)) == rec


# ------------------------------------------------------------------ base real (N3)
@pytest.mark.parametrize("table", sorted(BASE_COUNTS))
def test_generated_base_roundtrips(table):
    # Todas as tabelas do gerador, escala 0,01: cada registro volta igual e cabe em max_size.
    c = RecordCodec(SCHEMAS[table])
    for rec in Dataset(scale=0.01).records(table):
        b = c.encode(rec)
        assert len(b) <= c.max_size
        assert c.decode(b) == tuple(rec)


def _heap_blocks(sizes, block_size):
    # Modelo do heap de slotted pages (guia P2 §3): cabeçalho de HEADER B e 4 B de slot por registro,
    # enchendo cada bloco na ordem de inserção.
    blocks, free = 0, 0
    for s in sizes:
        if s + 4 > free:
            blocks, free = blocks + 1, block_size - SlottedPage.HEADER
        free -= s + 4
    return blocks


def test_record_sizes_give_the_block_counts_of_the_decisions():
    # N4: os cartões D-P2-01 a D-P2-04 (e o PR #91) estimaram, em escala 1 e B = 4096, com o
    # cabeçalho de 28 B, aluno = 1 830 blocos e inscricao = 4 425. D-P2-07 revista (sem flags e
    # sem USER, cabeçalho de 10 B): aluno 3 726 / 1 815 / 899 em 2/4/8 KB.
    # inscricao: 4 campos fixos de 8 B, sem bitmap = 32 B; ⌊4 086 / 36⌋ = 113 por bloco;
    # ⌈500 000 / 113⌉ = 4 425 (conferimos o tamanho numa amostra, não os 500 mil).
    ins = RecordCodec(SCHEMAS["inscricao"])
    assert {len(ins.encode(r)) for r in Dataset(scale=0.01).records("inscricao")} == {32}
    # aluno: tamanho variável, 50 000 registros (~0,5 s).
    alu = RecordCodec(SCHEMAS["aluno"])
    sizes = [len(alu.encode(r)) for r in Dataset().records("aluno")]
    assert [_heap_blocks(sizes, b) for b in (2048, 4096, 8192)] == [3726, 1815, 899]


# ------------------------------------------------------------------ chaves (D-P2-05)
def test_key_size():
    assert [key_size(Field("x", t, 7)) for t in (FType.INT, FType.FLOAT, FType.DATE, FType.CHAR,
                                                  FType.VARCHAR)] == [8, 8, 4, 7, 7]
    with pytest.raises(TypeError):
        key_size(Field("l", FType.LIST, 5))
    for t in (FType.CHAR, FType.VARCHAR):              # chave de 0 bytes: o RecordCodec também recusa
        with pytest.raises(ValueError):
            key_size(Field("x", t, 0))
        with pytest.raises(ValueError):
            encode_key(Field("x", t, 0), "")


def _cmp(a, b):
    return (a > b) - (a < b)


def _rand_float(rng):
    special = [0.0, -0.0, math.inf, -math.inf, 5e-324, -5e-324, 1.0, -1.0, 6.57, -6.57]
    if rng.random() < 0.2:
        return rng.choice(special)
    x = struct.unpack("<d", rng.getrandbits(64).to_bytes(8, "little"))[0]
    return rng.uniform(-10, 10) if math.isnan(x) else x


def _rand_int(rng):
    return rng.choice([-2**63 + 1, 2**63 - 1, -1, 0, 1, 255, 256, rng.randint(-2**63 + 1, 2**63 - 1),
                       rng.randint(-1000, 1000)])


def _rand_text(rng):
    return "".join(rng.choice("abAé€\x01z") for _ in range(rng.randint(0, 3)))


@pytest.mark.parametrize("ftype,gen", [
    (FType.INT, _rand_int), (FType.FLOAT, _rand_float),
    (FType.DATE, lambda r: date.fromordinal(r.randint(1, date.max.toordinal()))),
    (FType.CHAR, _rand_text), (FType.VARCHAR, _rand_text),
])
def test_key_order(ftype, gen):
    # a < b ⇔ encode_key(a) < encode_key(b) (e a = b ⇔ bytes iguais) em 10 mil pares, seed fixa.
    # Para texto, a ordem esperada é a dos code points, que é a ordem dos bytes utf-8.
    f = Field("k", ftype, 9)
    rng = random.Random(5)
    for _ in range(10_000):
        a, b = gen(rng), gen(rng)
        ka, kb = encode_key(f, a), encode_key(f, b)
        assert len(ka) == len(kb) == key_size(f)
        assert _cmp(ka, kb) == _cmp(a, b), (a, b, ka.hex(), kb.hex())


def test_key_order_known_traps():
    i, fl = Field("k", FType.INT), Field("k", FType.FLOAT)
    assert encode_key(i, -1) < encode_key(i, 0) < encode_key(i, 1) < encode_key(i, 256)
    assert encode_key(fl, -math.inf) < encode_key(fl, -2.5) < encode_key(fl, -0.0) \
        == encode_key(fl, 0.0) < encode_key(fl, 5e-324) < encode_key(fl, 6.57) < encode_key(fl, math.inf)
    c = Field("k", FType.CHAR, 3)
    assert encode_key(c, "a") < encode_key(c, "ab") < encode_key(c, "b") < encode_key(c, "é")


def test_composite_key_with_seq_keeps_order():
    # Uso do P3: encode_key(k) + seq em 8 bytes big-endian = ordem lexicográfica de (k, seq).
    f = Field("k", FType.FLOAT)
    pairs = [(-1.0, 9), (-1.0, 10), (0.0, 0), (0.0, 2**40), (6.5, 1), (6.57, 0)]
    keys = [encode_key(f, k) + s.to_bytes(8, "big") for k, s in pairs]
    assert keys == sorted(keys)


@pytest.mark.parametrize("ftype,values", [
    (FType.INT, [-2**63 + 1, -1, 0, 50_000, 2**63 - 1]),
    (FType.FLOAT, [-math.inf, -6.57, 5e-324, 0.0, 9.99, math.inf]),
    (FType.DATE, [date.min, date(2026, 11, 23), date.max]),
    (FType.CHAR, ["", "a", "ab€", "abcdefghi"]),
    (FType.VARCHAR, ["", "zé"]),
])
def test_decode_key_roundtrip(ftype, values):
    f = Field("k", ftype, 9)
    for v in values:
        assert decode_key(f, encode_key(f, v)) == v
        assert decode_key(f, memoryview(encode_key(f, v))) == v
    assert decode_key(Field("k", FType.FLOAT), encode_key(Field("k", FType.FLOAT), -0.0)) == 0.0


@pytest.mark.parametrize("ftype,v", [
    (FType.INT, None), (FType.INT, -2**63), (FType.INT, 2**63), (FType.FLOAT, math.nan),
    (FType.FLOAT, None), (FType.DATE, None), (FType.CHAR, None), (FType.CHAR, "a\x00"),
    (FType.CHAR, "x" * 10), (FType.VARCHAR, "a\x00"), (FType.FLOAT, 10**400),
    (FType.DATE, datetime(2026, 1, 1, 8)),
])
def test_encode_key_rejects_null_sentinels_and_bad_values(ftype, v):
    # D-P2-05 (N1): chave nunca é NULL; sentinelas e valores fora do tipo levantam ValueError
    with pytest.raises(ValueError):
        encode_key(Field("k", ftype, 9), v)


@pytest.mark.parametrize("ftype,b", [
    (FType.INT, b"\x00" * 8),             # −2⁶³: a sentinela de NULL
    (FType.FLOAT, b"\xff" * 8),           # NaN (bit de sinal ligado na chave = positivo)
    (FType.FLOAT, b"\x00" * 8),           # NaN (todos os bits invertidos = negativo)
    (FType.FLOAT, b"\x7f" + b"\xff" * 7), # −0,0 (o encode_key normaliza para 0,0)
    (FType.DATE, b"\x00" * 4),            # ordinal 0: a sentinela de NULL
    (FType.DATE, b"\xff" * 4),            # além de date.max
    (FType.CHAR, b"a\x00b"),              # \x00 no meio (o encode_key nunca grava)
    (FType.VARCHAR, b"\xff\x00\x00"),     # utf-8 inválido
])
def test_decode_key_rejects_bytes_encode_key_never_writes(ftype, b):
    # Mesmo padrão do decode do registro: bytes que encode_key não gera são "registro corrompido".
    with pytest.raises(ValueError, match="registro corrompido"):
        decode_key(Field("k", ftype, 3), b)


def test_decode_key_rejects_wrong_length():
    with pytest.raises(ValueError):
        decode_key(Field("k", FType.INT), b"\x00" * 7)
    with pytest.raises(TypeError):
        encode_key(Field("l", FType.LIST, 5), ("a",))
