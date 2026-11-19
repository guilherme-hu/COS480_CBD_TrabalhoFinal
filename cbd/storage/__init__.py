"""Camada 4 — Gerente de Arquivos e Gerente de Buffer Pool (P1)."""
from cbd.storage.buffer_pool import BufferPool, Frame, Page
from cbd.storage.file_manager import FileManager
from cbd.storage.policies import ReplacementPolicy, make_policy

__all__ = ["FileManager", "BufferPool", "Page", "Frame", "ReplacementPolicy", "make_policy"]
