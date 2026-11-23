"""Formato do registro em bytes e chaves que preservam a ordem. Camada 3, dono: P2.

Implementa: RecordCodec, key_size, encode_key, decode_key (arquitetura §5.1). Usa: só cbd.contracts.
Decisões: D-P2-01 (variável com prefixo u16), D-P2-02 (NULL = sentinela, sem bitmap),
D-P2-03 (LIST: itens (u8 len, bytes), sem contagem), D-P2-04 (estruturado = string),
D-P2-05 (chave em bytes big-endian com sinal ajustado; INT e FLOAT em 8 B). Ver docs/decisoes/P2.md.

Layout do registro:
    [parte fixa: INT '<q' | FLOAT '<d' | DATE '<i' (ordinal) | CHAR size bytes, na ordem do esquema]
    [variáveis, na ordem do esquema: u16 len + bytes; len = 0xFFFF é NULL]
Sentinelas de NULL: INT −2⁶³, FLOAT NaN, DATE 0, CHAR 0xFF no 1º byte (utf-8 nunca começa com 0xFF).
Invariantes: decode(encode(r)) == r; decode nunca lê fora de `b` (levanta ValueError).
Custo: nenhum I/O. Um pack/unpack de struct pré-compilado por registro na parte fixa.
"""
from datetime import date
import math
import struct

from cbd.contracts import Field, FType, Record, RecordTooLarge, Schema

MAX_LIST = 16                 # D-P2-03: itens por LIST
_NULL_LEN = 0xFFFF            # D-P2-02: tamanho de variável que marca NULL
_INT_NULL = -2**63            # D-P2-02: sentinela de INT (fica fora do domínio aceito)
_SIGN = 1 << 63
_ONES = (1 << 64) - 1
_U16 = struct.Struct("<H")
_CODES = {FType.INT: "q", FType.FLOAT: "d", FType.DATE: "i"}
_NULLS = {FType.INT: _INT_NULL, FType.FLOAT: math.nan, FType.DATE: 0, FType.CHAR: b"\xff"}
# ponytail: = SlottedPage.max_record(B) = B − HEADER(28) − slot(4); trocar pela chamada quando a #40 existir.
_PAGE_OVERHEAD = 32


def _corrupt(what):
    return ValueError(f"registro corrompido: {what}")


def _text(v, f, char):
    """str → utf-8 de até f.size bytes. CHAR não aceita \\x00 (o decode corta os \\x00 finais)."""
    if not isinstance(v, str):
        raise ValueError(f"{f.name}: esperava str, veio {type(v).__name__}")
    if char and "\x00" in v:
        raise ValueError(f"{f.name}: CHAR não pode conter \\x00")
    b = v.encode("utf-8")
    if len(b) > f.size:
        raise ValueError(f"{f.name}: {len(b)} bytes > size {f.size}")
    return b


def _untext(raw, f):
    if len(raw) > f.size:
        raise _corrupt(f"{f.name} com {len(raw)} bytes > size {f.size}")
    try:
        return bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        raise _corrupt(f"{f.name} não é utf-8") from None


def _number(v, f):
    """Valida INT/FLOAT/DATE fora do caminho do struct, que daria struct.error genérico."""
    t = f.ftype
    if t is FType.INT:
        if not isinstance(v, int) or not _INT_NULL < v < _SIGN:
            raise ValueError(f"{f.name}: INT fora de (−2⁶³, 2⁶³) ou não inteiro: {v!r}")
        return v
    if t is FType.FLOAT:
        if not isinstance(v, (int, float)) or math.isnan(v):
            raise ValueError(f"{f.name}: FLOAT inválido (NaN é a sentinela de NULL): {v!r}")
        return float(v)
    if not isinstance(v, date):
        raise ValueError(f"{f.name}: esperava date, veio {type(v).__name__}")
    return v.toordinal()


