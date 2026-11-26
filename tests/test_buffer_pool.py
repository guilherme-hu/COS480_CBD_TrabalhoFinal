"""Testes unitários do Gerente de Buffer Pool (BufferPool — P1)."""
from __future__ import annotations

import pytest

from cbd.contracts import BufferConfig, BufferFullError, PageType, Policy, StorageConfig
from cbd.storage.buffer_pool import BufferPool
from cbd.storage.file_manager import FileManager


def test_hit_miss(bp):
    """Pin no mesmo bloco 2x dá 1 miss e 1 hit; hit_rate == 0.5."""
    fid = bp.fm.create("aluno")
    p = bp.new_page(fid, PageType.DATA)
    bno = p.bno
    p.data[0] = 42
    bp.unpin(p, dirty=True)
    bp.clear()
    bp.fm.reset_stats()
    bp.reset_stats()

    with bp.page(fid, bno, PageType.DATA) as p1:
        assert p1.data[0] == 42
    with bp.page(fid, bno, PageType.DATA) as p2:
        assert p2.data[0] == 42

    # Cálculo esperado: 1º pin em cache frio = 1 miss (1 leitura física); 2º pin no mesmo bno = 1 hit (0 leituras); hit_rate = 1/2 = 0.5
    assert bp.stats.requests == 2
    assert bp.stats.misses == 1
    assert bp.stats.hits == 1
    assert bp.stats.hit_rate == pytest.approx(0.5)
    assert bp.fm.io.reads == 1


def test_dirty_eviction(tmp_path):
    """Área com 2 frames: suja A, carrega B e C; A é gravado (dirty_evictions == 1); ao reler, conteúdo novo está lá."""
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    fid = fm.create("t")
    for b in (1, 2, 3):
        fm.allocate(fid)
        fm.write(fid, b, bytes([b]) * 4096)
    fm.reset_stats()

    pool = BufferPool(
        fm,
        BufferConfig(
            areas={PageType.CATALOG: 1, PageType.INDEX: 1, PageType.DATA: 2, PageType.WORK: 1},
            policy=Policy.LRU,
        ),
    )

    # Suja bloco 1
    with pool.page(fid, 1, PageType.DATA) as p1:
        p1.data[0] = 99
        p1.mark_dirty()

    # Carrega 2 e 3 -> expulsa 1 (que estava sujo)
    with pool.page(fid, 2, PageType.DATA):
        pass
    with pool.page(fid, 3, PageType.DATA):
        pass

    # Cálculo esperado: DATA tem 2 frames; carregar 1, 2 ocupa os 2 frames; carregar 3 expulsa 1 (sujo) -> 1 dirty_eviction, 1 write
    assert pool.stats.dirty_evictions == 1
    assert fm.io.writes == 1

    # Relê bloco 1 do disco
    with pool.page(fid, 1, PageType.DATA) as p1_reload:
        assert p1_reload.data[0] == 99

    pool.flush_all()
    fm.close_all()


def test_not_dirty_no_write(tmp_path):
    """Página alterada SEM mark_dirty é perdida na expulsão e não incrementa io.writes."""
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    fid = fm.create("t")
    for b in (1, 2):
        fm.allocate(fid)
        fm.write(fid, b, bytes([b]) * 4096)
    fm.reset_stats()

    pool = BufferPool(
        fm,
        BufferConfig(
            areas={PageType.CATALOG: 1, PageType.INDEX: 1, PageType.DATA: 1, PageType.WORK: 1},
            policy=Policy.LRU,
        ),
    )

    p1 = pool.pin(fid, 1, PageType.DATA)
    p1.data[0] = 250  # alterou in-place, mas esqueceu mark_dirty() e unpin(dirty=True)
    pool.unpin(p1, dirty=False)

    # Carrega bloco 2 -> expulsa bloco 1 sem gravar
    with pool.page(fid, 2, PageType.DATA):
        pass

    # Cálculo esperado: 1 expulsão na área DATA (1 frame), mas como dirty=False -> 0 dirty_evictions e 0 writes no disco
    assert pool.stats.dirty_evictions == 0
    assert fm.io.writes == 0

    # Ao reler bloco 1, a alteração sem dirty foi perdida (continua valendo 1)
    with pool.page(fid, 1, PageType.DATA) as p1_again:
        assert p1_again.data[0] == 1

    pool.flush_all()
    fm.close_all()


def test_buffer_full(tmp_path):
    """Todos os frames fixados -> BufferFullError (tanto em LRU quanto em MRU)."""
    for pol in (Policy.LRU, Policy.MRU):
        fm = FileManager(StorageConfig(root_dir=str(tmp_path / pol.value), block_size=4096))
        fid = fm.create("t")
        for b in (1, 2, 3):
            fm.allocate(fid)
            fm.write(fid, b, bytes([b]) * 4096)

        pool = BufferPool(
            fm,
            BufferConfig(
                areas={PageType.CATALOG: 1, PageType.INDEX: 1, PageType.DATA: 2, PageType.WORK: 1},
                policy=pol,
            ),
        )
        fm.reset_stats()
        p1 = pool.pin(fid, 1, PageType.DATA)
        p2 = pool.pin(fid, 2, PageType.DATA)
        # Cálculo esperado: DATA tem 2 frames; pins em 1 e 2 (frios) = 2 misses = 2 leituras.
        assert (pool.stats.misses, fm.io.reads, pool.pinned()) == (2, 2, 2)
        with pytest.raises(BufferFullError):
            pool.pin(fid, 3, PageType.DATA)
        # O pin que falhou não acha vítima (os 2 frames estão fixados), então não lê do disco
        # e não conta miss: continuam 2 misses e 2 leituras, e nenhum frame foi perdido.
        assert (pool.stats.misses, fm.io.reads, pool.pinned()) == (2, 2, 2)

        pool.unpin(p1)
        pool.unpin(p2)
        assert pool.pinned() == 0
        pool.flush_all()
        fm.close_all()


def test_toss(tmp_path):
    """Com toss_immediate=True, a página desafixada com toss=True é a próxima vítima."""
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    fid = fm.create("t")
    for b in (1, 2, 3):
        fm.allocate(fid)
        fm.write(fid, b, bytes([b]) * 4096)

    pool = BufferPool(
        fm,
        BufferConfig(
            areas={PageType.CATALOG: 1, PageType.INDEX: 1, PageType.DATA: 2, PageType.WORK: 1},
            policy=Policy.LRU,
            toss_immediate=True,
        ),
    )

    # Carrega 1 normal; carrega 2 (mais recente) mas desafixa com toss=True
    p1 = pool.pin(fid, 1, PageType.DATA)
    pool.unpin(p1, toss=False)

    p2 = pool.pin(fid, 2, PageType.DATA)
    pool.unpin(p2, toss=True)

    # Sem toss, o LRU expulsaria o 1 (mais antigo). Com toss=True no 2, a vítima deve ser o 2!
    with pool.page(fid, 3, PageType.DATA):
        pass

    residents = {f.bno for f in pool._areas[PageType.DATA].frames if f.fid == fid}
    assert residents == {1, 3}

    # Prova pelos contadores (sem olhar a estrutura interna):
    # pins em 1, 2, 3 = 3 misses. Se o toss funcionou, 1 ainda está no buffer (hit)
    # e 2 foi descartado (miss). Total: 5 pedidos, 1 hit, 4 misses = 4 leituras físicas.
    # Sem toss, o LRU teria expulsado o 1: o pin em 1 seria miss e o pin em 2 seria hit.
    fm.reset_stats()
    with pool.page(fid, 1, PageType.DATA):
        pass
    assert (pool.stats.hits, pool.stats.misses, fm.io.reads) == (1, 3, 0)
    with pool.page(fid, 2, PageType.DATA):
        pass
    assert (pool.stats.requests, pool.stats.hits, pool.stats.misses, fm.io.reads) == (5, 1, 4, 1)
    pool.flush_all()
    fm.close_all()


