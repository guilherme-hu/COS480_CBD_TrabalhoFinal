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
    """Encher DATA não expulsa páginas de INDEX."""
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

    # Varre 9 blocos na área DATA (que só tem 2 frames)
    for b in range(1, 10):
        with pool.page(f_dat, b, PageType.DATA):
            pass

    # Página de INDEX continua residente (novo pin dá hit!)
    hits_before = pool.stats_by_area[PageType.INDEX].hits
    with pool.page(f_idx, 1, PageType.INDEX):
        pass
    assert pool.stats_by_area[PageType.INDEX].hits == hits_before + 1

    pool.flush_all()
    fm.close_all()


def test_force(bp):
    """force grava já, limpa dirty, mantém fixação e incrementa forced_writes."""
    fid = bp.fm.create("cat")
    p = bp.new_page(fid, PageType.CATALOG)
    p.data[:4] = b"META"
    assert p.dirty is True

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
