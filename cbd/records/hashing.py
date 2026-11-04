"""FNV-1a + finalizador splitmix64, conforme contrato P2 §3.

Compartilhado pela referência e pelos futuros arquivos/operadores do SGBD.
"""
from datetime import date
import struct

MASK = (1 << 64) - 1


def _bytes_of(value):
    if value is None:
        return b"n"
    if isinstance(value, int):
        return b"i" + struct.pack("<q", value)
    if isinstance(value, float):
        return b"f" + struct.pack("<d", 0.0 if value == 0 else value)
    if isinstance(value, str):
        return b"s" + value.encode("utf-8")
    if isinstance(value, date):
        return b"d" + struct.pack("<i", value.toordinal())
    if isinstance(value, tuple):
        return b"\x1f".join(_bytes_of(item) for item in value)
    raise TypeError(f"Tipo de chave não suportado: {type(value).__name__}")


def hash_value(value, seed=0):
    h = (0xCBF29CE484222325 ^ ((seed * 0x9E3779B97F4A7C15) & MASK)) & MASK
    for byte in _bytes_of(value):
        h = ((h ^ byte) * 0x100000001B3) & MASK
    h ^= h >> 30
    h = (h * 0xBF58476D1CE4E5B9) & MASK
    h ^= h >> 27
    h = (h * 0x94D049BB133111EB) & MASK
    return h ^ (h >> 31)