def test_prefetch(tmp_path):
    """prefetch=4 dentro de um cursor: um miss em bloco frio lê 5 blocos; as 4 pré-lidas ficam
    FIXADAS pelo cursor até ele usá-las; os 4 pins seguintes são hits; ao fim, pinned() == 0.

    D-P1-12 (revista em 23/11): antes as pré-lidas entravam sem pin (pinned() == 0 logo após o
    primeiro pin). Agora o cursor é dono delas (SKS §13.5.1.2), então pinned() == 4 enquanto ele
    não as usa. Teto: DATA = 8 e 1 cursor; o prefetch pode fixar enquanto sobrar >= 1 frame sem pin
    (8 − 1 do bloco pedido − 4 pré-lidas = 3 sobram), então o lote sai completo (4).
    """
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    fid = fm.create("scan_rel")
    for b in range(1, 10):
        fm.allocate(fid)
        fm.write(fid, b, bytes([b]) * 4096)
    fm.reset_stats()

    pool = BufferPool(
        fm,
        BufferConfig(
            areas={PageType.CATALOG: 1, PageType.INDEX: 1, PageType.DATA: 8, PageType.WORK: 1},
            policy=Policy.LRU,
            prefetch=4,
        ),
    )
    pool.allow_prefetch(fid, PageType.DATA)

    with pool.seq_scan(fid, PageType.DATA) as cur:
        with cur.page(1) as p1:
            assert p1.data[0] == 1
            assert pool.pinned() == 5  # o bloco 1 (em uso) + 4 pré-lidas do cursor

        # 1 miss + 4 prefetched = 5 leituras físicas (1 rand_read + 4 seq_reads)
        assert pool.stats.requests == 1
        assert pool.stats.misses == 1
        assert pool.stats.prefetched == 4
        assert fm.io.reads == 5
        assert fm.io.rand_reads == 1
        assert fm.io.seq_reads == 4
        assert pool.pinned() == 4  # as pré-lidas continuam fixadas pelo cursor dono

        # Os próximos 4 pins (blocos 2, 3, 4, 5) são hits: o cursor assume o pin de cada pré-lida
        for b in (2, 3, 4, 5):
            with cur.page(b) as p:
                assert p.data[0] == b
        assert pool.pinned() == 0  # usou todas; nada a soltar

    assert pool.stats.requests == 5
    assert pool.stats.hits == 4
    assert pool.stats.misses == 1
    assert fm.io.reads == 5
    assert pool.pinned() == 0
    # O CSV usa os contadores por área (bp.DATA.*): a pré-lida assumida conta lá também.
    st = pool.stats_by_area[PageType.DATA]
    assert (st.requests, st.hits, st.misses, st.prefetched) == (5, 4, 1, 4)
    pool.flush_all()
    fm.close_all()


@pytest.mark.parametrize("policy,data,lotes", [(Policy.LRU, 3, 10), (Policy.MRU, 8, 4)])
def test_prefetch_nao_expulsa_o_que_acabou_de_ler(tmp_path, policy, data, lotes):
    """Scan de 20 blocos com prefetch=4 e toss=True num cursor: cada bloco sai do disco 1 vez.

    O defeito original (código bruto ce1221b) era o prefetch expulsar o que ele mesmo trouxe
    (85 leituras no LRU). Com as pré-lidas fixadas (D-P1-12, revista em 23/11), isso é impossível.
    Teto com 1 cursor: o prefetch deixa >= 1 frame sem pin.
      LRU, DATA = 3: cada miss fixa o bloco pedido e pré-lê só 1 (3 − 1 − 1 = 1 sobra):
        lotes de 2 blocos -> 10 misses + 10 pré-lidas = 20 leituras.
      MRU, DATA = 8: cada miss pré-lê 4 (8 − 1 − 4 = 3 sobram): lotes de 5 -> 4 misses + 16 pré-lidas.
    """
    fm = FileManager(StorageConfig(root_dir=str(tmp_path)))
    fid = fm.create("t")
    for _ in range(20):
        fm.write(fid, fm.allocate(fid), bytes(fm.block_size))
    fm.reset_stats()
    bp = BufferPool(
        fm,
        BufferConfig(
            areas={PageType.DATA: data, PageType.WORK: 2},
            policy=policy,
            prefetch=4,
        ),
    )
    bp.allow_prefetch(fid, PageType.DATA)
    with bp.seq_scan(fid, PageType.DATA) as cur:
        for b in range(1, 21):
            with cur.page(b, toss=True):
                pass
    assert fm.io.reads == 20  # cada bloco sai do disco exatamente 1 vez
    assert (bp.stats.misses, bp.stats.prefetched) == (lotes, 20 - lotes)
    assert bp.pinned() == 0
    bp.flush_all()
    fm.close_all()


def test_buffer_full_nao_incrementa_misses(tmp_path):
    """Se _victim levantar BufferFullError, nenhuma leitura física ocorre e misses não deve ser incrementado."""
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    fid = fm.create("t")
    for b in (1, 2, 3):
        fm.allocate(fid)
        fm.write(fid, b, bytes([b]) * 4096)
    fm.reset_stats()

    pool = BufferPool(
        fm,
        BufferConfig(
            areas={PageType.CATALOG: 1, PageType.INDEX: 1, PageType.DATA: 2, PageType.WORK: 1},
            policy=Policy.LRU,
        ),
    )
    p1 = pool.pin(fid, 1, PageType.DATA)
    p2 = pool.pin(fid, 2, PageType.DATA)
    assert pool.stats.misses == 2
    assert fm.io.reads == 2

    with pytest.raises(BufferFullError):
        pool.pin(fid, 3, PageType.DATA)

    # Como a leitura do bloco 3 falhou por falta de frame, misses deve continuar igual a io.reads (2)
    assert pool.stats.misses == 2
    assert fm.io.reads == 2

    pool.unpin(p1)
    pool.unpin(p2)
    pool.flush_all()
    fm.close_all()


def test_reserve_work(tmp_path):
    """Com WORK=8, reserve_work(8) funciona e um pin(..., WORK) dentro dele levanta BufferFullError."""
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    fid = fm.create("tmp")
    fm.allocate(fid)
    fm.write(fid, 1, b"W" * 4096)

    pool = BufferPool(
        fm,
        BufferConfig(
            areas={PageType.CATALOG: 2, PageType.INDEX: 4, PageType.DATA: 4, PageType.WORK: 8},
            policy=Policy.LRU,
        ),
    )

    fm.reset_stats()
    with pool.reserve_work(8) as k:
        assert k == 8
        with pytest.raises(BufferFullError):
            pool.pin(fid, 1, PageType.WORK)
        # Cálculo esperado: os 8 frames de WORK estão emprestados ao operador; o pin não tem
        # frame livre nem vítima, então não lê do disco: 0 misses, 0 leituras.
        assert (pool.stats.misses, fm.io.reads) == (0, 0)
        # reservar além do que sobra (0 frames) também falha
        with pytest.raises(BufferFullError):
            with pool.reserve_work(1):
                pass

    # Fora do with, os 8 frames voltaram para WORK e o pin funciona normalmente:
    # bloco frio = 1 miss = 1 leitura física.
    with pool.page(fid, 1, PageType.WORK) as p:
        assert p.data[0] == ord("W")
    assert (pool.stats.misses, fm.io.reads, pool.pinned()) == (1, 1, 0)

    pool.flush_all()
    fm.close_all()


def test_areas_isolated(tmp_path):
    """Encher DATA não expulsa páginas de INDEX; stats_by_area devolve hits, misses e dirty_evictions por área."""
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    f_idx = fm.create("idx")
    f_dat = fm.create("dat")
    fm.allocate(f_idx)
    fm.write(f_idx, 1, b"I" * 4096)
    for b in range(1, 10):
        fm.allocate(f_dat)
        fm.write(f_dat, b, bytes([b]) * 4096)

    pool = BufferPool(
        fm,
        BufferConfig(
            areas={PageType.CATALOG: 1, PageType.INDEX: 2, PageType.DATA: 2, PageType.WORK: 1},
            policy=Policy.LRU,
        ),
    )

    # Carrega índice na área INDEX
    with pool.page(f_idx, 1, PageType.INDEX):
        pass

    # Varre 9 blocos na área DATA (que só tem 2 frames), sujando o bloco 1 (que será expulso ao carregar o 3)
    for b in range(1, 10):
        with pool.page(f_dat, b, PageType.DATA) as p_dat:
            if b == 1:
                p_dat.data[0] = 99
                p_dat.mark_dirty()

    # Página de INDEX continua residente (novo pin dá hit!)
    hits_before = pool.stats_by_area[PageType.INDEX].hits
    with pool.page(f_idx, 1, PageType.INDEX):
        pass
    assert pool.stats_by_area[PageType.INDEX].hits == hits_before + 1

    # Cálculo esperado por área:
    # - INDEX (2 frames): 1º pin = 1 miss, 2º pin = 1 hit, 0 dirty_evictions
    # - DATA (2 frames, 9 blocos distintos): 9 misses, 0 hits, e 1 dirty_eviction (bloco 1 sujo expulso pelo bloco 3)
    idx_stats = pool.stats_by_area[PageType.INDEX]
    dat_stats = pool.stats_by_area[PageType.DATA]
    assert (idx_stats.hits, idx_stats.misses, idx_stats.dirty_evictions) == (1, 1, 0)
    assert (dat_stats.hits, dat_stats.misses, dat_stats.dirty_evictions) == (0, 9, 1)

    pool.flush_all()
    fm.close_all()


def test_force(bp):
    """force grava já, limpa dirty, mantém fixação e incrementa forced_writes."""
    fid = bp.fm.create("cat")
    p = bp.new_page(fid, PageType.CATALOG)
    p.data[:4] = b"META"
    assert p.dirty is True

    # Cálculo esperado: force() grava 1 bloco imediatamente -> forced_writes == 1, io.writes aumenta em 1, dirty vira False e pin_count segue 1
    writes_before = bp.fm.io.writes
    bp.force(p)
    assert p.dirty is False
    assert p.pin_count == 1
    assert bp.stats.forced_writes == 1
    assert bp.fm.io.writes == writes_before + 1
    bp.unpin(p)


def test_drop_file(bp):
    """drop_file descarta frames sujos sem gravar no disco; io.writes não muda."""
    fid = bp.fm.create("temp_run")
    p1 = bp.new_page(fid, PageType.WORK)
    p1.data[0] = 77
    bp.unpin(p1, dirty=True)

    p2 = bp.new_page(fid, PageType.WORK)
    p2.data[0] = 88
    bp.unpin(p2, dirty=True)

    # Cálculo esperado: drop_file descarta os 2 frames sujos em RAM sem chamar fm.write -> io.writes aumenta em 0
    writes_before = bp.fm.io.writes
    bp.drop_file(fid)
    bp.fm.delete(fid)
    assert bp.fm.io.writes == writes_before


