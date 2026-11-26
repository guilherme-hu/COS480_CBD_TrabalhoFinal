"""Testes da slotted page (P2, #40). Decisões: D-P2-07 (formato), D-P2-08 (apagados e compactação).

N1: a página sobre um bytearray solto. N2: a página dentro do buffer pool real, sobrevivendo ao disco.
"""
import inspect
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
    # Cabeçalho de 10 B (n_slots, free_end, holes, next) e 4 B por slot. D-P2-07 revista: sem flags
    # e sem USER; a folha da B+ encadeia pelo next da página.
    assert SlottedPage.HEADER == 10 and not hasattr(SlottedPage, "USER")
    assert SlottedPage.max_record(2048) == 2034
    assert SlottedPage.max_record(4096) == 4082
    assert SlottedPage.max_record(8192) == 8178


def test_empty_page():
    sp = page()
    assert sp.n_slots() == 0
    assert sp.free_space() == SlottedPage.max_record(B)


@pytest.mark.parametrize("block_size", [2048, 4096, 8192])
@pytest.mark.parametrize("n", [1, 32, 145, 1000])
def test_fill_until_full(block_size, n):
    # D-P2-07: cabem exatamente ⌊(B − 10) / (n + 4)⌋ registros de n bytes. Ex.: B = 4096, n = 145
    # (um aluno) → ⌊4086 / 149⌋ = 27; n = 32 (inscricao) → ⌊4086 / 36⌋ = 113.
    sp = page(block_size)
    k = 0
    while (s := sp.insert(rec(k, n))) is not None:
        assert s == k
        k += 1
    assert k == (block_size - 10) // (n + 4)
    left = (block_size - 10) - k * (n + 4)       # sobra contínua; o slot novo ainda custaria 4 B
    assert sp.free_space() == max(0, left - 4)
    assert live(sp) == {i: rec(i, n) for i in range(k)}


def test_max_record_fits_and_one_more_does_not():
    sp = page()
    assert sp.insert(b"x" * (SlottedPage.max_record(B) + 1)) is None
    assert sp.n_slots() == 0
    assert sp.insert(b"x" * SlottedPage.max_record(B)) == 0
    assert sp.free_space() == 0


def test_empty_record_is_rejected():
    # Registro de 0 B é recusado: assim free_space() == 0 quer dizer "não cabe nada", sem exceção.
    # Nenhum produtor gera 0 B (o codec grava ao menos a parte fixa; B+ e índices, chave + ponteiro).
    sp = page()
    for call in (lambda: sp.insert(b""), lambda: sp.insert_at(0, b"")):
        with pytest.raises(AssertionError):
            call()
    assert sp.n_slots() == 0


def test_free_space_zero_means_nothing_fits():
    sp = page()
    sp.insert(b"x" * (SlottedPage.max_record(B) - 2))   # sobram 2 B: nem o slot novo cabe
    assert sp.free_space() == 0
    assert sp.insert(b"y") is None and sp.insert_at(0, b"y") is False


def test_layout_bytes():
    # Diretório no início (depois dos 10 B), registros encostados no fim (SKS Fig. 13.6).
    sp = page()
    sp.insert(b"AAA")
    sp.insert(b"BB")
    buf = sp.buf
    assert int.from_bytes(buf[0:2], "little") == 2          # n_slots
    assert int.from_bytes(buf[2:4], "little") == B - 5      # free_end
    assert buf[B - 3:] == b"AAA" and buf[B - 5:B - 3] == b"BB"
    assert int.from_bytes(buf[10:12], "little") == B - 3    # slot 0: offset
    assert int.from_bytes(buf[12:14], "little") == 3        # slot 0: len


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
    before = bytes(sp.buf[10 + 4 * 5:])
    sp.delete(2)
    assert all(bytes(views[i]) == rec(i, 100) for i in views)
    assert bytes(sp.buf[10 + 4 * 5:]) == before


