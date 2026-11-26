"""Testes da slotted page (P2, #40). Decisões: D-P2-07 (formato), D-P2-08 (apagados e compactação).

N1: a página sobre um bytearray solto. N2: a página dentro do buffer pool real, sobrevivendo ao disco.
"""
import random

import pytest

from cbd.contracts import PageType
from cbd.records.slotted_page import SlottedPage

B = 4096


def page(block_size=B):
    sp = SlottedPage(bytearray(block_size))
    sp.init()
    return sp


def rec(i, n):
    # Registro de n bytes reconhecível pelo i (para conferir que nada trocou de lugar).
    return bytes((i * 7 + j) % 251 for j in range(n))


def live(sp):
    return {s: bytes(sp.get(s)) for s in range(sp.n_slots()) if sp.get(s) is not None}


# ---------- formato (D-P2-07) ----------

def test_constants():
    # Cabeçalho de 28 B (n_slots, free_end, holes, flags, next, USER de 16 B) e 4 B por slot.
    assert SlottedPage.HEADER == 28 and SlottedPage.USER == 16
    assert SlottedPage.max_record(2048) == 2016
    assert SlottedPage.max_record(4096) == 4064
    assert SlottedPage.max_record(8192) == 8160


def test_empty_page():
    sp = page()
    assert sp.n_slots() == 0
    assert sp.free_space() == SlottedPage.max_record(B)


@pytest.mark.parametrize("block_size", [2048, 4096, 8192])
@pytest.mark.parametrize("n", [1, 32, 145, 1000])
def test_fill_until_full(block_size, n):
    # D-P2-07: cabem exatamente ⌊(B − 28) / (n + 4)⌋ registros de n bytes. Ex.: B = 4096, n = 145
    # (um aluno) → ⌊4068 / 149⌋ = 27; n = 32 (inscricao) → ⌊4068 / 36⌋ = 113.
    sp = page(block_size)
    k = 0
    while (s := sp.insert(rec(k, n))) is not None:
        assert s == k
        k += 1
    assert k == (block_size - 28) // (n + 4)
    assert sp.free_space() < n
    assert live(sp) == {i: rec(i, n) for i in range(k)}


def test_max_record_fits_and_one_more_does_not():
    sp = page()
    assert sp.insert(b"x" * (SlottedPage.max_record(B) + 1)) is None
    assert sp.n_slots() == 0
    assert sp.insert(b"x" * SlottedPage.max_record(B)) == 0
    assert sp.free_space() == 0


def test_empty_record():
    sp = page()
    s = sp.insert(b"")
    assert bytes(sp.get(s)) == b""
    assert sp.free_space() == SlottedPage.max_record(B) - 4


def test_layout_bytes():
    # Diretório no início (depois dos 28 B), registros encostados no fim (SKS Fig. 13.6).
    sp = page()
    sp.insert(b"AAA")
    sp.insert(b"BB")
    buf = sp.buf
    assert int.from_bytes(buf[0:2], "little") == 2          # n_slots
    assert int.from_bytes(buf[2:4], "little") == B - 5      # free_end
    assert buf[B - 3:] == b"AAA" and buf[B - 5:B - 3] == b"BB"
    assert int.from_bytes(buf[28:30], "little") == B - 3    # slot 0: offset
    assert int.from_bytes(buf[30:32], "little") == 3        # slot 0: len


# ---------- apagar e reaproveitar (D-P2-08) ----------

def test_delete_keeps_slot_and_rid_of_others():
    sp = page()
    for i in range(5):
        sp.insert(rec(i, 100))
    sp.delete(2)
    assert sp.get(2) is None
    assert sp.n_slots() == 5
    assert live(sp) == {i: rec(i, 100) for i in (0, 1, 3, 4)}


def test_delete_does_not_move_bytes():
    # D-P2-08: apagar não move nada, então uma view obtida antes continua certa.
    sp = page()
    for i in range(5):
        sp.insert(rec(i, 100))
    views = {i: sp.get(i) for i in (0, 1, 3, 4)}
    before = bytes(sp.buf[28 + 4 * 5:])
    sp.delete(2)
    assert all(bytes(views[i]) == rec(i, 100) for i in views)
    assert bytes(sp.buf[28 + 4 * 5:]) == before


def test_delete_twice_or_out_of_range_fails():
    sp = page()
    sp.insert(b"a")
    sp.delete(0)
    with pytest.raises(AssertionError):
        sp.delete(0)
    with pytest.raises(AssertionError):
        sp.delete(1)
    with pytest.raises(AssertionError):
        sp.get(1)


