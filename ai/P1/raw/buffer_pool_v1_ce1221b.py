"""Gerente de Buffer Pool (Camada 4 — P1).

Gerencia áreas fixas de memória por PageType (CATALOG, INDEX, DATA, WORK),
controle de fixação (pin/unpin), marcação de páginas sujas (dirty),
gravação forçada (force), descarte imediato (toss-immediate), leitura
antecipada (prefetch) e reserva de memória de trabalho (reserve_work).
"""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from typing import Iterator

from cbd.contracts import (
    BufferConfig,
    BufferCounters,
    BufferFullError,
    PageType,
)
from cbd.storage.file_manager import FileManager
from cbd.storage.policies import ReplacementPolicy, make_policy


class Frame:
    """Representa um frame (página residente) no Buffer Pool."""

    __slots__ = (
        "idx",
        "area",
        "fid",
        "bno",
        "ptype",
        "data",
        "pin_count",
        "dirty",
        "ref",
    )

    def __init__(self, idx: int, ptype: PageType, block_size: int) -> None:
        self.idx: int = idx
        self.area: PageType = ptype
        self.fid: int = -1
        self.bno: int = -1
        self.ptype: PageType = ptype
        self.data: bytearray = bytearray(block_size)
        self.pin_count: int = 0
        self.dirty: bool = False
        self.ref: int = 0

    def mark_dirty(self) -> None:
        self.dirty = True


Page = Frame


class _Area:
    def __init__(self, ptype: PageType, n_frames: int, block_size: int, policy_enum) -> None:
        self.ptype: PageType = ptype
        self.frames: list[Frame] = [
            Frame(i, ptype, block_size) for i in range(n_frames)
        ]
        # Invertido para que pop() entregue frames na ordem 0, 1, 2, ...
        self.free: list[Frame] = list(reversed(self.frames))
        self.policy: ReplacementPolicy = make_policy(policy_enum, self.frames)
        self.tossed: deque[Frame] = deque()
        self.stats: BufferCounters = BufferCounters()


