"""Testes unitários das duas políticas de substituição selecionadas (LRU e MRU — P1)."""
from __future__ import annotations

from cbd.contracts import BufferConfig, PageType, Policy, StorageConfig
from cbd.storage.buffer_pool import BufferPool
from cbd.storage.file_manager import FileManager


def _prepare_file(fm: FileManager, name: str, n_blocks: int) -> int:
    fid = fm.create(name)
    for bno in range(1, n_blocks + 1):
        fm.allocate(fid)
        fm.write(fid, bno, bytes([bno % 256]) * fm.block_size)
    fm.reset_stats()
    return fid


def test_lru_vs_mru_victims(tmp_path):
    """Sequência de referência clássica com 3 frames: compara a sequência de vítimas de LRU e MRU à mão.

    Sequência de acessos com 3 frames na área DATA:
      1. Carrega 1, 2, 3 -> buffer contém [1, 2, 3]
      2. Re-acessa 1 (hit) e depois 2 (hit):
         - Ordem de recência (do mais antigo para o mais recente):
           [3 (mais antigo), 1, 2 (mais recente)]
      3. Pede bloco 4 (miss):
         - LRU expulsa o menos recentemente acessado: bloco 3 -> ficam {1, 2, 4}
         - MRU expulsa o mais recentemente acessado: bloco 2 -> ficam {3, 1, 4}
      4. Pede bloco 5 (miss):
         - LRU (tinha [1, 2, 4]) expulsa o menos recente: bloco 1 -> ficam {2, 4, 5}
         - MRU (tinha [3, 1, 4], sendo 4 o mais recente) expulsa o mais recente: bloco 4 -> ficam {1, 3, 5}
    """
    expected_residents_after = {
        Policy.LRU: {2, 4, 5},  # expulsou 3, depois 1
        Policy.MRU: {1, 3, 5},  # expulsou 2, depois 4
    }

    for pol, expected_set in expected_residents_after.items():
        fm = FileManager(StorageConfig(root_dir=str(tmp_path / pol.value), block_size=4096))
        fid = _prepare_file(fm, "rel", 6)
        bcfg = BufferConfig(
            areas={PageType.CATALOG: 1, PageType.INDEX: 1, PageType.DATA: 3, PageType.WORK: 1},
            policy=pol,
        )
        pool = BufferPool(fm, bcfg)

        for bno in (1, 2, 3, 1, 2, 4, 5):
            with pool.page(fid, bno, PageType.DATA):
                pass

        resident_bnos = {
            f.bno for f in pool._areas[PageType.DATA].frames if f.fid == fid
        }
        assert resident_bnos == expected_set, f"Falha na política {pol}: {resident_bnos} != {expected_set}"
        pool.flush_all()
        fm.close_all()


def test_sequential_flooding(tmp_path):
    """Área de 4 frames; varrer 5 blocos 3x (15 acessos).
    - LRU sofre sequential flooding: 15 misses (0 hits).
    - MRU retém 3 blocos entre passadas: apenas 7 misses (8 hits)!
    """
    # 1) LRU: 15 misses, 0 hits
    fm_lru = FileManager(StorageConfig(root_dir=str(tmp_path / "lru"), block_size=4096))
    fid_lru = _prepare_file(fm_lru, "inner", 5)
    pool_lru = BufferPool(
        fm_lru,
        BufferConfig(
            areas={PageType.CATALOG: 1, PageType.INDEX: 1, PageType.DATA: 4, PageType.WORK: 1},
            policy=Policy.LRU,
        ),
    )
    for _ in range(3):
        for bno in range(1, 6):
            with pool_lru.page(fid_lru, bno, PageType.DATA):
                pass
    assert pool_lru.stats.requests == 15
    assert pool_lru.stats.misses == 15
    assert pool_lru.stats.hits == 0
    fm_lru.close_all()

    # 2) MRU: 1ª passada = 5 misses (ficam {1,2,3,5});
    #         2ª passada = 4 hits (1,2,3,5) + 1 miss (bloco 4 expulsa 3 -> ficam {1,2,4,5});
    #         3ª passada = 4 hits (1,2,4,5) + 1 miss (bloco 3 expulsa 2 -> ficam {1,3,4,5})
    #         Total: apenas 7 misses e 8 hits (contra 15 misses e 0 hits do LRU)!
    fm_mru = FileManager(StorageConfig(root_dir=str(tmp_path / "mru"), block_size=4096))
    fid_mru = _prepare_file(fm_mru, "inner", 5)
    pool_mru = BufferPool(
        fm_mru,
        BufferConfig(
            areas={PageType.CATALOG: 1, PageType.INDEX: 1, PageType.DATA: 4, PageType.WORK: 1},
            policy=Policy.MRU,
        ),
    )
    for _ in range(3):
        for bno in range(1, 6):
            with pool_mru.page(fid_mru, bno, PageType.DATA):
                pass
    assert pool_mru.stats.requests == 15
    assert pool_mru.stats.misses == 7
    assert pool_mru.stats.hits == 8
    assert pool_mru.stats.misses < pool_lru.stats.misses
    fm_mru.close_all()
