"""Fixtures compartilhados para os testes do SGBD (P1)."""
from __future__ import annotations

import pytest

from cbd.contracts import BufferConfig, StorageConfig
from cbd.storage.buffer_pool import BufferPool
from cbd.storage.file_manager import FileManager


@pytest.fixture
def bp(tmp_path):
    """Fixture da Camada 4 (FileManager + BufferPool) sem depender de Catalog."""
    fm = FileManager(StorageConfig(root_dir=str(tmp_path)))
    pool = BufferPool(fm, BufferConfig())
    yield pool
    assert pool.pinned() == 0, "pin sem unpin"
    pool.flush_all()
    fm.close_all()


@pytest.fixture
def eng(tmp_path):
    """Fixture completo do Engine (para quando Catalog estiver disponível)."""
    from cbd.engine import Engine

    e = Engine(StorageConfig(root_dir=str(tmp_path)), BufferConfig())
    yield e
    assert e.bp.pinned() == 0, "pin sem unpin"
    e.close()
