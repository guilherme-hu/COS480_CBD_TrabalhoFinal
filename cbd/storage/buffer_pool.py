"""Gerente de Buffer Pool (Camada 4, dono: P1 — Vivian).

Implementa: BufferPool, Page/Frame (cbd/contracts.py). Usa: FileManager, ReplacementPolicy.
Decisões: D-P1-06 (1 página = 1 bloco, 264 páginas), D-P1-07 (4 áreas fixas isoladas),
D-P1-08 (políticas LRU/MRU), D-P1-09 (toss-immediate), D-P1-10 (pin_count, dirty e reserve_work),
D-P1-11 (gravação forçada force), D-P1-12 (prefetch sem auto-expulsão). Ver docs/decisoes/P1.md.
Invariantes: nenhum frame com pin_count > 0 é expulso; bp.pinned() == 0 ao fim de cada operação;
1 miss == 1 leitura física fm.read; páginas alteradas marcadas dirty antes de unpin.
Custo esperado: pin com hit = 0 I/Os; pin com miss = 1 leitura (+1 escrita se vítima suja).
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
    """Representa um frame (página residente de 1 bloco) no Buffer Pool (D-P1-06)."""

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
        """Marca a página residente como modificada (dirty = True).

        Custo de I/O: 0 blocos (a gravação física ocorre apenas na expulsão, flush ou force).
        """
        self.dirty = True


Page = Frame


class _Area:
    def __init__(self, ptype: PageType, n_frames: int, block_size: int, policy_enum) -> None:
        # D-P1-07: área fixa isolada por PageType com sua própria instância de política e fila tossed
        self.ptype: PageType = ptype
        self.frames: list[Frame] = [
            Frame(i, ptype, block_size) for i in range(n_frames)
        ]
        # Invertido para que pop() entregue frames na ordem 0, 1, 2, ...
        self.free: list[Frame] = list(reversed(self.frames))
        self.reserved: set[Frame] = set()
        self.policy: ReplacementPolicy = make_policy(policy_enum, self.frames)
        self.tossed: deque[Frame] = deque()
        self.stats: BufferCounters = BufferCounters()


class BufferPool:
    """Gerente de Buffer Pool da Camada 4."""

    def __init__(self, fm: FileManager, cfg: BufferConfig = BufferConfig()) -> None:
        """Inicializa o Buffer Pool com áreas fixas por PageType e frames pré-alocados.

        Parâmetros:
            fm: instância de FileManager que realiza a E/S física em blocos.
            cfg: configuração do buffer (capacidade em páginas por área, política e prefetch em blocos).
        Levanta:
            ValueError: se cfg.prefetch < 0, capacidade negativa ou política não adotada.
        Custo de I/O: 0 blocos.
        """
        if cfg.prefetch < 0:
            raise ValueError(f"prefetch inválido: {cfg.prefetch}")
        self.fm: FileManager = fm
        self.block_size: int = fm.block_size
        self.cfg: BufferConfig = cfg

        self._table: dict[tuple[int, int], Frame] = {}
        self._areas: dict[PageType, _Area] = {}
        self.stats: BufferCounters = BufferCounters()
        self.stats_by_area: dict[PageType, BufferCounters] = {}

        # D-P1-06, D-P1-07: inicializa as 4 áreas fixas (CATALOG, INDEX, DATA, WORK)
        for ptype in PageType:
            cap = cfg.areas.get(ptype, 0)
            if cap < 0:
                raise ValueError(f"Capacidade negativa para área {ptype}: {cap}")
            area = _Area(ptype, cap, self.block_size, cfg.policy)
            self._areas[ptype] = area
            self.stats_by_area[ptype] = area.stats

        self._zero_block: bytes = bytes(self.block_size)

    def _evict_frame(self, area: _Area, f: Frame) -> None:
        """Expulsa um frame residente (deve ter pin_count == 0), gravando se dirty."""
        # Grava no disco ANTES de tirar de _table/policy: se fm.write falhar, o frame não fica órfão.
        if f.dirty:
            if self.fm.is_open(f.fid):
                self.fm.write(f.fid, f.bno, f.data)
                area.stats.dirty_evictions += 1
                self.stats.dirty_evictions += 1
            f.dirty = False
        if (f.fid, f.bno) in self._table:
            del self._table[(f.fid, f.bno)]
        area.policy.removed(f)
        f.fid = -1
        f.bno = -1
        f.pin_count = 0
        f.ref = 0

    def _victim_or_none(
        self, area: _Area, exclude: set[Frame] | None = None
    ) -> Frame | None:
        # 1ª prioridade: frames nunca usados ou já descartados (lista area.free)
        if area.free:
            return area.free.pop()

        # 2ª prioridade (D-P1-09): frames marcados com toss=True no unpin (descarte imediato de scan)
        while area.tossed:
            cand = area.tossed.popleft()
            if (
                cand.fid >= 0
                and self._table.get((cand.fid, cand.bno)) is cand
                and cand.pin_count == 0
            ):
                if exclude is not None and cand in exclude:
                    area.tossed.appendleft(cand)
                    return None
                self._evict_frame(area, cand)
                return cand

        # 3ª prioridade (D-P1-08): consulta a política da área (LRU ou MRU), que só devolve pin_count == 0.
        # Se foi chamado por _prefetch (exclude=batch_frames) e o candidato escolhido pertence ao próprio
        # lote que o _prefetch acabou de instalar, retorna None ANTES de _evict_frame para não expulsá-lo!
        cand = area.policy.victim()
        if cand is not None:
            if exclude is not None and cand in exclude:
                return None
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
        self,
        fid: int,
        bno: int,
        ptype: PageType = PageType.DATA,
        seq: bool = False,
    ) -> Page:
        """Fixa (pin_count += 1) e retorna a página correspondente ao bloco (fid, bno) na área ptype.

        Parâmetros:
            fid: identificador inteiro do arquivo aberto no FileManager.
            bno: número do bloco lógico (0-indexado, em blocos de block_size bytes).
            ptype: área do buffer onde a página deve residir (CATALOG, INDEX, DATA ou WORK).
            seq: dica de varredura sequencial; se True e cfg.prefetch > 0, aciona leitura antecipada em miss.
        Retorna:
            Objeto Page (Frame) residente na memória com pin_count >= 1.
        Levanta:
            ValueError: se (fid, bno) já estiver residente em outra área != ptype, ou se bno for inválido.
            KeyError: se fid for inválido ou fechado.
            BufferFullError: se for miss e todos os frames da área ptype estiverem fixados/reservados.
        Custo de I/O:
            - Hit: 0 leituras e 0 gravações.
            - Miss: 1 leitura física (mais até cfg.prefetch leituras se seq=True) + 1 gravação se a vítima estiver suja.
        """
        area = self._areas[ptype]

        f = self._table.get((fid, bno))
        if f is not None:
            # HIT: a página já está na memória. Incrementa pin_count ("alfinete") para que
            # ninguém possa expulsá-la enquanto estiver em uso, e avisa a política (accessed).
            if f.ptype != ptype:
                raise ValueError(
                    f"Bloco ({fid}, {bno}) residente na área {f.ptype}, mas pedido como {ptype}"
                )
            area.stats.requests += 1
            self.stats.requests += 1
            area.stats.hits += 1
            self.stats.hits += 1
            f.pin_count += 1
            # Se a página estava na fila de descarte imediato (tossed), resgata-a pois voltou a ser usada.
            if area.tossed:
                try:
                    area.tossed.remove(f)
                except ValueError:
                    pass
            area.policy.accessed(f)
            return f

        # MISS: a página não está no buffer.
        # Valida se (fid, bno) existe antes de expulsar qualquer vítima da memória.
        if not self.fm.is_valid_block(fid, bno):
            self.fm.read(fid, bno)

        # D-P1-10: escolhe vítima e lê do disco antes de incrementar misses (garante 1 miss == 1 fm.read).
        f = self._victim(area)
        try:
            raw = self.fm.read(fid, bno)
        except Exception:
            area.free.append(f)
            raise
        area.stats.requests += 1
        self.stats.requests += 1
        area.stats.misses += 1
        self.stats.misses += 1
        # Copia para dentro do bytearray pré-alocado do frame sem trocar a referência do objeto f.data
        f.data[:] = raw
        self._install(f, area, fid, bno, ptype, pin_count=1, dirty=False)

        # Se o operador informou leitura sequencial (seq=True) e há prefetch configurado, carrega os próximos k blocos
        if seq and self.cfg.prefetch > 0:
            self._prefetch(area, fid, bno + 1, self.cfg.prefetch)
        return f

    def _prefetch(self, area: _Area, fid: int, start: int, k: int) -> None:
        # D-P1-12: leitura antecipada dos blocos [start .. start + k - 1].
        # Cada bloco pré-carregado entra com pin_count = 0 (para não vazar pin se o scan parar antes)
        # e não conta como request/miss até que o operador realmente chame pin() nele.
        # Guardamos em batch_frames os frames instalados nesta chamada: se a próxima vítima escolhida
        # pela política (ex.: MRU ou área pequena no LRU) for um frame de batch_frames, _victim_or_none
        # retorna None e paramos imediatamente (break) para o prefetch nunca expulsar a si mesmo!
        batch_frames: set[Frame] = set()
        for b in range(start, start + k):
            if (fid, b) in self._table:
                break
            if not self.fm.is_valid_block(fid, b):
                break
            f = self._victim_or_none(area, exclude=batch_frames)
            if f is None:
                break
            try:
                raw = self.fm.read(fid, b)
            except Exception:
                area.free.append(f)
                break
            f.data[:] = raw
            self._install(f, area, fid, b, area.ptype, pin_count=0, dirty=False)
            batch_frames.add(f)
            area.stats.prefetched += 1
            self.stats.prefetched += 1

    def new_page(self, fid: int, ptype: PageType = PageType.DATA) -> Page:
        """Aloca um novo bloco em fid e o instala zerado na área ptype com pin_count=1 e dirty=True.

        Parâmetros:
            fid: identificador inteiro do arquivo aberto no FileManager.
            ptype: área do buffer onde a nova página residirá (CATALOG, INDEX, DATA ou WORK).
        Retorna:
            Objeto Page (Frame) recém-alocado, com pin_count=1 e dirty=True.
        Levanta:
            KeyError: se fid for inválido ou fechado.
            BufferFullError: se todos os frames da área ptype estiverem fixados/reservados.
        Custo de I/O: 0 leituras; 0 gravações imediatas (ou 1 gravação se a vítima expulsa estiver suja).
        """
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
        """Decrementa pin_count da página, preserva/ativa dirty e enfileira para descarte imediato se toss=True.

        Parâmetros:
            page: página residente previamente fixada por pin() ou new_page().
            dirty: se True, marca a página como modificada (OU lógico com page.dirty atual — D-P1-10).
            toss: se True, cfg.toss_immediate=True e pin_count zerar, coloca na fila tossed (D-P1-09).
        Levanta:
            RuntimeError: se page.pin_count <= 0 ou se a página não estiver residente no buffer.
        Custo de I/O: 0 blocos (escrita diferida até expulsão, flush ou force).
        """
        if page.pin_count <= 0:
            raise RuntimeError("unpin sem pin correspondente")
        if page.fid < 0 or self._table.get((page.fid, page.bno)) is not page:
            raise RuntimeError("unpin em página não residente")
        # D-P1-10: OU lógico — se a página já estava dirty (via mark_dirty() ou new_page()),
        # um unpin(dirty=False) de outro leitor NUNCA pode apagar o dirty=True anterior!
        if dirty:
            page.dirty = True
        page.pin_count -= 1
        area = self._areas[page.ptype]
        area.policy.unpinned(page)
        # D-P1-09: se ninguém mais segura a página (pin_count == 0) e o operador avisou toss=True,
        # coloca na fila area.tossed para ser reciclada imediatamente na próxima falta de página.
        if toss and self.cfg.toss_immediate and page.pin_count == 0:
            if page not in area.tossed:
                area.tossed.append(page)

    @contextmanager
    def page(
        self,
        fid: int,
        bno: int,
        ptype: PageType = PageType.DATA,
        seq: bool = False,
        toss: bool = False,
    ) -> Iterator[Page]:
        """Gerenciador de contexto que fixa (fid, bno) na entrada e garante unpin na saída.

        Parâmetros:
            fid: identificador inteiro do arquivo aberto no FileManager.
            bno: número do bloco lógico (0-indexado, em blocos).
            ptype: área do buffer (CATALOG, INDEX, DATA ou WORK).
            seq: dica de leitura sequencial repassada a pin().
            toss: dica de descarte imediato repassada a unpin().
        Produz (yields):
            Objeto Page residente e fixado.
        Levanta:
            Mesmas exceções de pin() e unpin().
        Custo de I/O: mesmo custo de pin() (0 blocos em hit; 1 leitura em miss + 1 escrita se vítima suja).
        """
        p = self.pin(fid, bno, ptype, seq=seq)
        try:
            yield p
        finally:
            self.unpin(p, dirty=p.dirty, toss=toss)

    def force(self, page: Page) -> None:
        """Grava imediatamente a página residente no disco sem desafixá-la (D-P1-11: forced output).

        Parâmetros:
            page: página residente no buffer a ser sincronizada com o disco.
        Levanta:
            ValueError: se a página não estiver residente no buffer.
            KeyError: se o arquivo fid estiver fechado.
        Custo de I/O: 1 gravação de bloco (incrementa forced_writes e fm.io.writes) e limpa page.dirty.
        """
        if page.fid < 0 or self._table.get((page.fid, page.bno)) is not page:
            raise ValueError("force em página não residente")
        self.fm.write(page.fid, page.bno, page.data)
        page.dirty = False
        area = self._areas[page.ptype]
        area.stats.forced_writes += 1
        self.stats.forced_writes += 1

    def flush_file(self, fid: int) -> None:
        """Grava em ordem crescente de bno todas as páginas sujas residentes do arquivo fid.

        Parâmetros:
            fid: identificador inteiro do arquivo. Se já estiver fechado, não faz nada.
        Custo de I/O: 1 gravação por página suja residente de fid (0 leituras).
        """
        if not self.fm.is_open(fid):
            return
        # Ordena por bno para que a descarga de páginas sujas aproveite escritas sequenciais
        dirty_frames = [
            f for f in self._table.values() if f.fid == fid and f.dirty
        ]
        dirty_frames.sort(key=lambda f: f.bno)
        for f in dirty_frames:
            self.fm.write(f.fid, f.bno, f.data)
            f.dirty = False

    def flush_all(self) -> None:
        """Grava todas as páginas sujas de todos os arquivos abertos residentes no Buffer Pool.

        Ordena por (fid, bno) para minimizar seeks. Páginas de arquivos já fechados são limpas/descartadas.
        Custo de I/O: 1 gravação por página suja residente de arquivo aberto (0 leituras).
        """
        dirty_frames = [f for f in list(self._table.values()) if f.dirty]
        dirty_frames.sort(key=lambda f: (f.fid, f.bno))
        for f in dirty_frames:
            if self.fm.is_open(f.fid):
                self.fm.write(f.fid, f.bno, f.data)
                f.dirty = False
            elif f.pin_count == 0:
                self._discard_frame(f)
            else:
                f.dirty = False

    def _discard_frame(self, f: Frame) -> None:
        """Descarta um frame sem gravar no disco e o devolve para area.free."""
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
        if f not in area.reserved and f not in area.free:
            area.free.append(f)

    def drop_file(self, fid: int) -> None:
        """Descarta sem gravar todas as páginas residentes de fid (usado ao descartar TempFile).

        Parâmetros:
            fid: identificador inteiro do arquivo cujas páginas serão invalidadas.
        Levanta:
            RuntimeError: se houver qualquer página de fid ainda fixada (pin_count > 0).
        Custo de I/O: 0 blocos (descarte puramente em memória).
        """
        frames = [f for f in self._table.values() if f.fid == fid]
        for f in frames:
            if f.pin_count != 0:
                raise RuntimeError(
                    f"drop_file({fid}) com página ({f.fid}, {f.bno}) fixada (pin_count={f.pin_count})"
                )
        for f in frames:
            self._discard_frame(f)

    def close_file(self, fid: int) -> None:
        """Descarrega páginas sujas de fid, descarta seus frames do buffer e fecha fid no FileManager.

        Parâmetros:
            fid: identificador inteiro do arquivo aberto.
        Levanta:
            RuntimeError: se houver página de fid fixada (pin_count > 0).
            KeyError: se fid não estiver aberto no FileManager.
        Custo de I/O: 1 gravação por página suja residente de fid (cabeçalho não conta em IOCounters).
        """
        if self.fm.is_open(fid):
            self.flush_file(fid)
        self.drop_file(fid)
        self.fm.close(fid)

    def free_block(self, fid: int, bno: int) -> None:
        """Descarta eventual cópia residente no buffer sem gravar e devolve bno à free list do FileManager (D-P1-04).

        Parâmetros:
            fid: identificador inteiro do arquivo aberto.
            bno: número do bloco lógico (0-indexado, em blocos) a liberar.
        Levanta:
            RuntimeError: se a página (fid, bno) estiver fixada no buffer (pin_count > 0).
            KeyError: se fid for inválido ou fechado.
            ValueError: se bno estiver fora da faixa ou já estiver livre no FileManager.
        Custo de I/O: 0 blocos (descarta sem escrita).
        """
        # Se não descartássemos do buffer antes de fm.free(), um futuro allocate() que reutilizasse
        # o mesmo bno encontraria dados velhos cacheados em _table[(fid, bno)].
        f = self._table.get((fid, bno))
        if f is not None:
            if f.pin_count != 0:
                raise RuntimeError(
                    f"free_block({fid}, {bno}) com página fixada (pin_count={f.pin_count})"
                )
            self._discard_frame(f)
        self.fm.free(fid, bno)

    def clear(self) -> None:
        """Grava todas as páginas sujas e esvazia todas as áreas do Buffer Pool (usado por Engine.cold()).

        Frames atualmente emprestados a reserve_work() permanecem reservados para evitar duplicação em area.free.
        Levanta:
            RuntimeError: se houver qualquer página fixada (pinned() != 0).
        Custo de I/O: 1 gravação por página suja residente (via flush_all); 0 leituras.
        """
        if self.pinned() != 0:
            raise RuntimeError(
                f"clear() chamado com {self.pinned()} página(s) ainda fixada(s)"
            )
        self.flush_all()
        self._table.clear()
        for area in self._areas.values():
            area.tossed.clear()
            area.policy.reset()
            for f in area.frames:
                if f in area.reserved:
                    continue
                f.fid = -1
                f.bno = -1
                f.pin_count = 0
                f.dirty = False
                f.ref = 0
            # Preserva frames atualmente emprestados a reserve_work() para não duplicá-los no finally
            area.free = [f for f in reversed(area.frames) if f not in area.reserved]

    def capacity(self, ptype: PageType | None = None) -> int:
        """Retorna a capacidade total configurada em páginas (frames), global ou da área ptype.

        Parâmetros:
            ptype: área opcional (CATALOG, INDEX, DATA ou WORK); se None, soma todas as áreas.
        Retorna:
            Número de frames (páginas) da área ou do Buffer Pool inteiro.
        Custo de I/O: 0 blocos.
        """
        if ptype is None:
            return sum(len(a.frames) for a in self._areas.values())
        return len(self._areas[ptype].frames)

    def pinned(self, ptype: PageType | None = None) -> int:
        """Retorna a quantidade de páginas atualmente fixadas (pin_count > 0), global ou na área ptype.

        Parâmetros:
            ptype: área opcional (CATALOG, INDEX, DATA ou WORK); se None, soma todas as áreas.
        Retorna:
            Número de páginas com pin_count > 0.
        Custo de I/O: 0 blocos.
        """
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
        """Reserva fisicamente n frames da área WORK para estruturas em memória dos operadores (D-P1-10).

        Parâmetros:
            n: quantidade de páginas (frames) de trabalho a reservar na área WORK.
        Produz (yields):
            O inteiro n reservado, devolvendo os frames para WORK.free ao sair do bloco with.
        Levanta:
            ValueError: se n < 0.
            BufferFullError: se n exceder o total de frames disponíveis (não fixados e não reservados) em WORK.
        Custo de I/O: 0 leituras; até n gravações se precisar expulsar páginas sujas residentes em WORK.
        """
        # Retira fisicamente n frames de WORK enquanto durar o bloco with, garantindo que
        # algoritmos de junção/ordenação não ultrapassem o limite de M páginas de trabalho.
        if n < 0:
            raise ValueError(f"Reserva inválida: n={n}")
        area = self._areas[PageType.WORK]
        available = sum(
            1 for f in area.frames if f.pin_count == 0 and f not in area.reserved
        )
        if n > available:
            raise BufferFullError(
                f"área WORK: pedido reserve_work({n}), mas apenas {available} frames disponíveis"
            )
        taken: list[Frame] = []
        try:
            for _ in range(n):
                f = self._victim(area)
                area.reserved.add(f)
                taken.append(f)
            yield n
        finally:
            for f in reversed(taken):
                area.reserved.discard(f)
                if f not in area.free:
                    area.free.append(f)

    def reset_stats(self) -> None:
        """Zera os contadores estatísticos globais e por área do Buffer Pool (BufferCounters).

        Custo de I/O: 0 blocos.
        """
        self.stats = BufferCounters()
        for ptype, area in self._areas.items():
            area.stats = BufferCounters()
            self.stats_by_area[ptype] = area.stats
