"""Testes de estresse da Camada 4 (FileManager + BufferPool) e comparação Ingênuo vs Refatorado."""
from __future__ import annotations

import random
import pytest

from ai.P1.raw.buffer_pool_inicial import NaiveBufferPool, NaiveFileManager
from cbd.contracts import BufferConfig, BufferFullError, PageType, Policy, StorageConfig
from cbd.storage.buffer_pool import BufferPool
from cbd.storage.file_manager import FileManager


@pytest.mark.stress
def test_buffer_random_stress_with_oracle(tmp_path):
    """Estresse do BufferPool com área minúscula (3 frames), LRU e MRU e oráculo Python."""
    for pol in (Policy.LRU, Policy.MRU):
        root = tmp_path / f"stress_{pol.value}"
        scfg = StorageConfig(root_dir=str(root), block_size=2048)
        bcfg = BufferConfig(
            areas={PageType.CATALOG: 1, PageType.INDEX: 2, PageType.DATA: 3, PageType.WORK: 2},
            policy=pol,
            toss_immediate=True,
            prefetch=2,
        )
        fm = FileManager(scfg)
        pool = BufferPool(fm, bcfg)
        fid = fm.create("stress_rel")
        pool.allow_prefetch(fid, PageType.DATA)

        rng = random.Random(480)
        n_blocks = 10_000
        oracle: dict[int, bytearray] = {}

        # Aloca 10.000 blocos iniciais via new_page (conforme guia P1 §6)
        for i in range(1, n_blocks + 1):
            p = pool.new_page(fid, PageType.DATA)
            assert p.bno == i
            val = rng.randrange(1, 255)
            p.data[0] = val
            p.data[-1] = (val * 7) % 256
            oracle[i] = bytearray(p.data)
            pool.unpin(p)  # sem passar dirty=True, pois new_page já nasce dirty=True!

        active_pins: list = []
        # 1.000.000 operações aleatórias de pin/unpin/dirty/force em apenas 3 frames (guia P1 §6)!
        for step in range(1_000_000):
            # Se já temos 2 páginas fixadas (de 3 frames), desafixa uma para não esgotar sempre
            if len(active_pins) >= 2 or (active_pins and rng.random() < 0.65):
                idx = rng.randrange(len(active_pins))
                p = active_pins.pop(idx)
                toss = rng.random() < 0.3
                pool.unpin(p, toss=toss)
                continue

            bno = rng.randint(1, n_blocks)
            seq = rng.random() < 0.2
            p = pool.pin(fid, bno, PageType.DATA, seq=seq)
            assert p.data == oracle[bno], (
                f"Corrupção no passo {step} (pol={pol}): bloco {bno} diverge do oráculo!"
            )

            # 40% de chance de atualizar a página usando mark_dirty()
            if rng.random() < 0.4:
                new_byte = rng.randrange(1, 255)
                pos = rng.randrange(scfg.block_size)
                p.data[pos] = new_byte
                p.mark_dirty()
                oracle[bno][pos] = new_byte
                if rng.random() < 0.1:
                    pool.force(p)

            active_pins.append(p)

        while active_pins:
            pool.unpin(active_pins.pop())

        assert pool.pinned() == 0
        assert pool.stats.requests == pool.stats.hits + pool.stats.misses
        assert fm.io.reads == pool.stats.misses + pool.stats.prefetched
        assert fm.io.writes == pool.stats.dirty_evictions + pool.stats.forced_writes
        assert fm.io.reads == fm.io.seq_reads + fm.io.rand_reads
        assert fm.io.writes == fm.io.seq_writes + fm.io.rand_writes

        # Limpa o cache (grava todos os sujos), fecha e reabre do disco para validar persistência
        pool.clear()
        fm.close_all()

        fm_check = FileManager(scfg)
        fid_check = fm_check.open("stress_rel")
        for bno, expected in oracle.items():
            actual = fm_check.read(fid_check, bno)
            assert actual == expected, f"Bloco {bno} incorreto no disco após reabertura ({pol})"
        fm_check.close_all()