def test_free_block_reuse(bp):
    """free_block descarta eventual frame velho do buffer para não reaparecer ao realocar o bno."""
    fid = bp.fm.create("btree")
    p1 = bp.new_page(fid, PageType.DATA)
    bno = p1.bno
    p1.data[0] = 123
    bp.unpin(p1, dirty=True)
    bp.flush_file(fid)

    # Libera o bloco via free_block (estava residente no buffer com data[0] == 123)
    bp.free_block(fid, bno)

    # Realoca via new_page -> deve reutilizar o mesmo bno, porém zerado!
    p_reused = bp.new_page(fid, PageType.DATA)
    assert p_reused.bno == bno
    assert p_reused.data[0] == 0
    bp.unpin(p_reused, dirty=True)


def test_page_context_manager_unpins_on_exception(bp):
    """O context manager bp.page() deve fazer unpin e preservar dirty mesmo se o corpo levantar exceção."""
    fid = bp.fm.create("ctx_exc")
    p = bp.new_page(fid, PageType.DATA)
    bno = p.bno
    bp.unpin(p, dirty=True)
    bp.flush_file(fid)

    with pytest.raises(RuntimeError):
        with bp.page(fid, bno, PageType.DATA) as pg:
            pg.data[0] = 77
            pg.mark_dirty()
            raise RuntimeError("erro simulado no operador")

    assert bp.pinned() == 0
    assert pg.dirty is True


def test_reserve_work_partial_rollback(tmp_path):
    """Se reserve_work(8) falhar no 8º frame porque 1 frame de WORK está fixado, devolve os 7 frames parciais."""
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    fid = fm.create("work_tmp")
    fm.allocate(fid)
    fm.write(fid, 1, b"W" * 4096)

    pool = BufferPool(
        fm,
        BufferConfig(
            areas={PageType.CATALOG: 1, PageType.INDEX: 1, PageType.DATA: 2, PageType.WORK: 8},
            policy=Policy.LRU,
        ),
    )
    # Fixa 1 frame em WORK (restam apenas 7 livres)
    p_pinned = pool.pin(fid, 1, PageType.WORK)
    with pytest.raises(BufferFullError):
        with pool.reserve_work(8):
            pass

    # Os 7 frames retirados antes da falha devem ter voltado para WORK
    pool.unpin(p_pinned)
    with pool.reserve_work(8) as k:
        assert k == 8

    pool.flush_all()
    fm.close_all()


def test_cold_resets_head_counts_seek(bp):
    """reset_stats zera a posição da cabeça (_head = None): o 1º acesso depois de esfriar o cache conta como seek mesmo sendo bno + 1."""
    fid = bp.fm.create("rel_cold")
    for bno in (1, 2):
        p = bp.new_page(fid, PageType.DATA)
        p.data[0] = bno
        bp.unpin(p, dirty=True)

    # Lê o bloco 1 (deixaria a cabeça em (fid, 1)) e em seguida esfria o buffer e reseta contadores (como eng.cold())
    bp.flush_all()
    with bp.page(fid, 1, PageType.DATA):
        pass
    bp.clear()
    bp.fm.reset_stats()
    bp.reset_stats()

    # Cálculo esperado: como fm.reset_stats() zerou _head para None,
    # ler o bloco 2 (adjacente ao 1) DEVE contar como 1 seek (1 rand_read, 0 seq_reads) e 1 miss.
    with bp.page(fid, 2, PageType.DATA) as p2:
        assert p2.data[0] == 2

    assert bp.fm.io.seeks == 1
    assert bp.fm.io.rand_reads == 1
    assert bp.fm.io.seq_reads == 0
    assert bp.stats.misses == 1


def test_clear_inside_reserve_work(bp):
    """clear() chamado dentro de reserve_work(2) não duplica os frames reservados na lista free."""
    w = bp._areas[PageType.WORK]
    cap = len(w.frames)
    with bp.reserve_work(2):
        bp.clear()
        assert len(w.free) == cap - 2
        assert len({id(f) for f in w.free}) == len(w.free)
    assert len(w.free) == cap
    assert len({id(f) for f in w.free}) == len(w.free)


def test_close_reopen_and_recreate_with_buffer(bp):
    """Verifica os cenários de interação entre FileManager.close/create e BufferPool apontados na revisão."""
    # 1) new_page + unpin + fm.close(fid) direto + fm.open + bp.pin não corrompe o arquivo
    fid1 = bp.fm.create("rel_unflushed")
    p = bp.new_page(fid1, PageType.DATA)
    bp.unpin(p, dirty=True)
    bp.fm.close(fid1)

    fid1_reopened = bp.fm.open("rel_unflushed")
    with bp.page(fid1_reopened, 1, PageType.DATA) as p_reopened:
        assert len(p_reopened.data) == bp.block_size

    # 2) bp.close_file(fid) descarrega páginas sujas, limpa frames do buffer e fecha no FileManager
    fid2 = bp.fm.create("rel_closed_via_bp")
    p2 = bp.new_page(fid2, PageType.DATA)
    p2.data[0] = 111
    bp.unpin(p2, dirty=True)
    bp.close_file(fid2)
    assert not bp.fm.is_open(fid2)

    fid2_reopened = bp.fm.open("rel_closed_via_bp")
    with bp.page(fid2_reopened, 1, PageType.DATA) as p2_check:
        assert p2_check.data[0] == 111

    # 3) fm.create("c") sobre nome já aberto com página suja no buffer não quebra o próximo flush_all()
    fid_c1 = bp.fm.create("c")
    pc = bp.new_page(fid_c1, PageType.DATA)
    pc.data[0] = 55
    bp.unpin(pc, dirty=True)
    fid_c2 = bp.fm.create("c")
    assert fid_c2 != fid_c1
    bp.flush_all()


def test_prefetch_entre_cursores_nao_expulsa_nao_referenciadas(tmp_path):
    """Um cursor B não pode expulsar as páginas pré-lidas do cursor A antes de A usá-las.

    D-P1-12 (revista em 23/11): agora a garantia vem do pin do dono, e não de uma marca.
    DATA = 6, prefetch = 4.
      A abre (1 cursor) e lê f1[1]: 1 frame + pré-lê 2, 3, 4, 5 (sobra 1 frame sem pin) -> 5 fixados.
      Depois de usar f1[1], A ainda segura 2..5; sem pin: o frame de f1[1] e 1 livre.
      B abre (2 cursores) e lê f2[1]: usa o frame livre; sobra 1 frame sem pin (o de f1[1]).
      Regra do teto: pré-lê só se (sem pin − 1) >= cursores, ou seja, 1 − 1 = 0 < 2 -> B não pré-lê.
      A lê 2, 3, 4, 5: 4 hits, 0 leituras.
    """
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    f1 = fm.create("r1")
    f2 = fm.create("r2")
    for b in range(1, 7):
        fm.write(f1, fm.allocate(f1), bytes([b]) * 4096)
        fm.write(f2, fm.allocate(f2), bytes([100 + b]) * 4096)
    fm.reset_stats()

    bp = BufferPool(
        fm,
        BufferConfig(areas={PageType.DATA: 6, PageType.WORK: 2}, policy=Policy.LRU, prefetch=4),
    )
    bp.allow_prefetch(f1, PageType.DATA)
    bp.allow_prefetch(f2, PageType.DATA)

    with bp.seq_scan(f1, PageType.DATA) as cur_a:
        with cur_a.page(1):
            pass
        assert bp.stats.prefetched == 4
        with bp.seq_scan(f2, PageType.DATA) as cur_b:
            with cur_b.page(1):
                pass
            assert bp.stats.prefetched == 4  # B não pré-leu: o teto protege o frame de cada cursor

            reads_before = fm.io.reads
            hits_before = bp.stats.hits
            for b in (2, 3, 4, 5):
                with cur_a.page(b) as p:
                    assert p.data[0] == b
            assert fm.io.reads == reads_before
            assert bp.stats.hits == hits_before + 4
    assert bp.pinned() == 0
    bp.flush_all()
    fm.close_all()


def test_prefetch_com_mru_nao_expulsa_antes_do_uso(tmp_path):
    """Sob MRU (sem toss), páginas pré-lidas não saem antes de o cursor usá-las.

    Antes da revisão de 12/11 o MRU expulsava a pré-lida (a mais recente) antes do uso (64 leituras).
    Agora ela está fixada pelo cursor (D-P1-12, revista em 23/11).
    Cálculo: DATA = 8, prefetch = 4, 1 cursor -> lotes de 5 (1 + 4) -> 4 misses, 16 pré-lidas,
    20 leituras, cada bloco 1 vez.
    """
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    fid = fm.create("seq_mru")
    for b in range(1, 21):
        fm.write(fid, fm.allocate(fid), bytes([b]) * 4096)
    fm.reset_stats()

    bp = BufferPool(
        fm,
        BufferConfig(
            areas={PageType.DATA: 8, PageType.WORK: 2},
            policy=Policy.MRU,
            prefetch=4,
            toss_immediate=False,
        ),
    )
    bp.allow_prefetch(fid, PageType.DATA)

    with bp.seq_scan(fid, PageType.DATA) as cur:
        for b in range(1, 21):
            with cur.page(b) as p:
                assert p.data[0] == b
    assert fm.io.reads == 20
    assert (bp.stats.misses, bp.stats.prefetched) == (4, 16)
    assert bp.pinned() == 0
    bp.flush_all()
    fm.close_all()


