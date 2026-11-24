"""Slotted page: o formato de um bloco por dentro. Camada 3, dono: P2.

Implementa: SlottedPage (arquitetura §5.1). Usa: só a biblioteca padrão.
Usada por: heap, hash (dados e índice), temporários e folhas da B+ (P3).
Decisões: D-P2-07 (slotted page em todos os arquivos, cabeçalho de 28 B),
          D-P2-08 (apagar marca o slot; compactação preguiçosa). Ver docs/decisoes/P2.md.

Layout (little-endian), SKS §13.2.2, Fig. 13.6:

    offset  tam  campo
    0       2    n_slots
    2       2    free_end   início da área de registros (cresce para baixo, a partir de B)
    4       2    holes      bytes de registros apagados ou removidos ainda não compactados
    6       2    flags      reservado ao dono do arquivo
    8       4    next       próximo bloco (cadeia de overflow do hash); 0 = nenhum
    12      16   USER       reservado a quem usa a página (P3: is_leaf, prev, next)
    28           diretório: n_slots × (u16 offset, u16 len); offset 0xFFFF = slot apagado

Invariantes: HEADER + 4·n_slots <= free_end <= B; todo slot vivo fica em [free_end, B).
Não faz pin/unpin nem marca dirty: quem chama faz page.mark_dirty() (ou unpin(dirty=True)).
Custo: nenhum I/O; tudo acontece na memória da página já fixada.
"""
import struct

_DEAD = 0xFFFF
_SLOT = struct.Struct("<HH")
_SLOT_SIZE = _SLOT.size  # 4


def _require(ok: bool, msg: str) -> None:
    # AssertionError explícito (o guia P2 §3 pede esse tipo), que não some com python -O.
    if not ok:
        raise AssertionError(f"slotted page corrompida ou uso inválido: {msg}")


