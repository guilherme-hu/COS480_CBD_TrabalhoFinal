"""Versão bruta ingênua gerada inicialmente para FileManager + BufferPool (para comparação no Post-Mortem).

Contém defeitos clássicos de código gerado por IA (§7.1 do enunciado e §5 do guia P1):
1. `unpin(page, dirty=False)` atribui `page.dirty = dirty` diretamente, apagando `dirty=True`
   anteriormente marcado via `page.mark_dirty()` ou `new_page()`.
2. `_victim()` na política LRU/MRU não verifica `f.pin_count == 0` ao escolher vítima,
   podendo expulsar uma página atualmente fixada (em uso).
3. `reset_stats()` no FileManager não reinicia `_head = None`, fazendo o primeiro acesso
   após `cold()` parecer sequencial caso coincida com `bno - 1` anterior.
4. `_prefetch()` chama `pin()` internamente sem `unpin()`, deixando os blocos lidos
   antecipadamente com `pin_count = 1` (vazamento de pin).
5. `free_block()` libera o bloco no `FileManager`, mas esquece de remover o frame residente
   de `_table`, fazendo uma alocação futura do mesmo `bno` enxergar dados antigos no cache.
"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
import struct

from cbd.contracts import BufferConfig, BufferCounters, BufferFullError, IOCounters, PageType, Policy, StorageConfig

HEADER_STRUCT = struct.Struct("<4sIIII12x")


class NaiveFileManager:
    def __init__(self, cfg: StorageConfig):
        self.cfg = cfg
        self.block_size = cfg.block_size
        self.root_dir = Path(cfg.root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._next_fid = 1
        self._files = {}
        self._high_water = {}
        self._free = {}
        self.io = IOCounters()
        self.io_by_file = {}
        self._head = None

    def create(self, name: str) -> int:
        fid = self._next_fid
        self._next_fid += 1
        p = self.root_dir / f"{name}.dat"
        f = open(p, "w+b")
        f.write(b"\x00" * self.block_size)
        self._files[fid] = f
        self._high_water[fid] = 0
        self._free[fid] = []
        return fid

    def allocate(self, fid: int) -> int:
        if self._free[fid]:
            return self._free[fid].pop()
        self._high_water[fid] += 1
        return self._high_water[fid]

    def free(self, fid: int, bno: int) -> None:
        self._free[fid].append(bno)

    def read(self, fid: int, bno: int) -> bytearray:
        f = self._files[fid]
        f.seek(bno * self.block_size)
        # DEFEITO: preenche com zeros se ler além do fim físico em vez de levantar ValueError
        raw = f.read(self.block_size)
        if len(raw) < self.block_size:
            raw = raw + b"\x00" * (self.block_size - len(raw))
        seq = self._head == (fid, bno - 1)
        self._head = (fid, bno)
        if seq:
            self.io.seq_reads += 1
        else:
            self.io.rand_reads += 1
        return bytearray(raw)

    def write(self, fid: int, bno: int, data: bytes | bytearray) -> None:
        f = self._files[fid]
        f.seek(bno * self.block_size)
        f.write(data)
        seq = self._head == (fid, bno - 1)
        self._head = (fid, bno)
        if seq:
            self.io.seq_writes += 1
        else:
            self.io.rand_writes += 1

    def reset_stats(self) -> None:
        self.io = IOCounters()
        self.io_by_file.clear()
        # DEFEITO: esqueceu de zerar self._head = None!

    def close_all(self) -> None:
        for f in self._files.values():
            f.close()
        self._files.clear()


class NaiveFrame:
    def __init__(self, idx: int, ptype: PageType, block_size: int):
        self.idx = idx
        self.fid = -1
        self.bno = -1
        self.ptype = ptype
        self.data = bytearray(block_size)
        self.pin_count = 0
        self.dirty = False

    def mark_dirty(self):
        self.dirty = True


class NaiveBufferPool:
    def __init__(self, fm: NaiveFileManager, cfg: BufferConfig):
        self.fm = fm
        self.cfg = cfg
        self.block_size = fm.block_size
        self.stats = BufferCounters()
        cap = cfg.areas.get(PageType.DATA, 4)
        self.frames = [NaiveFrame(i, PageType.DATA, self.block_size) for i in range(cap)]
        self.free = list(reversed(self.frames))
        self._table: dict[tuple[int, int], NaiveFrame] = {}
        self._order: OrderedDict[int, NaiveFrame] = OrderedDict()

    def _victim(self) -> NaiveFrame:
        if self.free:
            return self.free.pop()
        it = reversed(self._order.values()) if self.cfg.policy == Policy.MRU else self._order.values()
        for f in it:
            if f.pin_count == 0:
                del self._table[(f.fid, f.bno)]
                del self._order[f.idx]
                if f.dirty:
                    self.fm.write(f.fid, f.bno, f.data)
                    self.stats.dirty_evictions += 1
                return f
        raise BufferFullError("Buffer cheio")

    def pin(self, fid: int, bno: int, ptype: PageType = PageType.DATA, seq: bool = False) -> NaiveFrame:
        self.stats.requests += 1
        if (fid, bno) in self._table:
            f = self._table[(fid, bno)]
            self.stats.hits += 1
            f.pin_count += 1
            self._order.move_to_end(f.idx)
            return f
        self.stats.misses += 1
        f = self._victim()
        f.data[:] = self.fm.read(fid, bno)
        f.fid, f.bno, f.ptype = fid, bno, ptype
        f.pin_count = 1
        f.dirty = False
        self._table[(fid, bno)] = f
        self._order[f.idx] = f
        if seq and self.cfg.prefetch > 0:
            # DEFEITO: chama pin() no prefetch e vaza pin_count = 1!
            for b in range(bno + 1, bno + 1 + self.cfg.prefetch):
                if b <= self.fm._high_water[fid] and (fid, b) not in self._table:
                    self.pin(fid, b, ptype, seq=False)
        return f

    def new_page(self, fid: int, ptype: PageType = PageType.DATA) -> NaiveFrame:
        bno = self.fm.allocate(fid)
        f = self._victim()
        f.data[:] = b"\x00" * self.block_size
        f.fid, f.bno, f.ptype = fid, bno, ptype
        f.pin_count = 1
        f.dirty = True
        self._table[(fid, bno)] = f
        self._order[f.idx] = f
        return f

    def unpin(self, page: NaiveFrame, dirty: bool = False, toss: bool = False) -> None:
        # DEFEITO GRAVE: sobrescreve page.dirty com False quando o chamador usou page.mark_dirty()
        # ou new_page() e depois chamou unpin(page) sem passar dirty=True!
        page.dirty = dirty
        page.pin_count -= 1

    def free_block(self, fid: int, bno: int) -> None:
        # DEFEITO: não remove o frame de self._table antes de liberar no FileManager!
        self.fm.free(fid, bno)