def test_prefetch_exige_monotyped_e_bloqueia_misto(tmp_path):
    """Sem allow_prefetch(), o cursor não pré-lê; em arquivo misto, o prefetch é bloqueado."""
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    fid_sem = fm.create("sem_decl")
    fid_misto = fm.create("misto")
    for f in (fid_sem, fid_misto):
        for b in range(1, 6):
            fm.write(f, fm.allocate(f), bytes([b]) * 4096)
    fm.reset_stats()

    bp = BufferPool(
        fm,
        BufferConfig(
            areas={PageType.INDEX: 4, PageType.DATA: 4, PageType.WORK: 2},
            policy=Policy.LRU,
            prefetch=3,
        ),
    )

    # 1) Sem allow_prefetch(), o cursor lê apenas o bloco pedido (0 prefetched)
    with bp.seq_scan(fid_sem, PageType.DATA) as cur:
        with cur.page(1):
            pass
    assert bp.stats.prefetched == 0
    assert fm.io.reads == 1

    # 2) Declara fid_misto como DATA e usa como INDEX. O prefetched == 0 aqui sai só porque a área
    #    não bate com a declaração (DATA != INDEX); quem prova a DETECÇÃO da mistura é a parte 3.
    bp.allow_prefetch(fid_misto, PageType.DATA)
    with bp.seq_scan(fid_misto, PageType.INDEX) as cur:
        with cur.page(1):
            pass
    assert bp.stats.prefetched == 0
    with pytest.raises(ValueError):
        bp.allow_prefetch(fid_misto, PageType.INDEX)

    # Após detectar mistura, mesmo em DATA não deve mais fazer prefetch
    with bp.seq_scan(fid_misto, PageType.DATA) as cur:
        with cur.page(2):
            pass
    assert bp.stats.prefetched == 0
    assert bp.pinned() == 0
    bp.flush_all()
    fm.close_all()


def test_fm_close_and_free_diretos_mantem_coerencia(tmp_path):
    """Chamadas diretas a fm.close() e fm.free() não perdem páginas sujas nem deixam frames stale."""
    cfg = StorageConfig(root_dir=str(tmp_path), block_size=4096)
    fm = FileManager(cfg)
    bp = BufferPool(
        fm,
        BufferConfig(areas={PageType.DATA: 2, PageType.WORK: 1}, policy=Policy.LRU),
    )

    # 1) fm.close(fid) direto deve fazer flush de página suja antes de fechar o arquivo
    fid1 = fm.create("direct_close")
    p1 = bp.new_page(fid1, PageType.DATA)
    p1.data[0] = 77
    bp.unpin(p1, dirty=True)
    fm.close(fid1)
    assert not fm.is_open(fid1)

    fid1_re = fm.open("direct_close")
    with bp.page(fid1_re, 1, PageType.DATA) as p1_check:
        assert p1_check.data[0] == 77
    bp.close_file(fid1_re)

    # 2) fm.free(fid, bno) direto deve descartar frame stale sujo do buffer
    fid2 = fm.create("direct_free")
    p2 = bp.new_page(fid2, PageType.DATA)
    bno = p2.bno
    p2.data[0] = 88
    bp.unpin(p2, dirty=True)
    fm.free(fid2, bno)

    # Ao realocar o mesmo bno via new_page, deve vir zerado e sem dirty_eviction do frame antigo
    p2_re = bp.new_page(fid2, PageType.DATA)
    assert p2_re.bno == bno
    assert p2_re.data[0] == 0
    bp.unpin(p2_re)
    assert bp.stats.dirty_evictions == 0
    bp.close_file(fid2)


def test_free_block_descarta_suja_sem_gravar(tmp_path):
    """free_block() deve descartar página suja sem gravar no disco e devolver bloco zerado ao realocar."""
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    bp = BufferPool(fm, BufferConfig(areas={PageType.DATA: 2, PageType.WORK: 1}))
    fid = fm.create("free_dirty")

    p = bp.new_page(fid, PageType.DATA)
    bno = p.bno
    p.data[0] = 123
    bp.unpin(p, dirty=True)

    bp.free_block(fid, bno)
    assert fm.io.writes == 0
    assert bp.stats.dirty_evictions == 0

    p_new = bp.new_page(fid, PageType.DATA)
    assert p_new.bno == bno
    assert p_new.data[0] == 0
    bp.unpin(p_new)
    bp.close_file(fid)


def test_drop_file_e_unpin_invariantes(tmp_path):
    """Valida exceções de drop_file com pin, descarte sem escrita, e invariantes de unpin."""
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    bp = BufferPool(fm, BufferConfig(areas={PageType.DATA: 2, PageType.WORK: 1}))
    fid = fm.create("invariantes")

    p = bp.new_page(fid, PageType.DATA)
    p.data[0] = 42
    # unpin com dirty=False após new_page (ou mark_dirty) deve preservar dirty=True
    bp.unpin(p, dirty=False)
    assert p.dirty is True

    # unpin quando pin_count == 0 deve levantar RuntimeError
    with pytest.raises(RuntimeError):
        bp.unpin(p)

    # drop_file com página fixada deve levantar RuntimeError
    p_pin = bp.pin(fid, p.bno, PageType.DATA)
    with pytest.raises(RuntimeError):
        bp.drop_file(fid)
    bp.unpin(p_pin)

    # drop_file sem pin descarta página suja sem gravar no disco
    bp.drop_file(fid)
    assert fm.io.writes == 0
    fm.delete(fid)


def test_interleaved_files_with_prefetch_reduces_seeks(tmp_path):
    """Dois cursores alternando entre dois arquivos: o prefetch reduz os seeks.

    Cálculo: sem prefetch, cada leitura troca de arquivo -> 32 leituras = 32 seeks.
    Com prefetch = 3 (lotes de 4) e DATA = 16 com 2 cursores (o teto exige >= 2 frames sem pin;
    nunca chega perto): cada cursor lê 4 blocos seguidos por miss -> 32 / 4 = 8 seeks.
    """
    seeks_by_prefetch = {}
    for pref in (0, 3):
        fm = FileManager(StorageConfig(root_dir=str(tmp_path / f"p{pref}"), block_size=4096))
        f1 = fm.create("r1")
        f2 = fm.create("r2")
        for b in range(1, 17):
            fm.write(f1, fm.allocate(f1), bytes([b]) * 4096)
            fm.write(f2, fm.allocate(f2), bytes([100 + b]) * 4096)
        fm.reset_stats()

        bp = BufferPool(
            fm,
            BufferConfig(areas={PageType.DATA: 16, PageType.WORK: 2}, policy=Policy.LRU, prefetch=pref),
        )
        bp.allow_prefetch(f1, PageType.DATA)
        bp.allow_prefetch(f2, PageType.DATA)

        with bp.seq_scan(f1, PageType.DATA) as c1, bp.seq_scan(f2, PageType.DATA) as c2:
            for b in range(1, 17):
                with c1.page(b) as p1:
                    assert p1.data[0] == b
                with c2.page(b) as p2:
                    assert p2.data[0] == 100 + b

        seeks_by_prefetch[pref] = fm.io.seeks
        assert fm.io.reads == 32  # 16 blocos de cada arquivo, cada um lido 1 vez
        assert bp.pinned() == 0
        bp.close_file(f1)
        bp.close_file(f2)

    assert seeks_by_prefetch[0] == 32
    assert seeks_by_prefetch[3] == 8


def test_fm_delete_direto_notifica_buffer_pool(tmp_path):
    """fm.delete(fid) em arquivo aberto deve validar pin_count == 0 e descartar frames do buffer."""
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    bp = BufferPool(fm, BufferConfig(areas={PageType.DATA: 2, PageType.WORK: 1}))

    fid = fm.create("del_hook")
    p = bp.new_page(fid, PageType.DATA)
    p.data[0] = 42
    # Enquanto estiver fixada, fm.delete(fid) deve recusar com RuntimeError
    with pytest.raises(RuntimeError):
        fm.delete(fid)
    assert fm.exists("del_hook")

    bp.unpin(p, dirty=True)
    fm.delete(fid)
    assert not fm.exists("del_hook")
    with pytest.raises(KeyError):
        bp.pin(fid, 1, PageType.DATA)
    assert bp.pinned() == 0