class SlottedPage:
    """Opera sobre page.data (bytearray) in-place. Não faz pin/unpin nem dirty."""

    HEADER = 28   # D-P2-07
    USER = 16

    def __init__(self, buf: bytearray):
        self.buf = buf
        self._b = len(buf)
        _require(self._b <= 8192, f"bloco de {self._b} B não cabe nos offsets u16")

    # ----- cabeçalho -----

    def _get16(self, pos: int) -> int:
        return int.from_bytes(self.buf[pos:pos + 2], "little")

    def _set16(self, pos: int, v: int) -> None:
        self.buf[pos:pos + 2] = v.to_bytes(2, "little")

    def n_slots(self) -> int:
        return self._get16(0)

    def _state(self) -> tuple[int, int, int]:
        """(n_slots, free_end, holes), validando o cabeçalho."""
        n, fe, h = self._get16(0), self._get16(2), self._get16(4)
        _require(self.HEADER + _SLOT_SIZE * n <= fe <= self._b, f"n_slots={n}, free_end={fe}")
        _require(h <= self._b - fe, f"holes={h} maior que a área de registros")
        return n, fe, h

    @property
    def next(self) -> int:
        return int.from_bytes(self.buf[8:12], "little")

    @next.setter
    def next(self, bno: int) -> None:
        self.buf[8:12] = bno.to_bytes(4, "little")

    def user_header(self) -> memoryview:
        """Os USER = 16 bytes reservados a quem usa a página (P3: is_leaf, prev, next)."""
        return memoryview(self.buf)[12:12 + self.USER]

    def init(self) -> None:
        """Formata a página vazia: zera o cabeçalho (inclusive next e USER) e free_end = B."""
        self.buf[:self.HEADER] = bytes(self.HEADER)
        self._set16(2, self._b)

    @staticmethod
    def max_record(block_size: int) -> int:
        """Maior registro que cabe num bloco vazio: B − 28 − 4 (um slot). D-P2-06, D-P2-07."""
        return block_size - SlottedPage.HEADER - _SLOT_SIZE

    # ----- diretório -----

    def _slot(self, i: int) -> tuple[int, int]:
        return _SLOT.unpack_from(self.buf, self.HEADER + _SLOT_SIZE * i)

    def _set_slot(self, i: int, off: int, ln: int) -> None:
        _SLOT.pack_into(self.buf, self.HEADER + _SLOT_SIZE * i, off, ln)

    def _first_dead(self, n: int) -> int | None:
        for i in range(n):
            if self._slot(i)[0] == _DEAD:
                return i
        return None

    def get(self, slot: int) -> memoryview | None:
        """View dos bytes do registro, ou None se o slot foi apagado.

        É uma janela para a página, não uma cópia: delete e remove_at não a invalidam (D-P2-08),
        mas insert, insert_at e compact podem mover registros. Copie com bytes(...) antes deles.
        """
        n, fe, _ = self._state()
        _require(0 <= slot < n, f"slot {slot} fora de 0..{n - 1}")
        off, ln = self._slot(slot)
        if off == _DEAD:
            return None
        _require(fe <= off and off + ln <= self._b, f"slot {slot}: offset {off} + len {ln}")
        return memoryview(self.buf)[off:off + ln]

    # ----- espaço -----

    def free_space(self) -> int:
        """Maior registro (bytes) que insert aceita agora, contando os buracos (compacta se preciso).

        D-P2-08: desconta os 4 B de um slot novo só quando não há slot apagado para reaproveitar.
        Numa folha da B+ (sem slots apagados) é também o que insert_at aceita.
        """
        n, fe, h = self._state()
        new_slot = 0 if self._first_dead(n) is not None else _SLOT_SIZE
        return max(0, fe - (self.HEADER + _SLOT_SIZE * n) + h - new_slot)

    def compact(self) -> None:
        """Reempacota os registros vivos no fim do bloco, mantendo os números de slot; holes = 0."""
        n, _, _ = self._state()
        live = [(self._slot(i)[0], i) for i in range(n) if self._slot(i)[0] != _DEAD]
        end = self._b
        # Do registro mais perto do fim para o mais perto do diretório: cada um só anda para a
        # direita, sobre buracos ou sobre o próprio lugar, então nenhum vivo é sobrescrito.
        for off, i in sorted(live, reverse=True):
            ln = self._slot(i)[1]
            end -= ln
            if end != off:
                self.buf[end:end + ln] = self.buf[off:off + ln]
                self._set_slot(i, end, ln)
        self._set16(2, end)
        self._set16(4, 0)

    def _make_room(self, ln: int, new_slot: int) -> int | None:
        """Garante ln bytes contínuos (mais new_slot de diretório); devolve o novo free_end ou None."""
        n, fe, h = self._state()
        contiguous = fe - (self.HEADER + _SLOT_SIZE * n) - new_slot
        if ln > contiguous:
            if ln > contiguous + h:
                return None
            self.compact()        # D-P2-08: compactação preguiçosa, só quando o insert precisa
            fe = self._get16(2)
        return fe - ln

    # ----- inserir e apagar -----

    def insert(self, data: bytes) -> int | None:
        """Guarda data e devolve o slot (o menor apagado, se houver; D-P2-08), ou None se não couber."""
        n = self.n_slots()
        slot = self._first_dead(n)
        off = self._make_room(len(data), 0 if slot is not None else _SLOT_SIZE)
        if off is None:
            return None
        if slot is None:
            slot = n
            self._set16(0, n + 1)
        self.buf[off:off + len(data)] = data
        self._set_slot(slot, off, len(data))
        self._set16(2, off)
        return slot

    def insert_at(self, pos: int, data: bytes) -> bool:
        """Cria um slot novo na posição pos, deslocando os seguintes (ordem lógica das folhas da B+).

        Devolve False, sem mexer na página, se não couber.
        """
        n = self.n_slots()
        _require(0 <= pos <= n, f"posição {pos} fora de 0..{n}")
        off = self._make_room(len(data), _SLOT_SIZE)
        if off is None:
            return False
        a = self.HEADER + _SLOT_SIZE * pos
        b = self.HEADER + _SLOT_SIZE * n
        self.buf[a + _SLOT_SIZE:b + _SLOT_SIZE] = self.buf[a:b]
        self.buf[off:off + len(data)] = data
        self._set_slot(pos, off, len(data))
        self._set16(0, n + 1)
        self._set16(2, off)
        return True

    def delete(self, slot: int) -> None:
        """Marca o slot como apagado; o número continua reservado (RID estável). Não move bytes."""
        n, _, h = self._state()
        _require(0 <= slot < n, f"slot {slot} fora de 0..{n - 1}")
        off, ln = self._slot(slot)
        _require(off != _DEAD, f"slot {slot} já apagado")
        self._set_slot(slot, _DEAD, ln)
        self._set16(4, h + ln)    # D-P2-08: o espaço vira buraco até um insert precisar dele

    def remove_at(self, pos: int) -> None:
        """Tira a entrada pos do diretório, deslocando as seguintes (só B+). Não move registros."""
        n, _, h = self._state()
        _require(0 <= pos < n, f"posição {pos} fora de 0..{n - 1}")
        off, ln = self._slot(pos)
        a = self.HEADER + _SLOT_SIZE * pos
        b = self.HEADER + _SLOT_SIZE * n
        self.buf[a:b - _SLOT_SIZE] = self.buf[a + _SLOT_SIZE:b]
        self._set16(0, n - 1)
        if off != _DEAD:
            self._set16(4, h + ln)