class BufferPool:
    """Gerente de Buffer Pool da Camada 4."""

    def __init__(self, fm: FileManager, cfg: BufferConfig) -> None:
        self.fm: FileManager = fm
        self.block_size: int = fm.block_size
        self.cfg: BufferConfig = cfg

        self._table: dict[tuple[int, int], Frame] = {}
        self._areas: dict[PageType, _Area] = {}
        self.stats: BufferCounters = BufferCounters()
        self.stats_by_area: dict[PageType, BufferCounters] = {}

        for ptype in PageType:
            cap = cfg.areas.get(ptype, 0)
            area = _Area(ptype, cap, self.block_size, cfg.policy)
            self._areas[ptype] = area
            self.stats_by_area[ptype] = area.stats

        self._zero_block: bytes = bytes(self.block_size)

    def _evict_frame(self, area: _Area, f: Frame) -> None:
        """Expulsa um frame residente (deve ter pin_count == 0), gravando se dirty."""
        if (f.fid, f.bno) in self._table:
            del self._table[(f.fid, f.bno)]
        area.policy.removed(f)
        if f.dirty:
            self.fm.write(f.fid, f.bno, f.data)
            f.dirty = False
            area.stats.dirty_evictions += 1
            self.stats.dirty_evictions += 1
        f.fid = -1
        f.bno = -1
        f.pin_count = 0
        f.ref = 0

    def _victim_or_none(self, area: _Area) -> Frame | None:
        if area.free:
            return area.free.pop()

        while area.tossed:
            cand = area.tossed.popleft()
            if (
                cand.fid >= 0
                and self._table.get((cand.fid, cand.bno)) is cand
                and cand.pin_count == 0
            ):
                self._evict_frame(area, cand)
                return cand

        cand = area.policy.victim()
        if cand is not None:
            self._evict_frame(area, cand)
            return cand
        return None

    def _victim(self, area: _Area) -> Frame:
        f = self._victim_or_none(area)
        if f is None:
            raise BufferFullError(
                f"área {area.ptype.value}: {len(area.frames)} frames, todos fixados"
            )
        return f

    def _install(
        self,
        f: Frame,
        area: _Area,
        fid: int,
        bno: int,
        ptype: PageType,
        pin_count: int = 1,
        dirty: bool = False,
    ) -> None:
        f.fid = fid
        f.bno = bno
        f.ptype = ptype
        f.pin_count = pin_count
        f.dirty = dirty
        self._table[(fid, bno)] = f
        area.policy.loaded(f)

    def pin(
        self, fid: int, bno: int, ptype: PageType, seq: bool = False
    ) -> Page:
        area = self._areas[ptype]
        area.stats.requests += 1
        self.stats.requests += 1

        f = self._table.get((fid, bno))
        if f is not None:
            assert f.ptype == ptype, (
                f"Bloco ({fid}, {bno}) residente na área {f.ptype}, mas pedido como {ptype}"
            )
            area.stats.hits += 1
            self.stats.hits += 1
            f.pin_count += 1
            if area.tossed:
                try:
                    area.tossed.remove(f)
                except ValueError:
                    pass
            area.policy.accessed(f)
            return f

        area.stats.misses += 1
        self.stats.misses += 1
        f = self._victim(area)
        try:
            raw = self.fm.read(fid, bno)
        except Exception:
            area.free.append(f)
            raise
        f.data[:] = raw
        self._install(f, area, fid, bno, ptype, pin_count=1, dirty=False)

        if seq and self.cfg.prefetch > 0:
            self._prefetch(area, fid, bno + 1, self.cfg.prefetch)
        return f

    def _prefetch(self, area: _Area, fid: int, start: int, k: int) -> None:
        for b in range(start, start + k):
            if (fid, b) in self._table:
                break
            if not self.fm.is_valid_block(fid, b):
                break
            f = self._victim_or_none(area)
            if f is None:
                break
            try:
                raw = self.fm.read(fid, b)
            except Exception:
                area.free.append(f)
                break
            f.data[:] = raw
            self._install(f, area, fid, b, area.ptype, pin_count=0, dirty=False)
            area.stats.prefetched += 1
            self.stats.prefetched += 1

    def new_page(self, fid: int, ptype: PageType) -> Page:
        area = self._areas[ptype]
        f = self._victim(area)
        try:
            bno = self.fm.allocate(fid)
        except Exception:
            area.free.append(f)
            raise
        f.data[:] = self._zero_block
        self._install(f, area, fid, bno, ptype, pin_count=1, dirty=True)
        return f

    def unpin(
        self, page: Page, dirty: bool = False, toss: bool = False
    ) -> None:
        assert page.pin_count > 0, "unpin sem pin correspondente"
        if dirty:
            page.dirty = True
        page.pin_count -= 1
        area = self._areas[page.ptype]
        area.policy.unpinned(page)
        if toss and self.cfg.toss_immediate and page.pin_count == 0:
            if page not in area.tossed:
                area.tossed.append(page)

    @contextmanager
    def page(
        self, fid: int, bno: int, ptype: PageType, seq: bool = False
    ) -> Iterator[Page]:
        p = self.pin(fid, bno, ptype, seq=seq)
        try:
            yield p
        finally:
            self.unpin(p, dirty=p.dirty)

    def force(self, page: Page) -> None:
        assert page.fid >= 0 and (page.fid, page.bno) in self._table, (
            "force em página não residente"
        )
        self.fm.write(page.fid, page.bno, page.data)
        page.dirty = False
        area = self._areas[page.ptype]
        area.stats.forced_writes += 1
        self.stats.forced_writes += 1

    def flush_file(self, fid: int) -> None:
        dirty_frames = [
            f for f in self._table.values() if f.fid == fid and f.dirty
        ]
        dirty_frames.sort(key=lambda f: f.bno)
        for f in dirty_frames:
            self.fm.write(f.fid, f.bno, f.data)
            f.dirty = False

    def flush_all(self) -> None:
        dirty_frames = [f for f in self._table.values() if f.dirty]
        dirty_frames.sort(key=lambda f: (f.fid, f.bno))
        for f in dirty_frames:
            self.fm.write(f.fid, f.bno, f.data)
            f.dirty = False

    def _discard_frame(self, f: Frame) -> None:
        area = self._areas[f.ptype]
        self._table.pop((f.fid, f.bno), None)
        area.policy.removed(f)
        if area.tossed:
            try:
                area.tossed.remove(f)
            except ValueError:
                pass
        f.fid = -1
        f.bno = -1
        f.pin_count = 0
        f.dirty = False
        f.ref = 0
        area.free.append(f)

    def drop_file(self, fid: int) -> None:
        frames = [f for f in self._table.values() if f.fid == fid]
        for f in frames:
            assert f.pin_count == 0, (
                f"drop_file({fid}) com página ({f.fid}, {f.bno}) fixada (pin_count={f.pin_count})"
            )
        for f in frames:
            self._discard_frame(f)

    def free_block(self, fid: int, bno: int) -> None:
        f = self._table.get((fid, bno))
        if f is not None:
            assert f.pin_count == 0, (
                f"free_block({fid}, {bno}) com página fixada (pin_count={f.pin_count})"
            )
            self._discard_frame(f)
        self.fm.free(fid, bno)

    def clear(self) -> None:
        assert self.pinned() == 0, (
            f"clear() chamado com {self.pinned()} página(s) ainda fixada(s)"
        )
        self.flush_all()
        self._table.clear()
        for area in self._areas.values():
            area.tossed.clear()
            area.policy.reset()
            for f in area.frames:
                f.fid = -1
                f.bno = -1
                f.pin_count = 0
                f.dirty = False
                f.ref = 0
            area.free = list(reversed(area.frames))

    def capacity(self, ptype: PageType) -> int:
        return len(self._areas[ptype].frames)

    def pinned(self, ptype: PageType | None = None) -> int:
        if ptype is not None:
            return sum(1 for f in self._areas[ptype].frames if f.pin_count > 0)
        return sum(
            1
            for area in self._areas.values()
            for f in area.frames
            if f.pin_count > 0
        )

    @contextmanager
    def reserve_work(self, n: int) -> Iterator[int]:
        if n < 0:
            raise ValueError(f"Reserva inválida: n={n}")
        area = self._areas[PageType.WORK]
        taken: list[Frame] = []
        try:
            for _ in range(n):
                taken.append(self._victim(area))
            yield n
        finally:
            area.free.extend(reversed(taken))

    def reset_stats(self) -> None:
        self.stats = BufferCounters()
        for ptype, area in self._areas.items():
            area.stats = BufferCounters()
            self.stats_by_area[ptype] = area.stats