def test_allow_prefetch_rejeita_arquivo_ja_acessado_em_outra_area_ou_new_page(tmp_path):
    """Misturar áreas antes de allow_prefetch() ou via new_page() deve impedir prefetch monotipado."""
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    bp = BufferPool(
        fm,
        BufferConfig(
            areas={PageType.DATA: 4, PageType.INDEX: 2, PageType.WORK: 1},
            policy=Policy.LRU,
            prefetch=2,
        ),
    )

    # Caso A: acesso prévio em INDEX impede declarar allow_prefetch(..., DATA)
    fid_a = fm.create("prev_index")
    for _ in range(4):
        fm.write(fid_a, fm.allocate(fid_a), bytes(4096))
    with bp.page(fid_a, 1, PageType.INDEX):
        pass
    with pytest.raises(ValueError):
        bp.allow_prefetch(fid_a, PageType.DATA)
    with bp.seq_scan(fid_a, PageType.DATA) as cur:
        with cur.page(2):
            pass
    assert bp.stats.prefetched == 0
    bp.close_file(fid_a)

    # Caso B: allow_prefetch(..., DATA) seguido de new_page(..., INDEX) revoga prefetch
    fid_b = fm.create("new_page_mix")
    bp.allow_prefetch(fid_b, PageType.DATA)
    p1 = bp.new_page(fid_b, PageType.DATA)
    bp.unpin(p1, dirty=True)
    p2 = bp.new_page(fid_b, PageType.INDEX)
    bp.unpin(p2, dirty=True)
    bp.flush_all()
    bp.clear()
    with bp.seq_scan(fid_b, PageType.DATA) as cur:
        with cur.page(1):
            pass
    assert bp.stats.prefetched == 0
    bp.close_file(fid_b)


def test_prefetch_de_um_cursor_nao_impede_prefetch_de_outro(tmp_path):
    """Substitui test_prefetch_lru_nao_trava_quando_mais_antigo_e_prefetched (marca `prefetched`,
    removida na revisão de 23/11 da D-P1-12). Mesma intenção: a pré-leitura ainda não usada de um
    cursor não trava o prefetch de outro cursor, e continua no buffer até o dono usá-la.

    DATA = 5, prefetch = 1, LRU.
      Cursor 1 (1 cursor aberto) lê f1[1]: 1 frame + pré-lê f1[2] (fixado). Sem pin: 3 livres.
      Depois do uso de f1[1]: sem pin = 3 livres + f1[1] = 4.
      Cursor 2 abre (2 cursores) e lê f2[1]: usa 1 livre (sobram 3 sem pin); o teto pede >= 2
      depois de fixar mais 1: 3 − 1 = 2 >= 2 -> pré-lê f2[2]. Total: 2 pré-lidas.
      Cursor 1 lê f1[2]: hit, sem leitura (a pré-lida dele continuou fixada).
    """
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    bp = BufferPool(
        fm,
        BufferConfig(
            areas={PageType.DATA: 5, PageType.WORK: 1},
            policy=Policy.LRU,
            prefetch=1,
        ),
    )
    f1 = fm.create("lru_c1")
    f2 = fm.create("lru_c2")
    for f in (f1, f2):
        for b in range(1, 5):
            fm.allocate(f)
            fm.write(f, b, bytes([b]) * 4096)
        bp.allow_prefetch(f, PageType.DATA)

    with bp.seq_scan(f1, PageType.DATA) as c1:
        with c1.page(1):
            pass
        with bp.seq_scan(f2, PageType.DATA) as c2:
            with c2.page(1):
                pass
            assert bp.stats.prefetched == 2  # f1[2] e f2[2]
            hits_before, reads_before = bp.stats.hits, fm.io.reads
            with c1.page(2) as p:
                assert p.data[0] == 2
            assert (bp.stats.hits, fm.io.reads) == (hits_before + 1, reads_before)
    assert bp.pinned() == 0
    bp.close_file(f1)
    bp.close_file(f2)


def _orfas(tmp_path, policy):
    """DATA = 5, prefetch = 3. Um cursor lê só o bloco 1 e para (fecha): os blocos 2, 3 e 4 foram
    pré-lidos e nunca usados. D-P1-12 (revista em 23/11): enquanto o cursor está aberto eles estão
    fixados por ele; quando ele fecha, os pins saem. Teto: 1 cursor, 5 frames -> 5 − 1 − 3 = 1 sobra,
    então o lote sai completo. Estado depois de fechar, do mais antigo ao mais recente: [1, 2, 3, 4]
    (todos sem pin) + 1 frame livre."""
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    fid = fm.create("orfas")
    for b in range(1, 13):
        fm.write(fid, fm.allocate(fid), bytes([b]) * 4096)
    pool = BufferPool(
        fm,
        BufferConfig(
            areas={PageType.CATALOG: 1, PageType.INDEX: 1, PageType.DATA: 5, PageType.WORK: 1},
            policy=policy,
            prefetch=3,
        ),
    )
    pool.allow_prefetch(fid, PageType.DATA)
    with pool.seq_scan(fid, PageType.DATA) as cur:
        with cur.page(1):
            pass
        assert pool.stats.prefetched == 3
        assert pool.pinned() == 3  # as 3 pré-lidas estão fixadas pelo cursor dono
    assert pool.pinned() == 0  # o cursor terminou e soltou o que não usou
    fm.reset_stats()
    pool.reset_stats()
    return fm, pool, fid


def test_prefetch_orfas_soltas_quando_o_cursor_termina(tmp_path):
    """Substitui test_prefetch_orfas_perdem_protecao_lru (segunda chance, D-P1-12 revista em 22/11,
    substituída em 23/11). O defeito era: pré-lidas órfãs protegidas para sempre (10 misses em vez de 2).
    Agora o cursor solta os pins das pré-lidas que não usou ao terminar, e a área volta a ser LRU puro.

    Cálculo à mão (LRU, 5 frames, 10 pins alternando 5 e 6, a partir de [1, 2, 3, 4] + 1 livre):
      pin 5: miss, usa o livre; pin 6: miss, expulsa 1 (o mais antigo); os 8 pins restantes: hits.
    Total: 2 misses, 8 hits, 2 leituras = LRU puro (antes da correção de 22/11: 10 misses, 0 hits;
    com a segunda chance: 3 misses).
    """
    fm, pool, fid = _orfas(tmp_path, Policy.LRU)
    for i in range(10):
        with pool.page(fid, 5 + i % 2, PageType.DATA):
            pass
    assert (pool.stats.misses, pool.stats.hits, fm.io.reads) == (2, 8, 2)
    assert pool.pinned() == 0
    fm.close_all()


@pytest.mark.parametrize("policy", [Policy.LRU, Policy.MRU])
def test_prefetch_orfas_nao_desligam_prefetch_seguinte(tmp_path, policy):
    """Pré-lidas de um cursor que parou cedo não podem desligar o prefetch do próximo scan.

    Novo cursor lendo 7, 8, 9 e 10 a partir de [1, 2, 3, 4] (sem pin) + 1 livre, 1 cursor aberto:
      pin 7: miss, usa o livre; sem pin = 4 (1..4); o teto deixa fixar enquanto sobrar >= 1:
      pré-lê 8, 9, 10 usando 3 das antigas como vítimas (LRU: 1, 2, 3; MRU: 4, 3, 2).
      8, 9, 10: hits (o cursor assume os pins).
    Total nas duas políticas: 1 miss, 3 hits, 3 pré-lidas, 4 leituras.
    Antes da correção de 22/11: 4 misses, 0 pré-lidas (o prefetch parava nas órfãs).
    """
    fm, pool, fid = _orfas(tmp_path, policy)
    with pool.seq_scan(fid, PageType.DATA) as cur:
        for b in (7, 8, 9, 10):
            with cur.page(b) as p:
                assert p.data[0] == b
    obtido = (pool.stats.misses, pool.stats.hits, pool.stats.prefetched, fm.io.reads)
    assert obtido == (1, 3, 3, 4)
    assert pool.pinned() == 0
    fm.close_all()


def test_prefetch_com_vitimas_sujas_le_lote_sequencial(tmp_path):
    """D-P1-12 (revista em 22/11, B2): as vítimas do lote são limpas ANTES das leituras.

    DATA = 5 frames, todos sujos com os blocos 1..5 de outro arquivo `b` (na ordem LRU
    b1, b2, b3, b4, b5); prefetch = 3; cursor em `a`, cabeça zerada. Teto com 1 cursor:
    5 − 1 − 3 = 1 frame sem pin sobra, então o lote sai completo (a revisão de 23/11 tirou o
    prefetch da área WORK, onde este teste rodava antes, com 4 frames).
    Cálculo à mão, com a correção:
      gravações: b1 (rand, cabeça zerada), b2, b3, b4 (seq)       -> 1 rand + 3 seq
      leituras:  a1 (rand, a cabeça estava em b), a2, a3, a4 (seq) -> 1 rand + 3 seq
      seeks = 1 + 1 = 2.
    Antes da correção, gravação e leitura se alternavam (grava b1, lê a1, grava b2, lê a2 ...):
    as 8 operações eram aleatórias, com 0 leituras sequenciais (ai/P1/review.md, achado 18).
    """
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    a = fm.create("a")
    for blk in range(1, 5):
        fm.write(a, fm.allocate(a), bytes([blk]) * 4096)
    pool = BufferPool(
        fm,
        BufferConfig(
            areas={PageType.CATALOG: 1, PageType.INDEX: 1, PageType.DATA: 5, PageType.WORK: 1},
            policy=Policy.LRU,
            prefetch=3,
        ),
    )
    pool.allow_prefetch(a, PageType.DATA)
    b = fm.create("b")
    for _ in range(5):
        pool.unpin(pool.new_page(b, PageType.DATA))  # nasce suja
    fm.reset_stats()
    pool.reset_stats()

    with pool.seq_scan(a, PageType.DATA) as cur:
        for blk in (1, 2, 3, 4):  # 1 miss; o lote pré-lido (2, 3, 4) dá 3 hits, 0 leituras
            with cur.page(blk) as p:
                assert p.data[0] == blk

    io = fm.io
    assert (io.rand_writes, io.seq_writes) == (1, 3)
    assert (io.rand_reads, io.seq_reads) == (1, 3)
    assert io.seeks == 2
    assert (pool.stats.misses, pool.stats.prefetched, pool.stats.hits) == (1, 3, 3)
    assert pool.stats.dirty_evictions == 4
    assert pool.pinned() == 0

    pool.flush_all()
    fm.close_all()


