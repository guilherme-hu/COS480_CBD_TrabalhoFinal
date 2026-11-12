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
        p1 = pool.pin(fid, 1, PageType.DATA)
        p2 = pool.pin(fid, 2, PageType.DATA)
        with pytest.raises(BufferFullError):
            pool.pin(fid, 3, PageType.DATA)

        pool.unpin(p1)
        pool.unpin(p2)
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
    pool.flush_all()
    fm.close_all()


def test_prefetch(tmp_path):
    """prefetch=4: um pin seq em bloco frio lê 5 blocos; os 4 pins seguintes são hits e pinned()==0."""
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

    with pool.page(fid, 1, PageType.DATA, seq=True) as p1:
        assert p1.data[0] == 1

    # 1 miss + 4 prefetched = 5 leituras físicas (1 rand_read + 4 seq_reads)
    assert pool.stats.requests == 1
    assert pool.stats.misses == 1
    assert pool.stats.prefetched == 4
    assert fm.io.reads == 5
    assert fm.io.rand_reads == 1
    assert fm.io.seq_reads == 4
    assert pool.pinned() == 0  # prefetch NÃO pode vazar pin!

    # Os próximos 4 pins (blocos 2, 3, 4, 5) devem ser todos hits sem nenhuma leitura física extra
    for b in (2, 3, 4, 5):
        with pool.page(fid, b, PageType.DATA, seq=True) as p:
            assert p.data[0] == b

    assert pool.stats.requests == 5
    assert pool.stats.hits == 4
    assert pool.stats.misses == 1
    assert fm.io.reads == 5
    pool.flush_all()
    fm.close_all()


