"""Formatos locais da B+ (P3), sem acesso a disco ou escolha de preenchimento.

D-P3-04/05/06/07: RID físico, desempate u32, internos compactos e folhas P2.
SKS §§14.3.1/14.3.5/14.4.1; docs/decisoes/P3.md e P3_PLANO_ATUAL.md §4.
Helpers privados: operam numa página já fixada; o chamador faz dirty/unpin.
Não são uma árvore persistente: metadados, carga e validação pública estão abertos.
"""
import struct

from cbd.contracts import RID, RecordTooLarge
from cbd.records.codec import RecordCodec, encode_key, key_size
from cbd.records.slotted_page import SlottedPage

_COUNT = struct.Struct("<H")
_BLOCK = struct.Struct("<I")
_RID = struct.Struct("<IH")
_ID = struct.Struct(">I")


def _encode_rid(ptr):
    """RID físico → 6 B, como P2 (D-P3-04); nenhum I/O."""
    return _RID.pack(*ptr)


def _decode_rid(raw):
    """6 B → RID físico; struct recusa comprimento diferente. Nenhum I/O."""
    return RID(*_RID.unpack(raw))


class _InternalPage:
    """Visão do interno D-P3-06 sobre bytearray; k vem do perfil da árvore.

    Só formata/consulta uma página, sem alocar filhos ou escolher ocupação.
    Exceções ValueError aqui são verificações internas; API final de erros: L05.
    """

    HEADER = 6

    def __init__(self, buf, separator_size):
        if separator_size <= 0 or len(buf) < self.HEADER:
            raise ValueError("interno exige k positivo e cabeçalho de 6 B")
        self.buf, self.k = buf, separator_size

    @property
    def max_children(self):
        """Fan-out físico, não preenchimento: 1 + piso((B−6)/(k+4))."""
        return 1 + (len(self.buf) - self.HEADER) // (self.k + _BLOCK.size)

    @property
    def count(self):
        """Separadores usados; recusa contador que ultrapassa a página."""
        n = _COUNT.unpack_from(self.buf)[0]
        if n >= self.max_children:
            raise ValueError("contador de separadores ultrapassa a página")
        return n

    def write(self, children, separators):
        """Escreve filhos/separadores completos já ordenados, sem I/O.

        Valida antes de modificar: n separadores e n+1 filhos, limite físico,
        chaves estritamente crescentes e blocos u32 não nulos. Não cria filhos.
        """
        n = len(separators)
        if len(children) != n + 1 or n >= self.max_children or n > 65535:
            raise ValueError("quantidade de filhos/separadores inválida")
        if any(len(s) != self.k for s in separators):
            raise ValueError("separador com largura diferente de k")
        if any(a >= b for a, b in zip(separators, separators[1:])):
            raise ValueError("separadores devem ser estritamente crescentes")
        if any(not isinstance(b, int) or not 1 <= b <= 0xFFFFFFFF for b in children):
            raise ValueError("filho precisa ser bloco u32 não nulo")
        _COUNT.pack_into(self.buf, 0, n)
        _BLOCK.pack_into(self.buf, 2, children[0])
        for i, separator in enumerate(separators):
            pos = self.HEADER + i * (self.k + _BLOCK.size)
            self.buf[pos:pos + self.k] = separator
            _BLOCK.pack_into(self.buf, pos + self.k, children[i + 1])

    def separator(self, i):
        """Cópia dos bytes do separador i; não mantém view da página."""
        if not 0 <= i < self.count:
            raise IndexError(i)
        pos = self.HEADER + i * (self.k + _BLOCK.size)
        return bytes(self.buf[pos:pos + self.k])

    def child(self, i):
        """Bloco do filho i (0…n), folha ou interno; nunca bloco+slot."""
        if not 0 <= i <= self.count:
            raise IndexError(i)
        pos = 2 if i == 0 else self.HEADER + (i - 1) * (self.k + 4) + self.k
        return _BLOCK.unpack_from(self.buf, pos)[0]

    def child_for(self, key):
        """Busca binária nos separadores completos; igualdade vai à direita.

        D-P3-06. Chamada com chave codificada de k bytes, incluindo ID quando
        aplicável. Limites por campo nas consultas públicas ainda são L05.
        """
        if len(key) != self.k:
            raise ValueError("chave de busca com largura diferente de k")
        lo, hi = 0, self.count
        while lo < hi:
            mid = (lo + hi) // 2
            if key < self.separator(mid):
                hi = mid
            else:
                lo = mid + 1
        return self.child(lo)


class _PrimaryEntries:
    """Codec das entradas primárias UNIQUE/NOT UNIQUE, sem novo formato P2.

    Não atribui IDs nem ordena/corta páginas. A carga futura fornece o contador
    global desde 1 (D-P3-05); preenchimento/últimos grupos permanecem L03.
    """

    def __init__(self, schema, field_name, unique, block_size):
        self.codec = RecordCodec(schema)
        self.field = schema.get(field_name)
        self.column = schema.idx(field_name)
        self.unique = unique
        self.k = key_size(self.field) + (0 if unique else _ID.size)
        self.max_record = SlottedPage.max_record(block_size) - (0 if unique else _ID.size)

    def encode(self, record, identifier=None):
        """Entrada pronta para SlottedPage; recusa tamanho antes de gravar.

        Registro não vazio e chave válida pelo codec de P2. NOT UNIQUE exige
        ID u32 positivo; UNIQUE não aceita prefixo. Custo: CPU, nenhum I/O.
        """
        raw = self.codec.encode(record)
        encode_key(self.field, record[self.column])
        if not raw:
            raise ValueError("registro de 0 B")
        if len(raw) > self.max_record:
            raise RecordTooLarge(f"registro de {len(raw)} B; máximo {self.max_record} B")
        if self.unique:
            if identifier is not None:
                raise ValueError("UNIQUE não armazena identificador de desempate")
            return raw
        if not isinstance(identifier, int) or not 1 <= identifier <= 0xFFFFFFFF:
            raise ValueError("desempate precisa ser u32 desde 1, sem wrap")
        return _ID.pack(identifier) + raw

    def decode(self, raw):
        """Entrada → registro P2, retirando só o prefixo NOT UNIQUE; nenhum I/O."""
        return self.codec.decode(raw if self.unique else raw[_ID.size:])

    def order_key(self, raw):
        """Chave ordenável completa para comparar/separar; não copia chave na folha."""
        record = self.decode(raw)
        key = encode_key(self.field, record[self.column])
        return key if self.unique else key + bytes(raw[:_ID.size])
