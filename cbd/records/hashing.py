"""hash_value: FNV-1a de 64 bits com a seed misturada e finalizador splitmix64. Camada 3, dono: P2.

Usado nas partições do hash join (P5, seed = 100 + nível), no checksum da validação (P6,
D-P6-05) e no bucket_of do hash para chaves que não são INT (D-P2-14).
Decisões: D-P2-14 (comparação com as alternativas). Ver docs/decisoes/P2.md.
Invariantes: determinístico entre processos (não usa o hash embutido do Python); valores iguais
e do mesmo tipo dão o mesmo hash (-0.0 == 0.0; mas 1 e 1.0 não); valores diferentes dão bytes
diferentes (codificação injetiva). Fora do domínio: TypeError (tipo, datetime), struct.error
(INT fora de 64 bits), UnicodeEncodeError (texto com surrogate).
"""
from datetime import date, datetime
import struct

MASK = (1 << 64) - 1


def _bytes_of(value):
    # Cada valor se delimita sozinho: marca do tipo + tamanho fixo, ou + prefixo de tamanho
    # (texto) ou de número de itens (tupla). Por isso a concatenação dos itens de uma tupla
    # não confunde (1, (2, 3)) com ((1, 2), 3) nem ("a\x1fb",) com ("a", "b") (#41).
    if value is None:
        return b"n"
    if isinstance(value, int):
        return b"i" + struct.pack("<q", value)
    if isinstance(value, float):
        return b"f" + struct.pack("<d", 0.0 if value == 0 else value)
    if isinstance(value, str):
        data = value.encode("utf-8")
        return b"s" + struct.pack("<I", len(data)) + data
    if isinstance(value, date) and not isinstance(value, datetime):   # datetime perderia a hora
        return b"d" + struct.pack("<i", value.toordinal())
    if isinstance(value, tuple):
        return b"t" + struct.pack("<I", len(value)) + b"".join(_bytes_of(item) for item in value)
    raise TypeError(f"Tipo de chave não suportado: {type(value).__name__}")


def hash_value(value, seed=0):
    """Hash de 64 bits de `value` (int, float, str, date, None ou tupla deles), sem I/O.

    Seeds diferentes dão funções independentes (a recursão do hash join depende disso).
    """
    h = (0xCBF29CE484222325 ^ ((seed * 0x9E3779B97F4A7C15) & MASK)) & MASK
    for byte in _bytes_of(value):
        h = ((h ^ byte) * 0x100000001B3) & MASK
    # finalizador splitmix64: espalha os bits baixos, que são os que o "% n" usa
    h ^= h >> 30
    h = (h * 0xBF58476D1CE4E5B9) & MASK
    h ^= h >> 27
    h = (h * 0x94D049BB133111EB) & MASK
    return h ^ (h >> 31)