def test_delete_last_slot_keeps_it_reserved():
    # D-P2-08: o número do slot apagado continua reservado, mesmo o último (get dá None, não erro).
    # LIMITAÇÃO aceita: os 4 B de diretório dele só voltam quando um insert reaproveita o slot.
    sp = page()
    for i in range(3):
        sp.insert(rec(i, 50))
    sp.delete(2)
    assert sp.n_slots() == 3 and sp.get(2) is None


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
    assert sp.free_space() == B - 10 - 4 - 100 - 4
    # Com slot apagado: o 4 B não é descontado, e os 100 B apagados contam (compacta se precisar).
    sp.delete(0)
    assert sp.free_space() == B - 10 - 4
    assert sp.insert(b"z" * (B - 10 - 4)) == 0


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
    # Página cheia de 27 registros de 145 B (sobram 63 B contínuos); apaga 3 não vizinhos
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
    assert sp.free_space() == B - 10 - 4 * 3 - 8 - 4 + 2
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


# ---------- next: overflow do hash e cadeia das folhas da B+ ----------

def test_next_survives_inserts_and_compaction():
    sp = page()
    assert sp.next == 0
    sp.next = 123456
    for i in range(27):
        sp.insert(rec(i, 145))
    sp.delete(0)
    sp.compact()
    assert sp.next == 123456


def test_leaf_chain_with_page_next_only(bp):
    # D-P2-07 revista: a folha da B+ encadeia só pelo next da página, como o ponteiro Pn do SKS
    # (§14.3.1, p. 635); sem prev, sem USER. Ciclo do P3 sobre o buffer pool real: carga de 3
    # folhas encadeadas, split da do meio (nova folha à direita) e fusão de volta. Em cada passo,
    # o scan da 1ª folha seguindo next até 0 vê todas as chaves em ordem, e só as 2 folhas do
    # split ou da fusão são alteradas (nenhuma vizinha precisa de um prev acertado).
    fid = bp.fm.create("leaves")

    def new_leaf():
        p = bp.new_page(fid, PageType.DATA)
        SlottedPage(p.data).init()
        return p

    def scan(bno):
        out, leaves = [], 0
        while bno:                                  # next = 0: última folha
            p = bp.pin(fid, bno, PageType.DATA)
            sp = SlottedPage(p.data)
            out += [bytes(sp.get(i)) for i in range(sp.n_slots())]
            bno, leaves = sp.next, leaves + 1
            bp.unpin(p)
        return out, leaves

    keys = [i.to_bytes(8, "big") for i in range(30)]
    pages = [new_leaf() for _ in range(3)]
    assert all(p.bno != 0 for p in pages)          # 0 nunca é uma folha: serve de "sem próxima"
    for j, p in enumerate(pages):
        sp = SlottedPage(p.data)
        for k in keys[10 * j:10 * j + 10]:
            assert sp.insert_at(sp.n_slots(), k)
        sp.next = pages[j + 1].bno if j < 2 else 0
    first = pages[0].bno
    for p in pages:
        bp.unpin(p, dirty=True)
    assert scan(first) == (keys, 3)

    # split: a metade de cima da folha do meio vai para uma folha nova, ligada à direita dela
    p_mid, p_new = bp.pin(fid, pages[1].bno, PageType.DATA), new_leaf()
    mid, new = SlottedPage(p_mid.data), SlottedPage(p_new.data)
    upper = [bytes(mid.get(i)) for i in range(5, 10)]
    for i in reversed(range(5, 10)):
        mid.remove_at(i)
    for v in upper:
        assert new.insert_at(new.n_slots(), v)
    new.next, mid.next = mid.next, p_new.bno
    new_bno = p_new.bno
    bp.unpin(p_mid, dirty=True)
    bp.unpin(p_new, dirty=True)
    assert scan(first) == (keys, 4)

    # fusão: a folha da direita volta para a da esquerda, que herda o next dela
    p_mid, p_new = bp.pin(fid, pages[1].bno, PageType.DATA), bp.pin(fid, new_bno, PageType.DATA)
    mid, new = SlottedPage(p_mid.data), SlottedPage(p_new.data)
    for i in range(new.n_slots()):
        assert mid.insert_at(mid.n_slots(), new.get(i))
    mid.next = new.next
    bp.unpin(p_mid, dirty=True)
    bp.unpin(p_new)
    bp.free_block(fid, new_bno)
    assert scan(first) == (keys, 3)