def test_identidade_das_gravacoes_com_flush(tmp_path):
    """D-P1-05 (revista em 22/11, C0): toda gravação física vem de 3 causas e
    io.writes == dirty_evictions + forced_writes + gravações de flush, em que a última
    parcela é io.writes − dirty_evictions − forced_writes (não há contador próprio).

    Cálculo à mão (DATA = 2 frames, LRU):
      suja 1, suja 2; pin 3 expulsa 1 (sujo)       -> 1 dirty_eviction
      suja 3 e force(3)                            -> 1 forced_write (3 fica limpo)
      flush_file: só o 2 continua sujo             -> 1 gravação de flush
      close_file: nada sujo                        -> 0
    io.writes = 1 + 1 + 1 = 3; gravações de flush = 3 − 1 − 1 = 1.
    """
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    fid = fm.create("t")
    for blk in range(1, 5):
        fm.write(fid, fm.allocate(fid), bytes([blk]) * 4096)
    pool = BufferPool(
        fm,
        BufferConfig(
            areas={PageType.CATALOG: 1, PageType.INDEX: 1, PageType.DATA: 2, PageType.WORK: 1},
            policy=Policy.LRU,
        ),
    )
    fm.reset_stats()
    for blk in (1, 2):
        with pool.page(fid, blk, PageType.DATA) as p:
            p.data[0] = 100 + blk
            p.mark_dirty()
    p3 = pool.pin(fid, 3, PageType.DATA)  # expulsa o 1, sujo
    p3.data[0] = 103
    p3.mark_dirty()
    pool.force(p3)
    pool.unpin(p3)
    assert (fm.io.writes, pool.stats.dirty_evictions, pool.stats.forced_writes) == (2, 1, 1)

    pool.flush_file(fid)
    flushed = fm.io.writes - pool.stats.dirty_evictions - pool.stats.forced_writes
    assert (fm.io.writes, flushed) == (3, 1)

    pool.close_file(fid)  # nada sujo: 0 gravações
    assert fm.io.writes == 3
    assert pool.pinned() == 0

    # O que foi gravado é o que estava no buffer (as 3 alterações chegaram ao disco).
    fid = fm.open("t")
    lidos = []
    for blk in (1, 2, 3):
        with pool.page(fid, blk, PageType.DATA) as p:
            lidos.append(p.data[0])
    assert lidos == [101, 102, 103]
    pool.close_file(fid)
    fm.close_all()


# --- D-P1-12 (revista em 23/11): prefetch com pin do cursor dono (SKS §13.5.1.2) ---


def _arquivo(fm, nome, n):
    fid = fm.create(nome)
    for b in range(1, n + 1):
        fm.write(fid, fm.allocate(fid), bytes([b]) * fm.block_size)
    return fid


def _pins(pool, fid, bnos):
    """pin_count de cada bloco residente (0 se não residente). Só lê a tabela do buffer."""
    return [pool._table[(fid, b)].pin_count if (fid, b) in pool._table else 0 for b in bnos]


def test_prefetch_dois_donos_so_sai_quando_os_dois_soltam(tmp_path):
    """O exemplo combinado com o professor, passo a passo (prefetch = 3, DATA = 8).

      passo                         pin_count de [1, 2, 3, 4]
      1. A lê 1 (miss)              [1, 1, 1, 1]   1 em uso; 2, 3, 4 pré-lidas fixadas por A
      2. A solta 1                  [0, 1, 1, 1]
      3. B fixa 3 (hit)             [0, 1, 2, 1]   3 tem dois donos
      4. A usa 2 e solta            [0, 0, 2, 1]   A assume o pin de 2 (não soma outro)
      5. A termina                  [0, 0, 1, 0]   A solta 3 e 4, que não usou
      6. B solta 3                  [0, 0, 0, 0]
    Leituras: 4 (1 miss + 3 pré-lidas); 3 e 2 foram hits.
    """
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    fid = _arquivo(fm, "t", 6)
    fm.reset_stats()
    pool = BufferPool(fm, BufferConfig(areas={PageType.DATA: 8, PageType.WORK: 1}, prefetch=3))
    pool.allow_prefetch(fid, PageType.DATA)
    bnos = [1, 2, 3, 4]

    with pool.seq_scan(fid, PageType.DATA) as a:
        with a.page(1):
            assert _pins(pool, fid, bnos) == [1, 1, 1, 1]
        assert _pins(pool, fid, bnos) == [0, 1, 1, 1]
        p3_b = pool.pin(fid, 3, PageType.DATA)  # B: hit
        assert _pins(pool, fid, bnos) == [0, 1, 2, 1]
        with a.page(2):
            assert _pins(pool, fid, bnos) == [0, 1, 2, 1]  # assumiu o pin, não somou
        assert _pins(pool, fid, bnos) == [0, 0, 2, 1]
    assert _pins(pool, fid, bnos) == [0, 0, 1, 0]
    pool.unpin(p3_b)
    assert _pins(pool, fid, bnos) == [0, 0, 0, 0]

    assert (fm.io.reads, pool.stats.misses, pool.stats.prefetched, pool.stats.hits) == (4, 1, 3, 2)
    assert pool.pinned() == 0
    fm.close_all()


@pytest.mark.parametrize("saida", ["break", "excecao"])
def test_cursor_solta_pre_lidas_em_break_e_excecao(tmp_path, saida):
    """Um cursor que sai cedo (seleção que achou a chave, erro no operador) solta as pré-lidas.

    DATA = 8, prefetch = 4: o miss em 1 fixa 2..5. O cursor sai depois de usar só o 1, e
    pinned() volta a 0. Sem isso, a fixture `eng` acusaria pin vazando ao fim de cada teste.
    """
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    fid = _arquivo(fm, "t", 10)
    pool = BufferPool(fm, BufferConfig(areas={PageType.DATA: 8, PageType.WORK: 1}, prefetch=4))
    pool.allow_prefetch(fid, PageType.DATA)

    class Parada(Exception):
        pass

    try:
        with pool.seq_scan(fid, PageType.DATA) as cur:
            for b in range(1, 11):
                with cur.page(b):
                    assert pool.pinned() == 5
                if saida == "excecao":
                    raise Parada
                break
    except Parada:
        pass
    assert pool.stats.prefetched == 4
    assert pool.pinned() == 0
    fm.close_all()


@pytest.mark.parametrize("area", [PageType.WORK, PageType.CATALOG])
def test_allow_prefetch_so_em_data_e_index(tmp_path, area):
    """D-P1-12 (revista em 23/11): pré-lidas fixadas em WORK tirariam frames do M dos operadores."""
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    fid = _arquivo(fm, "t", 2)
    pool = BufferPool(fm, BufferConfig(prefetch=2))
    with pytest.raises(ValueError):
        pool.allow_prefetch(fid, area)
    pool.allow_prefetch(fid, PageType.INDEX)  # INDEX é aceito
    fm.close_all()


def test_teto_do_prefetch_garante_frame_para_outro_cursor(tmp_path):
    """Com pré-lidas fixadas, o prefetch de um cursor não pode tomar a área toda.

    DATA = 4, prefetch = 4, 2 cursores abertos. Regra: só pré-lê enquanto, depois de fixar,
    sobrarem >= 2 frames sem pin (1 por cursor).
      A lê f1[1] (e segura): 1 frame; sem pin = 3; pré-lê f1[2] (sobram 2); pararia em 1 < 2.
      B, com A ainda segurando f1[1] e f1[2]: sem pin = 2; lê f2[1] (sobra 1); não pré-lê.
      B solta f2[1] e lê f2[2]: usa o último frame livre; nenhum BufferFullError.
    Sem o teto, A pré-leria 3 blocos, fixaria os 4 frames e B levaria BufferFullError.
    """
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    f1 = _arquivo(fm, "r1", 6)
    f2 = _arquivo(fm, "r2", 6)
    fm.reset_stats()
    pool = BufferPool(
        fm, BufferConfig(areas={PageType.DATA: 4, PageType.WORK: 1}, policy=Policy.LRU, prefetch=4)
    )
    pool.allow_prefetch(f1, PageType.DATA)
    pool.allow_prefetch(f2, PageType.DATA)

    with pool.seq_scan(f1, PageType.DATA) as a, pool.seq_scan(f2, PageType.DATA) as b:
        with a.page(1):
            assert pool.stats.prefetched == 1
            assert pool.pinned() == 2
            for blk in (1, 2):
                with b.page(blk) as p:
                    assert p.data[0] == blk
            assert pool.stats.prefetched == 1  # B não pré-leu nada
    assert pool.pinned() == 0
    assert fm.io.reads == 4  # f1[1], f1[2] (pré-lida), f2[1], f2[2]
    fm.close_all()