class RecordCodec:
    """Converte Record (tuple) ↔ bytes no layout acima.

    block_size: se dado, `encode` levanta RecordTooLarge quando o registro não cabe numa
    slotted page de B bytes (registros não estendidos). Sem ele, só os limites do formato valem.
    """

    def __init__(self, schema: Schema, block_size: int | None = None):
        self.schema = schema
        self._n = len(schema.fields)
        self._fixed, self._var, fmt, var_max = [], [], "<", 0
        for i, f in enumerate(schema.fields):
            if f.ftype in _CODES:
                fmt += _CODES[f.ftype]
                self._fixed.append((i, f))
            elif f.ftype is FType.CHAR:
                if f.size < 1:
                    raise ValueError(f"{f.name}: CHAR precisa de size ≥ 1")
                fmt += f"{f.size}s"
                self._fixed.append((i, f))
            elif f.ftype is FType.VARCHAR:
                if not 1 <= f.size < _NULL_LEN:
                    raise ValueError(f"{f.name}: VARCHAR precisa de 1 ≤ size < {_NULL_LEN}")
                self._var.append((i, f))
                var_max += 2 + f.size
            elif f.ftype is FType.LIST:
                if not 1 <= f.size <= 255:                   # D-P2-03: tamanho do item em u8
                    raise ValueError(f"{f.name}: item de LIST precisa de 1 ≤ size ≤ 255")
                self._var.append((i, f))
                var_max += 2 + MAX_LIST * (1 + f.size)
            else:
                raise ValueError(f"{f.name}: tipo desconhecido {f.ftype}")
        self._struct = struct.Struct(fmt)
        self.max_size = self._struct.size + var_max
        self._limit = None if block_size is None else block_size - _PAGE_OVERHEAD

    # ------------------------------------------------------------ encode
    def encode(self, rec: Record) -> bytes:
        if len(rec) != self._n:
            raise ValueError(f"{self.schema.name}: {len(rec)} valores para {self._n} campos")
        vals = []
        for i, f in self._fixed:
            v = rec[i]
            if v is None:
                if not f.nullable:
                    raise ValueError(f"{f.name}: None em campo obrigatório")
                vals.append(_NULLS[f.ftype])
            elif f.ftype is FType.CHAR:
                vals.append(_text(v, f, char=True))        # struct preenche com \x00 até size
            else:
                vals.append(_number(v, f))
        out = bytearray(self._struct.pack(*vals))
        for i, f in self._var:
            v = rec[i]
            if v is None:
                if not f.nullable:
                    raise ValueError(f"{f.name}: None em campo obrigatório")
                out += _U16.pack(_NULL_LEN)
                continue
            if f.ftype is FType.VARCHAR:
                data = _text(v, f, char=False)
            else:
                if isinstance(v, str) or not isinstance(v, (tuple, list)):
                    raise ValueError(f"{f.name}: LIST precisa ser tuple ou list de str")
                if len(v) > MAX_LIST:
                    raise ValueError(f"{f.name}: {len(v)} itens > MAX_LIST {MAX_LIST}")
                data = b"".join(bytes((len(b),)) + b for b in (_text(x, f, char=False) for x in v))
            out += _U16.pack(len(data))
            out += data
        if self._limit is not None and len(out) > self._limit:
            raise RecordTooLarge(f"{self.schema.name}: {len(out)} bytes > {self._limit} (registro não estendido)")
        return bytes(out)

    # ------------------------------------------------------------ decode
    def decode(self, b: bytes | memoryview) -> Record:
        n = len(b)
        if n < self._struct.size:
            raise _corrupt(f"{n} bytes < parte fixa {self._struct.size}")
        rec = [None] * self._n
        for (i, f), x in zip(self._fixed, self._struct.unpack_from(b, 0)):
            t = f.ftype
            null = (x[0] == 0xFF if t is FType.CHAR else math.isnan(x) if t is FType.FLOAT
                    else x == _NULLS[t])
            if null:
                if not f.nullable:
                    raise _corrupt(f"NULL em {f.name}, que é obrigatório")
            elif t is FType.CHAR:
                rec[i] = _untext(x.rstrip(b"\x00"), f)
            elif t is FType.DATE:
                if x < 1:
                    raise _corrupt(f"{f.name}: ordinal {x}")
                rec[i] = date.fromordinal(x)               # ordinal grande demais: ValueError
            else:
                rec[i] = x
        pos = self._struct.size
        for i, f in self._var:
            if pos + 2 > n:
                raise _corrupt(f"falta o tamanho de {f.name}")
            ln = _U16.unpack_from(b, pos)[0]
            pos += 2
            if ln == _NULL_LEN:
                if not f.nullable:
                    raise _corrupt(f"NULL em {f.name}, que é obrigatório")
                continue
            end = pos + ln
            if end > n:
                raise _corrupt(f"{f.name} passa do fim do registro")
            if f.ftype is FType.VARCHAR:
                rec[i] = _untext(b[pos:end], f)
            else:
                items = []
                while pos < end:
                    k = b[pos]
                    pos += 1
                    if len(items) == MAX_LIST or k > f.size or pos + k > end:
                        raise _corrupt(f"item {len(items)} de {f.name}")
                    items.append(_untext(b[pos:pos + k], f))
                    pos += k
                rec[i] = tuple(items)
            pos = end
        if pos != n:
            raise _corrupt(f"{n - pos} bytes sobrando")
        return tuple(rec)