def test_init_clears_previous_content():
    # Cabeçalho inteiro zerado (n_slots, holes, next), só free_end = B.
    sp = SlottedPage(bytearray(b"\xff" * B))
    sp.init()
    assert bytes(sp.buf[:10]) == bytes(2) + B.to_bytes(2, "little") + bytes(6)
    assert sp.free_space() == SlottedPage.max_record(B)


# ---------- página corrompida ----------

@pytest.mark.parametrize("pos, value, call", [
    (10, B - 1, lambda sp: sp.get(0)),          # offset + len passa do fim do bloco
    (10, 4, lambda sp: sp.get(0)),             # offset dentro do cabeçalho
    (0, 2000, lambda sp: sp.free_space()),      # n_slots invade a área de registros
    (0, 2000, lambda sp: sp.insert(b"a")),      # idem, pelo insert (antes dava struct.error)
    (4, 100, lambda sp: sp.free_space()),       # holes maior que a área de registros
], ids=["offset-fim", "offset-cabecalho", "n_slots", "n_slots-insert", "holes"])
def test_corrupt_header_is_detected(pos, value, call):
    sp = page()
    sp.insert(b"abc")
    sp.buf[pos:pos + 2] = value.to_bytes(2, "little")
    with pytest.raises(AssertionError):
        call(sp)


# ---------- estresse pequeno contra um dict ----------

@pytest.mark.parametrize("seed", range(5))
def test_random_ops_match_dict_oracle(seed):
    r = random.Random(seed)
    sp, oracle = page(), {}
    for _ in range(3000):
        op = r.random()
        if op < 0.55:
            data = rec(r.randrange(1000), r.randint(1, 400))
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
    bp.fm.reset_stats()
    bp.clear()
    assert bp.fm.io.writes == 1                       # a única página suja vai para o disco
    bp.fm.reset_stats()
    p = bp.pin(fid, bno, PageType.DATA)
    sp = SlottedPage(p.data)
    assert live(sp) == {i: rec(i, 100) for i in (0, 2, 3, 4)}
    assert sp.next == 7
    sp.insert(rec(9, 10))
    assert p.dirty is False                           # a SlottedPage não marca dirty: é de quem chama
    bp.unpin(p, dirty=True)                           # e quem chama marca (AGENTS regra 5)
    assert bp.fm.io.reads == 1 and bp.fm.io.writes == 0


# ---------- achados da revisão independente (ai/P2/review.md) ----------

def test_insert_at_with_a_view_of_the_same_page_that_compacts():
    # Uma view da própria página como data: se o insert_at compactar, os bytes precisam ter sido
    # copiados antes (o compact move o registro que a view enxerga).
    sp = page()
    while sp.insert_at(sp.n_slots(), rec(sp.n_slots(), 145)):
        pass
    v = sp.get(0)
    expected = bytes(v)
    sp.remove_at(0)
    sp.remove_at(0)
    assert sp.insert_at(0, v)
    assert bytes(sp.get(0)) == expected


def test_insert_with_a_view_of_the_same_page_that_compacts():
    sp = page()
    for i in range(27):
        sp.insert(rec(i, 145))
    v = sp.get(13)                    # no meio: o compact põe o slot 14 onde a view olha
    expected = bytes(v)
    sp.delete(0)
    sp.delete(26)
    s = sp.insert(v)
    assert bytes(sp.get(s)) == expected


def test_insert_at_free_space_is_exact():
    sp = page()
    sp.insert_at(0, rec(0, 100))
    f = sp.free_space()
    assert f == B - 10 - 4 - 100 - 4
    before = bytes(sp.buf)
    assert sp.insert_at(0, b"y" * (f + 1)) is False
    assert bytes(sp.buf) == before
    assert sp.insert_at(0, b"y" * f) is True
    assert bytes(sp.get(1)) == rec(0, 100)


@pytest.mark.parametrize("seed", range(10))
def test_random_insert_at_remove_at_match_list_oracle(seed):
    # Oráculo das folhas da B+: uma lista Python na mesma ordem lógica.
    r = random.Random(seed)
    sp, oracle = page(), []
    for _ in range(2000):
        if r.random() < 0.6:
            data = rec(r.randrange(1000), r.randint(1, 300))
            pos = r.randint(0, len(oracle))
            f = sp.free_space()
            ok = sp.insert_at(pos, data)
            assert ok == (len(data) <= f)
            if ok:
                oracle.insert(pos, data)
        elif oracle:
            pos = r.randrange(len(oracle))
            sp.remove_at(pos)
            del oracle[pos]
        assert [bytes(sp.get(i)) for i in range(sp.n_slots())] == oracle