@pytest.mark.parametrize("policy,data", [(Policy.LRU, 3), (Policy.MRU, 8)])
def test_prefetch_nao_expulsa_o_que_acabou_de_ler(tmp_path, policy, data):
    """Em um scan sequencial de 20 blocos com prefetch=4 e toss=True, o prefetch não pode
    expulsar páginas que ele mesmo acabou de trazer na mesma chamada.
    Cálculo esperado: 20 blocos lidos em sequência -> cada bloco sai do disco exatamente 1 vez (io.reads == 20).
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
    for b in range(1, 21):
        bp.unpin(bp.pin(fid, b, PageType.DATA, seq=True), toss=True)
    assert fm.io.reads == 20  # cada bloco sai do disco exatamente 1 vez
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

    with pool.reserve_work(8) as k:
        assert k == 8
        with pytest.raises(BufferFullError):
            pool.pin(fid, 1, PageType.WORK)

    # Fora do with, os 8 frames voltaram para WORK e o pin funciona normalmente
    with pool.page(fid, 1, PageType.WORK) as p:
        assert p.data[0] == ord("W")

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
    """Prefetch de um cursor B não pode expulsar páginas pré-lidas ainda não referenciadas pelo cursor A."""
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    f1 = fm.create("r1")
    f2 = fm.create("r2")
    for b in range(1, 7):
        fm.write(f1, fm.allocate(f1), bytes([b]) * 4096)
        fm.write(f2, fm.allocate(f2), bytes([100 + b]) * 4096)
    fm.reset_stats()

    # DATA=6: Cursor A lê bloco 1 (1 frame) + prefetch traz 2, 3, 4, 5 (4 frames) = 5 frames ocupados.
    # Cursor B lê bloco 1 (usa o 6º frame livre); seu prefetch para imediatamente porque todos
    # os outros frames disponíveis são páginas prefetched ainda não referenciadas do Cursor A.
    bp = BufferPool(
        fm,
        BufferConfig(areas={PageType.DATA: 6, PageType.WORK: 2}, policy=Policy.LRU, prefetch=4),
    )
    bp.allow_prefetch(f1, PageType.DATA)
    bp.allow_prefetch(f2, PageType.DATA)

    p_a1 = bp.pin(f1, 1, PageType.DATA, seq=True)
    bp.unpin(p_a1)
    p_b1 = bp.pin(f2, 1, PageType.DATA, seq=True)
    bp.unpin(p_b1)

    # Cursor A agora lê os blocos 2, 3, 4 e 5: todos devem dar HIT sem nenhuma leitura física extra!
    reads_before = fm.io.reads
    hits_before = bp.stats.hits
    for b in (2, 3, 4, 5):
        with bp.page(f1, b, PageType.DATA, seq=True) as p:
            assert p.data[0] == b
    assert fm.io.reads == reads_before
    assert bp.stats.hits == hits_before + 4
    bp.flush_all()
    fm.close_all()


def test_prefetch_com_mru_nao_expulsa_antes_do_uso(tmp_path):
    """Sob MRU (sem toss=True), páginas pré-lidas não entram como próxima vítima antes do primeiro pin()."""
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

    # Cálculo esperado: scan sequencial de 20 blocos -> cada bloco é lido do disco exatamente 1 vez (io.reads == 20).
    for b in range(1, 21):
        with bp.page(fid, b, PageType.DATA, seq=True) as p:
            assert p.data[0] == b
    assert fm.io.reads == 20
    assert bp.pinned() == 0
    bp.flush_all()
    fm.close_all()


def test_prefetch_exige_monotyped_e_bloqueia_misto(tmp_path):
    """Sem allow_prefetch(), seq=True não pré-lê; em arquivo misto, prefetch é bloqueado."""
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

    # 1) Sem allow_prefetch(), pin(..., seq=True) lê apenas o bloco pedido (0 prefetched)
    with bp.page(fid_sem, 1, PageType.DATA, seq=True):
        pass
    assert bp.stats.prefetched == 0
    assert fm.io.reads == 1

    # 2) Declara fid_misto como DATA, mas tenta usar também como INDEX -> conflito bloqueia prefetch
    bp.allow_prefetch(fid_misto, PageType.DATA)
    with bp.page(fid_misto, 1, PageType.INDEX, seq=True):
        pass
    assert bp.stats.prefetched == 0
    with pytest.raises(ValueError):
        bp.allow_prefetch(fid_misto, PageType.INDEX)

    # Após detectar mistura, mesmo em DATA não deve mais fazer prefetch
    with bp.page(fid_misto, 2, PageType.DATA, seq=True):
        pass
    assert bp.stats.prefetched == 0
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
    """Alternar leitura entre dois arquivos com prefetch reduz significativamente o total de seeks."""
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

        for b in range(1, 17):
            with bp.page(f1, b, PageType.DATA, seq=True) as p1:
                assert p1.data[0] == b
            with bp.page(f2, b, PageType.DATA, seq=True) as p2:
                assert p2.data[0] == 100 + b

        seeks_by_prefetch[pref] = fm.io.seeks
        assert fm.io.reads == 32  # 16 blocos de cada arquivo, cada um lido 1 vez
        bp.close_file(f1)
        bp.close_file(f2)

    # Cálculo esperado: sem prefetch, 32 trocas de arquivo = 32 seeks; com prefetch=3 (lote de 4), ~8 seeks (32 / 4).
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
    with bp.page(fid_a, 2, PageType.DATA, seq=True):
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
    with bp.page(fid_b, 1, PageType.DATA, seq=True):
        pass
    assert bp.stats.prefetched == 0
    bp.close_file(fid_b)


def test_prefetch_lru_nao_trava_quando_mais_antigo_e_prefetched(tmp_path):
    """Sob LRU, um bloco prefetched=True na ponta mais antiga não deve travar prefetch se houver frames prefetched=False."""
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    bp = BufferPool(
        fm,
        BufferConfig(
            areas={PageType.DATA: 3, PageType.WORK: 1},
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

    # Cursor 1 lê f1[1] -> pré-carrega f1[2] (prefetched=True). Depois f1[1] é unpinado (prefetched=False).
    # Acessamos f1[3] sem seq=True (só 1 bloco) para que f1[2] (prefetched=True) fique mais antigo na fila LRU que f1[1]/f1[3]?
    # Primeiro f1[1] (pos 0), f1[2] (pos 1). Acessar f1[1] move f1[1] para o fim -> fila LRU: [f1[2](pref), f1[1](ref)]
    with bp.page(f1, 1, PageType.DATA, seq=True):
        pass
    with bp.page(f1, 1, PageType.DATA, seq=False):
        pass
    # Agora o frame mais antigo da fila LRU é f1[2] (prefetched=True), mas f1[1] tem prefetched=False.
    # Cursor 2 lê f2[1] com seq=True -> deve conseguir pré-carregar f2[2] usando f1[1] como vítima, sem expulsar f1[2]!
    with bp.page(f2, 1, PageType.DATA, seq=True):
        pass
    assert bp.stats.prefetched == 2  # f1[2] e f2[2]
    # f1[2] ainda deve estar residente (HIT)
    hits_before = bp.stats.hits
    with bp.page(f1, 2, PageType.DATA):
        pass
    assert bp.stats.hits == hits_before + 1
    bp.close_file(f1)
    bp.close_file(f2)