# ------------------------------------------------------------------ chaves (D-P2-05)
def key_size(f: Field) -> int:
    """Bytes da chave codificada: INT/FLOAT 8, DATE 4, CHAR/VARCHAR size. LIST: TypeError."""
    if f.ftype in (FType.INT, FType.FLOAT):
        return 8
    if f.ftype is FType.DATE:
        return 4
    if f.ftype in (FType.CHAR, FType.VARCHAR):
        return f.size
    raise TypeError(f"{f.name}: {f.ftype} não pode ser chave")


def encode_key(f: Field, v) -> bytes:
    """Valor → key_size(f) bytes com a mesma ordem do valor (comparação byte a byte).

    INT: v + 2⁶³ em big-endian (inverte o bit de sinal). FLOAT: IEEE big-endian; positivo inverte
    o bit de sinal, negativo inverte todos; −0,0 vira 0,0. DATE: ordinal em 4 B big-endian.
    CHAR/VARCHAR: utf-8 preenchido com \\x00 (a ordem é a dos code points).
    Levanta ValueError para NULL, sentinela, NaN e valor fora do tipo (D-P2-05, opção N1).
    """
    if v is None:
        raise ValueError(f"{f.name}: chave não pode ser NULL (D-P2-05)")
    t = f.ftype
    if t is FType.INT:
        return (_number(v, f) + _SIGN).to_bytes(8, "big")
    if t is FType.FLOAT:
        u = int.from_bytes(struct.pack(">d", _number(v, f) + 0.0), "big")    # −0,0 + 0,0 = 0,0
        return (u ^ _ONES if u & _SIGN else u | _SIGN).to_bytes(8, "big")
    if t is FType.DATE:
        return _number(v, f).to_bytes(4, "big")
    if t in (FType.CHAR, FType.VARCHAR):
        return _text(v, f, char=True).ljust(f.size, b"\x00")              # \x00 proibido também no VARCHAR
    raise TypeError(f"{f.name}: {t} não pode ser chave")


def decode_key(f: Field, b: bytes | memoryview):
    """Inverso de encode_key. ValueError se len(b) != key_size(f)."""
    if len(b) != key_size(f):
        raise ValueError(f"{f.name}: chave com {len(b)} bytes, esperava {key_size(f)}")
    t = f.ftype
    u = int.from_bytes(b, "big")
    if t is FType.INT:
        return u - _SIGN
    if t is FType.FLOAT:
        u = u ^ _SIGN if u & _SIGN else u ^ _ONES
        return struct.unpack(">d", u.to_bytes(8, "big"))[0]
    if t is FType.DATE:
        return date.fromordinal(u)
    return _untext(bytes(b).rstrip(b"\x00"), f)
