"""Testes do Engine (P6, issue #7).

N1 com dublês da camada 4: o Engine só orquestra, então os dublês registram as
chamadas e simulam contadores. O teste com a pilha real é pulado enquanto
cbd/storage (P1) e cbd/files (P2) não existirem no repositório.
"""
from pathlib import Path
import sys
import types

import pytest

from cbd.contracts import (BufferConfig, BufferCounters, IOCounters, PageType, Policy,
                           StorageConfig)


class FakeFileManager:
    def __init__(self, cfg, log):
        self.cfg, self.log, self.io = cfg, log, IOCounters()

    def reset_stats(self):
        self.log.append('fm.reset_stats')
        self.io = IOCounters()

    def close_all(self):
        self.log.append('fm.close_all')


class FakeBufferPool:
    def __init__(self, fm, cfg):
        self.fm, self.cfg, self.log = fm, cfg, fm.log
        self.frames = {}          # (fid, bno) -> dirty
        self.stats = BufferCounters()
        self.stats_by_area = {area: BufferCounters() for area in PageType}

    def touch(self, fid, bno, area, dirty=False):
        """Simula um pin+unpin com miss: 1 request, 1 miss, 1 leitura física."""
        self.frames[(fid, bno)] = dirty
        for counters in (self.stats, self.stats_by_area[area]):
            counters.requests += 1
            counters.misses += 1
        self.fm.io.rand_reads += 1

    def clear(self):
        # Contrato §3.2: flush_all (grava os sujos, contando I/O) + esvazia os frames.
        self.log.append('bp.clear')
        self.fm.io.rand_writes += sum(self.frames.values())
        self.frames.clear()

    def flush_all(self):
        self.log.append('bp.flush_all')

    def reset_stats(self):
        self.log.append('bp.reset_stats')
        self.stats = BufferCounters()
        self.stats_by_area = {area: BufferCounters() for area in PageType}

    def capacity(self, ptype):
        return self.cfg.areas[ptype]

    def pinned(self, ptype=None):
        return 0


class FakeCatalog:
    def __init__(self, bp):
        self.bp = bp

    def close(self):
        self.bp.log.append('catalog.close')


@pytest.fixture
def fake_engine(monkeypatch, tmp_path):
    log = []
    modules = {
        'cbd.storage': types.ModuleType('cbd.storage'),
        'cbd.storage.file_manager': types.SimpleNamespace(FileManager=lambda cfg: FakeFileManager(cfg, log)),
        'cbd.storage.buffer_pool': types.SimpleNamespace(BufferPool=FakeBufferPool),
        'cbd.files': types.ModuleType('cbd.files'),
        'cbd.files.catalog': types.SimpleNamespace(Catalog=FakeCatalog),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    from cbd.engine import Engine
    return Engine(StorageConfig(str(tmp_path)), BufferConfig()), log


def csv_counter_columns():
    """Colunas io.* e bp.* do CSV, lidas do próprio guia P6 §6.3 (fonte única)."""
    text = Path('docs/implementacao/P6_bancada_relatorio.md').read_text()
    block = text.split('### 6.3 CSV')[1].split('```')[1]
    columns = [c.strip() for c in block.replace('\n', ',').split(',') if c.strip()]
    return [c for c in columns if c.startswith(('io.', 'bp.'))]


def test_cold_zeroes_every_counter_and_empties_cache(fake_engine):
    eng, _ = fake_engine
    eng.bp.touch(1, 1, PageType.DATA, dirty=True)
    eng.bp.touch(1, 2, PageType.INDEX)
    assert eng.snapshot()['io.reads'] == 2
    eng.cold()
    assert eng.bp.frames == {}
    assert all(value == 0 for value in eng.snapshot().values())
    assert eng.bp.pinned() == 0


def test_cold_flushes_before_resetting(fake_engine):
    # A página suja gravada pelo clear() não pode aparecer como gravação da próxima
    # medição. Com a ordem invertida (reset antes do clear), io.writes seria 1.
    eng, log = fake_engine
    eng.bp.touch(1, 1, PageType.DATA, dirty=True)
    eng.cold()
    assert log.index('bp.clear') < log.index('fm.reset_stats')
    assert log.index('bp.clear') < log.index('bp.reset_stats')
    assert eng.snapshot()['io.writes'] == 0


def test_snapshot_has_every_csv_counter(fake_engine):
    eng, _ = fake_engine
    columns = csv_counter_columns()
    assert 'bp.WORK.misses' in columns and 'io.seeks' in columns  # parser leu o bloco certo
    assert set(columns) <= set(eng.snapshot())


def test_snapshot_reflects_counters(fake_engine):
    eng, _ = fake_engine
    eng.bp.touch(3, 7, PageType.WORK)
    snap = eng.snapshot()
    assert (snap['bp.requests'], snap['bp.misses'], snap['bp.hits']) == (1, 1, 0)
    assert (snap['bp.WORK.misses'], snap['bp.DATA.misses']) == (1, 0)
    assert (snap['io.reads'], snap['io.rand_reads'], snap['io.seeks']) == (1, 1, 1)


def test_m_is_work_capacity(fake_engine):
    eng, _ = fake_engine
    assert eng.M == BufferConfig().areas[PageType.WORK] == 64


def test_close_order_and_context_manager(fake_engine):
    eng, log = fake_engine
    with eng:
        pass
    assert log[-3:] == ['catalog.close', 'bp.flush_all', 'fm.close_all']


def test_reopen_with_other_policy_real_stack(tmp_path):
    """Pilha real (P1 + P2): reabrir a mesma pasta com outra política, sem regenerar."""
    pytest.importorskip('cbd.storage.buffer_pool')
    pytest.importorskip('cbd.files.catalog')
    from cbd.engine import Engine
    scfg = StorageConfig(str(tmp_path), 2048)
    with Engine(scfg, BufferConfig(policy=Policy.LRU)) as eng:
        eng.cold()
        assert all(v == 0 for v in eng.snapshot().values())
        tables = eng.catalog.tables()
    with Engine(scfg, BufferConfig(policy=Policy.MRU)) as eng:
        assert eng.bcfg.policy == Policy.MRU
        assert eng.catalog.tables() == tables
        assert eng.bp.pinned() == 0