@pytest.mark.stress
def test_naive_vs_refactored_stress(tmp_path):
    """Demonstra que o código refatorado supera tanto a v1 bruta (ce1221b) quanto o protótipo ingênuo (§7.2.3.d)."""
    from ai.P1.raw.buffer_pool_v1_ce1221b import BufferPool as BufferPoolV1

    # 1. Código bruto real (v1_ce1221b) vs. Refatorado: auto-expulsão no prefetch (85 leituras vs 20 leituras)
    fm_v1 = FileManager(StorageConfig(root_dir=str(tmp_path / "v1_lru")))
    fid_v1 = fm_v1.create("t")
    for _ in range(20):
        fm_v1.write(fid_v1, fm_v1.allocate(fid_v1), bytes(fm_v1.block_size))
    fm_v1.reset_stats()
    bp_v1 = BufferPoolV1(
        fm_v1,
        BufferConfig(areas={PageType.DATA: 3, PageType.WORK: 2}, policy=Policy.LRU, prefetch=4),
    )
    for b in range(1, 21):
        bp_v1.unpin(bp_v1.pin(fid_v1, b, PageType.DATA, seq=True), toss=True)
    v1_reads = fm_v1.io.reads
    fm_v1.close_all()
    assert v1_reads == 85, "A versão bruta ce1221b relê blocos por auto-expulsão no prefetch"

    fm_ref = FileManager(StorageConfig(root_dir=str(tmp_path / "ref_lru")))
    fid_ref = fm_ref.create("t")
    for _ in range(20):
        fm_ref.write(fid_ref, fm_ref.allocate(fid_ref), bytes(fm_ref.block_size))
    fm_ref.reset_stats()
    bp_ref = BufferPool(
        fm_ref,
        BufferConfig(areas={PageType.DATA: 3, PageType.WORK: 2}, policy=Policy.LRU, prefetch=4),
    )
    bp_ref.allow_prefetch(fid_ref, PageType.DATA)
    for b in range(1, 21):
        bp_ref.unpin(bp_ref.pin(fid_ref, b, PageType.DATA, seq=True), toss=True)
    ref_reads = fm_ref.io.reads
    fm_ref.close_all()
    assert ref_reads == 20, "BufferPool refatorado lê cada bloco exatamente 1 vez (85 -> 20 leituras)"

    # 2. Demonstra a perda de dados por mark_dirty() + unpin() no protótipo ingênuo vs. refatorado
    scfg_naive = StorageConfig(root_dir=str(tmp_path / "naive"), block_size=2048)
    nfm = NaiveFileManager(scfg_naive)
    nbp = NaiveBufferPool(
        nfm,
        BufferConfig(areas={PageType.DATA: 2}, policy=Policy.LRU),
    )
    nfid = nfm.create("rel")
    for _ in range(3):
        b = nfm.allocate(nfid)
        nfm.write(nfid, b, b"\x01" * 2048)

    # No código ingênuo: marca dirty via mark_dirty() e chama unpin(p) padrão -> perde a escrita!
    np1 = nbp.pin(nfid, 1)
    np1.data[0] = 99
    np1.mark_dirty()
    nbp.unpin(np1)
    # Expulsa bloco 1 carregando blocos 2 e 3
    nbp.unpin(nbp.pin(nfid, 2))
    nbp.unpin(nbp.pin(nfid, 3))
    np1_reload = nbp.pin(nfid, 1)
    naive_lost_update = np1_reload.data[0] != 99
    nbp.unpin(np1_reload)
    nfm.close_all()
    assert naive_lost_update is True, "O código ingênuo perde a atualização de mark_dirty()"

    # No código refatorado: a mesma sequência preserva 100% da atualização!
    scfg_ref = StorageConfig(root_dir=str(tmp_path / "refactored"), block_size=2048)
    rfm = FileManager(scfg_ref)
    rbp = BufferPool(
        rfm,
        BufferConfig(
            areas={PageType.CATALOG: 1, PageType.INDEX: 1, PageType.DATA: 2, PageType.WORK: 1},
            policy=Policy.LRU,
        ),
    )
    rfid = rfm.create("rel")
    for _ in range(3):
        b = rfm.allocate(rfid)
        rfm.write(rfid, b, b"\x01" * 2048)

    rp1 = rbp.pin(rfid, 1, PageType.DATA)
    rp1.data[0] = 99
    rp1.mark_dirty()
    rbp.unpin(rp1)
    rbp.unpin(rbp.pin(rfid, 2, PageType.DATA))
    rbp.unpin(rbp.pin(rfid, 3, PageType.DATA))
    rp1_reload = rbp.pin(rfid, 1, PageType.DATA)
    assert rp1_reload.data[0] == 99
    rbp.unpin(rp1_reload)
    rbp.flush_all()
    rfm.close_all()