def test_pin_seq_fora_de_cursor_nao_pre_le(tmp_path):
    """pin(seq=True) fica no contrato por compatibilidade, mas sem dono não há prefetch:
    uma pré-leitura sem dono não teria quem soltasse o pin dela (D-P1-12, revista em 23/11)."""
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    fid = _arquivo(fm, "t", 6)
    fm.reset_stats()
    pool = BufferPool(fm, BufferConfig(areas={PageType.DATA: 8, PageType.WORK: 1}, prefetch=4))
    pool.allow_prefetch(fid, PageType.DATA)
    with pool.page(fid, 1, PageType.DATA, seq=True):
        pass
    assert (fm.io.reads, pool.stats.prefetched, pool.pinned()) == (1, 0, 0)
    fm.close_all()


# --- Revisão independente de 23/11 (ai/P1/review.md, achados 23 a 27) ---


def test_cursor_que_salta_solta_pre_lidas(tmp_path):
    """D-P1-12 (S2): um pedido fora das pré-lidas solta as que sobraram; o cursor segura <= 1 + k.

    DATA = 40, k = 4, páginas 1, 10, 20, 30. Cada uma é miss e pré-lê 4 (o teto, com 1 cursor e
    muitos frames livres, nunca corta). Dentro de cada página: 1 em uso + 4 pré-lidas = 5 fixadas.
    Leituras: 4 misses + 16 pré-lidas = 20. Antes da correção as pré-lidas se acumulavam: 5, 9, 13, 17.
    """
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    fid = _arquivo(fm, "t", 40)
    fm.reset_stats()
    pool = BufferPool(fm, BufferConfig(areas={PageType.DATA: 40, PageType.WORK: 1}, prefetch=4))
    pool.allow_prefetch(fid, PageType.DATA)
    with pool.seq_scan(fid, PageType.DATA) as cur:
        for b in (1, 10, 20, 30):
            with cur.page(b) as p:
                assert p.data[0] == b
                assert pool.pinned() == 5
    assert (fm.io.reads, pool.stats.misses, pool.stats.prefetched) == (20, 4, 16)
    assert pool.pinned() == 0
    fm.close_all()


def test_cursor_fechado_levanta_erro(tmp_path):
    """Usar o cursor depois do `with` levantava nada e deixava 4 páginas fixadas para sempre."""
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    fid = _arquivo(fm, "t", 8)
    pool = BufferPool(fm, BufferConfig(areas={PageType.DATA: 8, PageType.WORK: 1}, prefetch=4))
    pool.allow_prefetch(fid, PageType.DATA)
    with pool.seq_scan(fid, PageType.DATA) as cur:
        pass
    with pytest.raises(RuntimeError):
        with cur.page(1):
            pass
    assert pool.pinned() == 0
    assert fm.io.reads == 0
    fm.close_all()


def _frames_utilizaveis(pool, ptype):
    """Frames da área que voltam a ser usados: livres + residentes (os demais se perderam)."""
    area = pool._areas[ptype]
    return len(area.free) + sum(1 for f in area.frames if f.fid >= 0)


def test_falha_de_gravacao_no_lote_nao_perde_frame(tmp_path):
    """Uma falha de fm.write ao expulsar uma vítima suja do lote não pode tirar frames da área.

    DATA = 4 com 4 páginas sujas de `b`, k = 3. cur.page(a, 1): a vítima do bloco pedido é b1
    (1ª gravação, ok); a 1ª vítima do lote é b2 e a 2ª gravação falha. Esperado: OSError propagado,
    b2 continua residente e suja, e os 4 frames continuam utilizáveis (antes da correção: 3).
    """
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    a = _arquivo(fm, "a", 4)
    pool = BufferPool(fm, BufferConfig(areas={PageType.DATA: 4, PageType.WORK: 1}, prefetch=3))
    pool.allow_prefetch(a, PageType.DATA)
    b = fm.create("b")
    for _ in range(4):
        pool.unpin(pool.new_page(b, PageType.DATA))

    original = fm.write
    chamadas = [0]

    def write_que_falha(fid, bno, data):
        chamadas[0] += 1
        if chamadas[0] == 2:
            raise OSError("disco cheio (simulado)")
        original(fid, bno, data)

    fm.write = write_que_falha
    with pytest.raises(OSError):
        with pool.seq_scan(a, PageType.DATA) as cur:
            with cur.page(1):
                pass
    fm.write = original

    assert pool.pinned() == 0
    assert pool._table[(b, 2)].dirty  # a vítima que falhou continua residente e suja
    assert _frames_utilizaveis(pool, PageType.DATA) == 4
    fixadas = [pool.pin(a, blk, PageType.DATA) for blk in (1, 2, 3, 4)]  # cabem os 4
    for p in fixadas:
        pool.unpin(p)
    pool.flush_all()
    fm.close_all()


def test_falha_de_leitura_no_lote_propaga_sem_vazar(tmp_path):
    """Um erro real de leitura no meio do lote (não fim de arquivo) é propagado, sem vazar pin.

    DATA = 8, k = 4. A 3ª chamada de fm.read (bloco 3, a 2ª pré-leitura) levanta OSError.
    Esperado: OSError chega ao chamador; o bloco 1 e a pré-lida 2 ficam residentes e sem pin
    depois do cursor; io.reads == misses + prefetched (2 == 1 + 1); nenhum frame perdido.
    Antes da correção o erro era engolido e o lote só terminava mais cedo.
    """
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    fid = _arquivo(fm, "t", 8)
    fm.reset_stats()
    pool = BufferPool(fm, BufferConfig(areas={PageType.DATA: 8, PageType.WORK: 1}, prefetch=4))
    pool.allow_prefetch(fid, PageType.DATA)

    original = fm.read
    chamadas = [0]

    def read_que_falha(f, bno):
        chamadas[0] += 1
        if chamadas[0] == 3:
            raise OSError("setor ilegível (simulado)")
        return original(f, bno)

    fm.read = read_que_falha
    with pytest.raises(OSError):
        with pool.seq_scan(fid, PageType.DATA) as cur:
            with cur.page(1):
                pass
    fm.read = original

    assert pool.pinned() == 0
    assert (fm.io.reads, pool.stats.misses, pool.stats.prefetched) == (2, 1, 1)
    assert (fid, 1) in pool._table and (fid, 2) in pool._table
    assert _frames_utilizaveis(pool, PageType.DATA) == 8
    fm.close_all()


def test_bloco_alem_do_fim_fisico_no_lote_propaga_sem_perder_frame(tmp_path):
    """Bloco alocado mas nunca gravado no lote: o ValueError de fm.read propaga (achado 30).

    A 1ª fase já conferiu is_valid_block, então esse ValueError só aparece por bug de quem chamou
    (aqui, fm.allocate direto sem gravar) e não pode ser tratado como fim normal do lote.
    DATA = 4, k = 4, arquivo com 5 blocos alocados e só 1 e 2 gravados. cur.page(1): vítima do
    bloco 1 = 1 frame; o teto (frames sem pin − 1 >= cursores = 1) deixa o lote em 2 e 3 (3 − 1 = 2,
    2 − 1 = 1; no 4, 1 − 1 = 0 < 1). Lê 1 e 2 (1 pré-lida); o 3 levanta ValueError: o frame dele
    volta a free, o pin do bloco 1 sai e o cursor solta a pré-lida 2 ao terminar. Fica: 0 pins e os
    4 frames utilizáveis.
    """
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    fid = fm.create("t")
    for b in range(1, 6):
        fm.allocate(fid)
    for b in (1, 2):
        fm.write(fid, b, bytes([b]) * 4096)
    outro = _arquivo(fm, "outro", 4)
    pool = BufferPool(fm, BufferConfig(areas={PageType.DATA: 4, PageType.WORK: 1}, prefetch=4))
    pool.allow_prefetch(fid, PageType.DATA)
    with pytest.raises(ValueError):
        with pool.seq_scan(fid, PageType.DATA) as cur:
            with cur.page(1):
                pass
    assert pool.stats.prefetched == 1
    assert pool.pinned() == 0
    assert _frames_utilizaveis(pool, PageType.DATA) == 4
    fixadas = [pool.pin(outro, blk, PageType.DATA) for blk in (1, 2, 3, 4)]
    for p in fixadas:
        pool.unpin(p)
    fm.close_all()


def test_lote_para_em_bloco_residente(tmp_path):
    """O lote para no primeiro bloco já residente: nunca dois frames para o mesmo (fid, bno).

    DATA = 8, k = 4; o bloco 4 está fixado fora do cursor. cur.page(1) lê 1 e pré-lê só 2 e 3.
    Leituras depois do reset: 3. Fixadas: p4 + 1 (em uso) + 2 pré-lidas = 4.
    """
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    fid = _arquivo(fm, "t", 8)
    pool = BufferPool(fm, BufferConfig(areas={PageType.DATA: 8, PageType.WORK: 1}, prefetch=4))
    pool.allow_prefetch(fid, PageType.DATA)
    p4 = pool.pin(fid, 4, PageType.DATA)
    p4.data[1] = 77
    p4.mark_dirty()
    fm.reset_stats()
    pool.reset_stats()
    with pool.seq_scan(fid, PageType.DATA) as cur:
        with cur.page(1):
            assert pool.pinned() == 4
    assert (pool.stats.prefetched, fm.io.reads) == (2, 3)
    assert sum(1 for f in pool._areas[PageType.DATA].frames if (f.fid, f.bno) == (fid, 4)) == 1
    assert pool._table[(fid, 4)] is p4 and p4.data[1] == 77
    pool.unpin(p4)
    pool.flush_all()
    fm.close_all()