def test_insert_reuses_smallest_deleted_slot():
    sp = page()
    for i in range(5):
        sp.insert(rec(i, 50))
    sp.delete(3)
    sp.delete(1)
    assert sp.insert(b"novo") == 1
    assert sp.insert(b"outro") == 3
    assert sp.insert(b"fim") == 5
    assert sp.n_slots() == 6


def test_free_space_without_and_with_reusable_slot():
    # Sem slot apagado: o registro novo precisa de mais 4 B de diretório.
    sp = page()
    sp.insert(rec(0, 100))
    assert sp.free_space() == B - 28 - 4 - 100 - 4
    # Com slot apagado: o 4 B não é descontado, e os 100 B apagados contam (compacta se precisar).
    sp.delete(0)
    assert sp.free_space() == B - 28 - 4
    assert sp.insert(b"z" * (B - 28 - 4)) == 0


@pytest.mark.parametrize("seed", range(20))
def test_free_space_is_exact(seed):
    # free_space() é o maior registro que insert aceita agora: f entra, f + 1 não.
    r = random.Random(seed)
    sp = page()
    while sp.insert(rec(r.randrange(100), r.randint(1, 300))) is not None:
        pass
    for s in r.sample(range(sp.n_slots()), sp.n_slots() // 3):
        sp.delete(s)
    f = sp.free_space()
    probe = SlottedPage(bytearray(sp.buf))
    assert probe.insert(b"y" * (f + 1)) is None
    assert sp.insert(b"y" * f) is not None


def test_insert_compacts_when_only_holes_fit():
    # Página cheia de 27 registros de 145 B (sobram 45 B contínuos); apaga 3 não vizinhos
    # (435 B de buraco) e insere 400 B: só cabe compactando. Os outros slots mantêm número e conteúdo.
    sp = page()
    for i in range(27):
        sp.insert(rec(i, 145))
    for s in (3, 10, 20):
        sp.delete(s)
    assert sp.free_space() >= 400
    s = sp.insert(rec(99, 400))
    assert s == 3
    expect = {i: rec(i, 145) for i in range(27) if i not in (3, 10, 20)}
    expect[3] = rec(99, 400)
    assert live(sp) == expect


def test_insert_that_does_not_fit_leaves_page_untouched():
    sp = page()
    for i in range(27):
        sp.insert(rec(i, 145))
    sp.delete(5)
    before = bytes(sp.buf)
    assert sp.insert(rec(1, 300)) is None
    assert bytes(sp.buf) == before


def test_compact_keeps_slots_and_free_space():
    sp = page()
    for i in range(10):
        sp.insert(rec(i, 120))
    for s in (0, 4, 9):
        sp.delete(s)
    before, free = live(sp), sp.free_space()
    sp.compact()
    assert live(sp) == before
    assert sp.free_space() == free
    assert int.from_bytes(sp.buf[4:6], "little") == 0          # holes
    sp.compact()                                               # idempotente
    assert live(sp) == before


# ---------- ordem lógica para as folhas da B+ (P3) ----------

def test_insert_at_keeps_logical_order():
    sp = page()
    for w in (b"b", b"d"):
        assert sp.insert_at(sp.n_slots(), w)
    assert sp.insert_at(0, b"a")
    assert sp.insert_at(2, b"c")
    assert sp.insert_at(4, b"e")
    assert [bytes(sp.get(i)) for i in range(sp.n_slots())] == [b"a", b"b", b"c", b"d", b"e"]
    with pytest.raises(AssertionError):
        sp.insert_at(6, b"x")


def test_insert_at_full_returns_false_and_leaves_page_untouched():
    sp = page()
    while sp.insert_at(0, rec(1, 200)):
        pass
    before = bytes(sp.buf)
    assert sp.insert_at(0, rec(2, 200)) is False
    assert bytes(sp.buf) == before


def test_remove_at_shifts_and_does_not_move_bytes():
    sp = page()
    for w in (b"aa", b"bb", b"cc", b"dd"):
        sp.insert_at(sp.n_slots(), w)
    keep = [sp.get(i) for i in (0, 2, 3)]
    sp.remove_at(1)
    assert sp.n_slots() == 3
    assert [bytes(sp.get(i)) for i in range(3)] == [b"aa", b"cc", b"dd"]
    assert [bytes(v) for v in keep] == [b"aa", b"cc", b"dd"]   # D-P2-08: views antigas continuam certas
    # Sem slot apagado na folha: free_space desconta sempre os 4 B do slot novo; o buraco de 2 B conta.
    assert sp.free_space() == B - 28 - 4 * 3 - 8 - 4 + 2
    with pytest.raises(AssertionError):
        sp.remove_at(3)


def test_split_pattern_of_the_btree():
    # O padrão do split do P3: pega a metade de cima, remove uma a uma e copia para a folha nova.
    # Funciona com views (sem bytes(...)) porque remove_at não move registros (D-P2-08).
    old, new = page(), page()
    for i in range(20):
        old.insert_at(i, rec(i, 150))
    upper = [old.get(i) for i in range(10, 20)]
    for i in reversed(range(10, 20)):
        old.remove_at(i)
    for j, v in enumerate(upper):
        assert new.insert_at(j, v)
    assert [bytes(new.get(j)) for j in range(10)] == [rec(i, 150) for i in range(10, 20)]
    assert [bytes(old.get(j)) for j in range(10)] == [rec(i, 150) for i in range(10)]


def test_insert_at_compacts_after_removes():
    sp = page()
    while sp.insert_at(sp.n_slots(), rec(sp.n_slots(), 145)):
        pass
    n = sp.n_slots()
    for _ in range(3):
        sp.remove_at(0)
    assert sp.insert_at(0, rec(77, 400))
    assert bytes(sp.get(0)) == rec(77, 400)
    assert [bytes(sp.get(i)) for i in range(1, sp.n_slots())] == [rec(i, 145) for i in range(3, n)]


# ---------- cabeçalho do usuário e next ----------

def test_user_header_and_next_survive_inserts_and_compaction():
    sp = page()
    uh = sp.user_header()
    assert len(uh) == SlottedPage.USER and bytes(uh) == bytes(16)
    uh[:] = bytes(range(1, 17))
    assert sp.next == 0
    sp.next = 123456
    for i in range(27):
        sp.insert(rec(i, 145))
    sp.delete(0)
    sp.compact()
    assert bytes(sp.user_header()) == bytes(range(1, 17))
    assert sp.next == 123456


def test_init_clears_previous_content():
    buf = bytearray(b"\xff" * B)
    sp = SlottedPage(buf)
    sp.init()
    assert sp.n_slots() == 0 and sp.next == 0 and bytes(sp.user_header()) == bytes(16)
    assert sp.free_space() == SlottedPage.max_record(B)


# ---------- página corrompida ----------

def test_corrupt_directory_is_detected():
    sp = page()
    sp.insert(b"abc")
    sp.buf[28:30] = (B - 1).to_bytes(2, "little")     # offset + len passa do fim do bloco
    with pytest.raises(AssertionError):
        sp.get(0)
    sp2 = page()
    sp2.buf[0:2] = (2000).to_bytes(2, "little")        # n_slots invade a área de registros
    with pytest.raises(AssertionError):
        sp2.free_space()


# ---------- estresse pequeno contra um dict ----------

@pytest.mark.parametrize("seed", range(5))
def test_random_ops_match_dict_oracle(seed):
    r = random.Random(seed)
    sp, oracle = page(), {}
    for _ in range(3000):
        op = r.random()
        if op < 0.55:
            data = rec(r.randrange(1000), r.randint(0, 400))
            f = sp.free_space()
            s = sp.insert(data)
            assert (s is not None) == (len(data) <= f)
            if s is not None:
                assert s not in oracle
                oracle[s] = data
        elif op < 0.9 and oracle:
            s = r.choice(sorted(oracle))
            sp.delete(s)
            del oracle[s]
        else:
            sp.compact()
        assert live(sp) == oracle


# ---------- N2: dentro do buffer pool real ----------

def test_page_survives_the_disk(bp):
    # A slotted page opera sobre page.data in-place e não marca dirty: quem chama marca.
    # Depois de clear() (grava e esvazia o buffer), reler o bloco custa 1 leitura e traz o mesmo conteúdo.
    fid = bp.fm.create("sp")
    p = bp.new_page(fid, PageType.DATA)
    sp = SlottedPage(p.data)
    sp.init()
    for i in range(5):
        sp.insert(rec(i, 100))
    sp.delete(1)
    sp.next = 7
    bno = p.bno
    bp.unpin(p, dirty=True)
    bp.clear()
    bp.fm.reset_stats()
    p = bp.pin(fid, bno, PageType.DATA)
    sp = SlottedPage(p.data)
    assert live(sp) == {i: rec(i, 100) for i in (0, 2, 3, 4)}
    assert sp.next == 7
    bp.unpin(p)
    assert bp.fm.io.reads == 1 and bp.fm.io.writes == 0