def test_insert_that_fits_contiguously_does_not_compact():
    # D-P2-08: compacta só quando precisa; um insert que cabe no contínuo não mexe nos outros.
    sp = page()
    for i in range(5):
        sp.insert(rec(i, 100))
    sp.delete(1)
    before = bytes(sp.buf[B - 500:])
    assert sp.insert(b"zz") == 1
    assert bytes(sp.buf[B - 500:]) == before
    assert int.from_bytes(sp.buf[4:6], "little") == 100


def test_remove_at_of_deleted_slot_does_not_count_twice():
    sp = page()
    sp.insert(rec(0, 100))
    sp.insert(rec(1, 100))
    sp.delete(0)
    sp.remove_at(0)
    assert int.from_bytes(sp.buf[4:6], "little") == 100
    assert bytes(sp.get(0)) == rec(1, 100)
    f = sp.free_space()
    assert SlottedPage(bytearray(sp.buf)).insert(b"y" * (f + 1)) is None
    assert sp.insert(b"y" * f) is not None


def test_get_beyond_n_slots_after_remove_at_fails():
    # Depois de remove_at, a entrada antiga continua nos bytes do diretório; get não pode lê-la.
    sp = page()
    for w in (b"aa", b"bb", b"cc", b"dd"):
        sp.insert_at(sp.n_slots(), w)
    sp.remove_at(1)
    with pytest.raises(AssertionError):
        sp.get(3)


def test_negative_positions_are_rejected():
    sp = page()
    sp.insert_at(0, b"a")
    sp.next = 77
    before = bytes(sp.buf)
    for call in (lambda: sp.insert_at(-1, b"x"), lambda: sp.remove_at(-1),
                 lambda: sp.delete(-1), lambda: sp.get(-1)):
        with pytest.raises(AssertionError):
            call()
    assert bytes(sp.buf) == before


def test_insert_at_refuses_page_with_deleted_slot():
    # Com slot apagado, free_space() não desconta o slot novo (vale para o insert), mas o insert_at
    # sempre cria um: a regra "não misture delete com insert_at" é checada, não só documentada.
    sp = page()
    sp.insert(b"x" * 2000)
    sp.insert(b"y" * 1000)
    sp.delete(1)
    before = bytes(sp.buf)
    with pytest.raises(AssertionError):
        sp.insert_at(0, b"z")
    assert bytes(sp.buf) == before


def test_get_returns_a_view_into_the_page():
    sp = page()
    sp.insert(b"abc")
    v = sp.get(0)
    assert isinstance(v, memoryview)
    v[0] = ord("z")
    assert bytes(sp.get(0)) == b"zbc"


def test_block_larger_than_8192_is_rejected():
    with pytest.raises(AssertionError):
        SlottedPage(bytearray(16384))


def test_signatures_match_the_architecture():
    # Arquitetura §5.1, critério de aceite da #40.
    names = ("__init__", "init", "n_slots", "get", "insert", "insert_at", "delete",
             "remove_at", "free_space", "compact", "max_record")
    sig = {n: list(inspect.signature(getattr(SlottedPage, n)).parameters) for n in names}
    assert sig == {
        "__init__": ["self", "buf"], "init": ["self"], "n_slots": ["self"], "get": ["self", "slot"],
        "insert": ["self", "data"], "insert_at": ["self", "pos", "data"], "delete": ["self", "slot"],
        "remove_at": ["self", "pos"], "free_space": ["self"], "compact": ["self"],
        "max_record": ["block_size"]}
    assert isinstance(SlottedPage.__dict__["max_record"], staticmethod)
    assert isinstance(SlottedPage.__dict__["next"], property)
    # Sem user_header: código escrito pelo guia antigo falha alto, em vez de gravar no diretório.
    assert not hasattr(SlottedPage, "user_header")