def test_toss_dentro_do_cursor(tmp_path):
    """cur.page(toss=True) repassa o toss: com toss_immediate, o scan não expulsa a página quente.

    LRU, DATA = 4, sem prefetch. X (outro arquivo) entra primeiro e é a mais antiga. Scan de 6
    blocos com toss: as páginas do scan usam os 3 frames livres e depois se reciclam pela fila
    tossed; X fica. Sem o toss, o LRU expulsaria X no 4º bloco do scan e o pin final seria miss.
    """
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    h = _arquivo(fm, "h", 1)
    a = _arquivo(fm, "a", 6)
    pool = BufferPool(
        fm,
        BufferConfig(areas={PageType.DATA: 4, PageType.WORK: 1}, policy=Policy.LRU, toss_immediate=True),
    )
    with pool.page(h, 1, PageType.DATA):
        pass
    with pool.seq_scan(a, PageType.DATA) as cur:
        for b in range(1, 7):
            with cur.page(b, toss=True):
                pass
    hits = pool.stats.hits
    with pool.page(h, 1, PageType.DATA):
        pass
    assert pool.stats.hits == hits + 1
    fm.close_all()


def test_pre_lida_assumida_atualiza_recencia(tmp_path):
    """Quando o cursor usa a pré-lida, a política registra o acesso (LRU: vai para o fim).

    LRU, DATA = 4, k = 2. Ordem (mais antiga -> mais recente):
      cur.page(1): pré-lê 2 e 3 (o teto para no 3º) -> [1, 2, 3]; pin em y -> [1, 2, 3, y]
      cur usa 2 e 3 -> [1, y, 2, 3]. Fora do cursor: pin z expulsa 1; pin w expulsa y.
    Sem registrar o acesso, a ordem seria [1, 2, 3, y] e o pin w expulsaria 2.
    """
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    a = _arquivo(fm, "a", 6)
    y, z, w = (_arquivo(fm, n, 1) for n in ("y", "z", "w"))
    pool = BufferPool(
        fm, BufferConfig(areas={PageType.DATA: 4, PageType.WORK: 1}, policy=Policy.LRU, prefetch=2)
    )
    pool.allow_prefetch(a, PageType.DATA)
    with pool.seq_scan(a, PageType.DATA) as cur:
        with cur.page(1):
            pass
        assert pool.stats.prefetched == 2
        with pool.page(y, 1, PageType.DATA):
            pass
        for b in (2, 3):
            with cur.page(b):
                pass
    for f in (z, w):
        with pool.page(f, 1, PageType.DATA):
            pass
    assert (a, 2) in pool._table and (a, 3) in pool._table
    assert (y, 1) not in pool._table and (a, 1) not in pool._table
    fm.close_all()


def test_prefetch_na_area_index(tmp_path):
    """O prefetch também funciona em INDEX, com o teto contado na própria área INDEX.

    INDEX = 3, k = 4, 1 cursor em INDEX: o miss em 1 usa 1 frame; o teto deixa >= 1 sem pin,
    então pré-lê só o 2 (3 − 1 − 1 = 1). O 2 é hit. Contadores na área INDEX, nada em DATA.
    """
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    fid = _arquivo(fm, "idx", 6)
    fm.reset_stats()
    pool = BufferPool(
        fm, BufferConfig(areas={PageType.INDEX: 3, PageType.DATA: 2, PageType.WORK: 1}, prefetch=4)
    )
    pool.allow_prefetch(fid, PageType.INDEX)
    with pool.seq_scan(fid, PageType.INDEX) as cur:
        for b in (1, 2):
            with cur.page(b) as p:
                assert p.data[0] == b
    st = pool.stats_by_area[PageType.INDEX]
    assert (st.requests, st.hits, st.misses, st.prefetched) == (2, 1, 1, 1)
    assert pool.stats_by_area[PageType.DATA].requests == 0
    assert fm.io.reads == 2
    assert pool.pinned() == 0
    fm.close_all()


def test_limite_do_teto_documentado(tmp_path):
    """D-P1-12 (T0): pré-leitura não causa BufferFullError enquanto
    pins simultâneos fora dos cursores <= capacidade − cursores × (1 + k).

    1) Dentro da condição: DATA = 12, k = 4, 1 cursor -> 12 − 5 = 7 pins avulsos simultâneos cabem.
    2) Fora dela (limite aceito e documentado): DATA = 3, k = 8. O cursor fixa o bloco 1 e o teto
       deixa pré-ler 1 bloco (sobra 1 frame). O 1º pin avulso cabe; o 2º leva BufferFullError,
       que sem prefetch não aconteceria (3 frames para 3 pins).
    """
    fm = FileManager(StorageConfig(root_dir=str(tmp_path / "ok"), block_size=4096))
    a = _arquivo(fm, "a", 10)
    b = _arquivo(fm, "b", 10)
    pool = BufferPool(fm, BufferConfig(areas={PageType.DATA: 12, PageType.WORK: 1}, prefetch=4))
    pool.allow_prefetch(a, PageType.DATA)
    with pool.seq_scan(a, PageType.DATA) as cur:
        with cur.page(1):
            assert pool.stats.prefetched == 4
            avulsas = [pool.pin(b, blk, PageType.DATA) for blk in range(1, 8)]  # 7 cabem
            for p in avulsas:
                pool.unpin(p)
    assert pool.pinned() == 0
    fm.close_all()

    fm = FileManager(StorageConfig(root_dir=str(tmp_path / "limite"), block_size=4096))
    a = _arquivo(fm, "a", 10)
    b = _arquivo(fm, "b", 10)
    pool = BufferPool(fm, BufferConfig(areas={PageType.DATA: 3, PageType.WORK: 1}, prefetch=8))
    pool.allow_prefetch(a, PageType.DATA)
    with pool.seq_scan(a, PageType.DATA) as cur:
        with cur.page(1):
            assert pool.stats.prefetched == 1
            with pool.page(b, 1, PageType.DATA):
                with pytest.raises(BufferFullError):
                    pool.pin(b, 2, PageType.DATA)
    assert pool.pinned() == 0
    fm.close_all()


# --- Revisor de confirmação de 23/11 (ai/P1/review.md, achado 29) ---


def test_repetir_a_pagina_atual_nao_descarta_pre_lidas(tmp_path):
    """S2 solta as pré-lidas só num MISS (quando o cursor vai pré-ler de novo), não num hit.

    DATA = 16, k = 8. cur.page(1) lê 1 e pré-lê 2..9. Pedir o 1 de novo (aninhado ou em
    sequência) é hit: as pré-lidas 2..9 continuam fixadas pelo cursor. Depois, 2..9 são hits
    assumidos, sem leitura: 9 leituras no total (1 miss + 8 pré-lidas).
    Antes da correção, o 2º pedido do 1 soltava 2..9 (perdiam a proteção do pin).
    """
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    fid = _arquivo(fm, "t", 20)
    fm.reset_stats()
    pool = BufferPool(fm, BufferConfig(areas={PageType.DATA: 16, PageType.WORK: 1}, prefetch=8))
    pool.allow_prefetch(fid, PageType.DATA)
    with pool.seq_scan(fid, PageType.DATA) as cur:
        with cur.page(1):
            with cur.page(1):  # aninhado: hit
                assert sorted(cur._held) == list(range(2, 10))
        with cur.page(1):  # em sequência: hit
            pass
        assert sorted(cur._held) == list(range(2, 10))
        assert pool.pinned() == 8
        for b in range(2, 10):
            with cur.page(b) as p:
                assert p.data[0] == b
    assert (fm.io.reads, pool.stats.misses, pool.stats.prefetched) == (9, 1, 8)
    assert pool.pinned() == 0
    fm.close_all()


def test_cursor_aninhado_segura_k_pre_lidas_mais_paginas_em_uso(tmp_path):
    """O limite por cursor é k pré-lidas + as páginas em uso (d + k com aninhamento de profundidade d).

    DATA = 128, k = 8. cur.page(1) fixa 1 e pré-lê 2..9 (9 fixadas). Dentro dela, cur.page(100)
    é miss: solta 2..9, fixa 100 e pré-lê 101..108. Fixadas: 1 e 100 em uso + 8 pré-lidas = 10
    (d = 2). As pré-lidas seguradas nunca passam de k.
    """
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    fid = _arquivo(fm, "t", 120)
    pool = BufferPool(fm, BufferConfig(areas={PageType.DATA: 128, PageType.WORK: 1}, prefetch=8))
    pool.allow_prefetch(fid, PageType.DATA)
    with pool.seq_scan(fid, PageType.DATA) as cur:
        with cur.page(1):
            assert pool.pinned() == 9
            with cur.page(100):
                assert sorted(cur._held) == list(range(101, 109))
                assert pool.pinned() == 10
    assert pool.pinned() == 0
    fm.close_all()
