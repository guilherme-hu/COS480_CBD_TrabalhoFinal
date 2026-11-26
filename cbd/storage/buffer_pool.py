"""Gerente de Buffer Pool (Camada 4, dono: P1 — Vivian).

Implementa: BufferPool, Page/Frame (cbd/contracts.py). Usa: FileManager, ReplacementPolicy.
Decisões: D-P1-06 (1 página = 1 bloco, 264 páginas), D-P1-07 (4 áreas fixas isoladas),
D-P1-08 (políticas LRU/MRU), D-P1-09 (toss-immediate), D-P1-10 (pin_count, dirty e reserve_work),
D-P1-11 (gravação forçada force), D-P1-12 (prefetch; revista em 23/11: só dentro de um cursor
bp.seq_scan, com as páginas pré-lidas FIXADAS pelo cursor dono até ele usá-las ou terminar, e só nas
áreas DATA e INDEX). Ver docs/decisoes/P1.md.
Invariantes: nenhum frame com pin_count > 0 é expulso; bp.pinned() == 0 ao fim de cada operação (os pins
de pré-leitura de um cursor saem quando ele termina); io.reads == misses + prefetched; páginas
alteradas marcadas dirty antes de unpin.
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
        # D-P1-12 (revista em 23/11): cursores seq_scan abertos nesta área (teto do prefetch).
        self.cursors: int = 0


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
        # D-P1-12 (B1): prefetch desligado por padrão; só roda em fid explicitamente marcado como monotipado.
        self._monotyped: dict[int, PageType] = {}
        self._seen_ptypes: dict[int, PageType] = {}
        self._mixed_fids: set[int] = set()
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
        # D-P1-04 / revisão PR #2: registro formal de hooks na Camada 4 para chamadas diretas a fm.close/free/delete
        fm.register_hooks(
            on_before_close=self._on_fm_before_close,
            on_before_free=self._on_fm_before_free,
            on_before_delete=self.drop_file,
        )

    def _on_fm_before_close(self, fid: int) -> None:
        if self.fm.is_open(fid):
            self.flush_file(fid)
        self.drop_file(fid)

    def _on_fm_before_free(self, fid: int, bno: int) -> None:
        f = self._table.get((fid, bno))
        if f is not None:
            if f.pin_count != 0:
                raise RuntimeError(
                    f"free({fid}, {bno}) com página fixada no buffer (pin_count={f.pin_count})"
                )
            self._discard_frame(f)

    def _record_ptype(self, fid: int, ptype: PageType) -> None:
        if fid in self._mixed_fids:
            return
        seen = self._seen_ptypes.get(fid)
        if seen is None:
            self._seen_ptypes[fid] = ptype
        elif seen != ptype:
            self._monotyped.pop(fid, None)
            self._mixed_fids.add(fid)
        if fid in self._monotyped and self._monotyped[fid] != ptype:
            self._monotyped.pop(fid, None)
            self._mixed_fids.add(fid)

    def allow_prefetch(self, fid: int, ptype: PageType = PageType.DATA) -> None:
        """Declara que o arquivo `fid` é monotipado na área `ptype`, habilitando prefetch (D-P1-12).

        O prefetch só acontece dentro de um cursor `bp.seq_scan(fid, ptype)` e só nas áreas DATA e
        INDEX: em WORK e CATALOG levanta ValueError (D-P1-12, revista em 23/11), porque as páginas
        pré-lidas ficam fixadas e, em WORK, tirariam frames do M dos operadores.

        Contrato estrito do chamador: o FileManager armazena apenas blocos crus sem PageType no disco;
        portanto, o BufferPool não consegue inferir o tipo de um bloco futuro antes da leitura.
        Arquivos heterogêneos (ex.: Árvore B+ de P3 com nós INDEX e folhas DATA) NÃO devem chamar
        allow_prefetch; se um arquivo declarado monotipado vier a acessar outra área, o prefetch é
        revogado (podendo levantar ValueError caso um bloco já pré-lido seja pedido em outra área).
        Custo de I/O: 0 blocos.
        """
        # D-P1-12 (revista em 23/11): na área WORK, cada cursor de run fixaria 1 + k páginas e a fusão
        # de M − 1 runs precisaria de (M − 1)·(k + 1) frames, quebrando a conta de memória (M) de
        # P4/P5, cujas fórmulas contam 1 página por entrada. CATALOG não tem varreduras longas.
        if ptype not in (PageType.DATA, PageType.INDEX):
            raise ValueError(f"prefetch só nas áreas DATA e INDEX (D-P1-12), pedido em {ptype}")
        if fid in self._mixed_fids:
            raise ValueError(
                f"Arquivo fid={fid} já misturou tipos de página e não pode usar prefetch monotipado"
            )
        seen = self._seen_ptypes.get(fid)
        if seen is not None and seen != ptype:
            self._monotyped.pop(fid, None)
            self._mixed_fids.add(fid)
            raise ValueError(
                f"Arquivo fid={fid} já acessado como {seen}, incompatível com {ptype}"
            )
        prev = self._monotyped.get(fid)
        if prev is not None and prev != ptype:
            self._monotyped.pop(fid, None)
            self._mixed_fids.add(fid)
            raise ValueError(
                f"Arquivo fid={fid} já declarado como {prev}, incompatível com {ptype}"
            )
        self._monotyped[fid] = ptype

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

    def _victim_or_none(self, area: _Area) -> Frame | None:
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
                self._evict_frame(area, cand)
                return cand

        # 3ª prioridade (D-P1-08): consulta a política da área (LRU ou MRU), que só devolve frames sem
        # pin. Páginas pré-lidas ainda não usadas estão fixadas pelo cursor dono (D-P1-12) e nunca saem.
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
        self,
        fid: int,
        bno: int,
        ptype: PageType = PageType.DATA,
        seq: bool = False,
    ) -> Page:
        """Fixa (pin_count += 1) e retorna a página correspondente ao bloco (fid, bno) na área ptype.

        Parâmetros:
            fid: identificador inteiro do arquivo aberto no FileManager.
            bno: número do bloco lógico (1-indexado, bno >= 1; Bloco 0 é o cabeçalho do arquivo).
            ptype: área do buffer onde a página deve residir (CATALOG, INDEX, DATA ou WORK).
            seq: mantido por compatibilidade com o contrato e SEM efeito: a leitura antecipada só acontece
                dentro de um cursor bp.seq_scan (D-P1-12, revista em 23/11).
        Retorna:
            Objeto Page (Frame) residente na memória com pin_count >= 1.
        Levanta:
            ValueError: se (fid, bno) já estiver residente em outra área != ptype, ou se bno for inválido.
            KeyError: se fid for inválido ou fechado.
            BufferFullError: se for miss e todos os frames da área ptype estiverem fixados/reservados.
        Custo de I/O:
            - Hit: 0 leituras e 0 gravações.
            - Miss: 1 leitura física + 1 gravação se a vítima estiver suja.
        """
        return self._pin(fid, bno, ptype, cursor=None)

    def _count_hit(self, area: _Area, f: Frame) -> None:
        """Contabiliza um hit em f (request + hit), resgata f da fila tossed e avisa a política."""
        area.stats.requests += 1
        self.stats.requests += 1
        area.stats.hits += 1
        self.stats.hits += 1
        # Se a página estava na fila de descarte imediato (tossed), resgata-a pois voltou a ser usada.
        if area.tossed:
            try:
                area.tossed.remove(f)
            except ValueError:
                pass
        area.policy.accessed(f)

    def _unpinned(self, area: _Area) -> int:
        """Frames da área que um pin poderia usar agora: livres + residentes sem pin (0 I/Os)."""
        return len(area.free) + sum(1 for f in area.frames if f.fid >= 0 and f.pin_count == 0)

    def _pin(self, fid: int, bno: int, ptype: PageType, cursor: "SeqScan | None") -> Page:
        area = self._areas[ptype]

        f = self._table.get((fid, bno))
        if f is not None:
            # HIT: a página já está na memória. Incrementa pin_count ("alfinete") para que
            # ninguém possa expulsá-la enquanto estiver em uso, e avisa a política (accessed).
            if f.ptype != ptype:
                # Se alguém misturou áreas no mesmo fid, revoga qualquer autorização de prefetch.
                self._monotyped.pop(fid, None)
                self._mixed_fids.add(fid)
                raise ValueError(
                    f"Bloco ({fid}, {bno}) residente na área {f.ptype}, mas pedido como {ptype}"
                )
            # Se a página é pré-leitura de OUTRO cursor, este pin soma ao dele (pin_count = 2): ela só pode
            # sair quando os dois soltarem (SKS §13.5.1.2).
            f.pin_count += 1
            self._count_hit(area, f)
            self._record_ptype(fid, ptype)
            return f

        # MISS: a página não está no buffer.
        # Valida se (fid, bno) existe antes de expulsar qualquer vítima da memória.
        if not self.fm.is_valid_block(fid, bno):
            self.fm.read(fid, bno)

        # D-P1-10: escolhe vítima e lê do disco antes de incrementar misses (garante 1 miss == 1 fm.read).
        f = self._victim(area)

        # D-P1-12 (B1): registra área vista para fid e revoga prefetch se houver mistura de tipos.
        self._record_ptype(fid, ptype)

        # D-P1-12 (B1): só dispara prefetch se o arquivo fid foi explicitamente declarado monotipado
        # para esta área ptype (evita instalar blocos de INDEX em DATA ou vice-versa).
        # D-P1-12 (revista em 23/11): e só se o pin vier de um cursor seq_scan, que é o dono das
        # páginas pré-lidas e as solta ao usá-las ou ao terminar.
        # D-P1-12 (revista em 22/11, B2): as vítimas do lote são escolhidas e expulsas AGORA, antes de
        # qualquer leitura. Assim as gravações das vítimas sujas acontecem todas antes, e o bloco
        # pedido + o lote são lidos em sequência: 1 seek + k leituras sequenciais (arquitetura §3.2).
        # Antes, cada vítima suja era gravada entre duas leituras e cada leitura virava aleatória
        # (ai/P1/review.md, achado 18).
        batch: list[tuple[int, Frame]] = []
        if (
            cursor is not None
            and self.cfg.prefetch > 0
            and fid not in self._mixed_fids
            and self._monotyped.get(fid) == ptype
        ):
            try:
                batch = self._prefetch_victims(area, fid, bno + 1, self.cfg.prefetch)
            except Exception:
                # Falha ao gravar uma vítima suja do lote (ai/P1/review.md, achado 26): devolve o
                # frame do bloco pedido; os do lote já voltaram dentro de _prefetch_victims.
                area.free.append(f)
                raise

        try:
            raw = self.fm.read(fid, bno)
        except Exception:
            area.free.append(f)
            area.free.extend(fr for _, fr in batch)
            raise
        area.stats.requests += 1
        self.stats.requests += 1
        area.stats.misses += 1
        self.stats.misses += 1
        # Copia para dentro do bytearray pré-alocado do frame sem trocar a referência do objeto f.data
        f.data[:] = raw
        self._install(f, area, fid, bno, ptype, pin_count=1, dirty=False)

        if batch:
            try:
                self._prefetch_read(area, fid, batch, cursor._held)
            except Exception:
                # Erro real de E/S no lote (achado 26): as pré-lidas já instaladas ficam com o cursor,
                # que as solta ao terminar; o bloco pedido não chega ao chamador, então o pin dele sai.
                self.unpin(f)
                raise
        return f

    def _prefetch_victims(self, area: _Area, fid: int, start: int, k: int) -> list[tuple[int, Frame]]:
        """1ª fase do prefetch: escolhe e expulsa as vítimas dos blocos [start .. start + k - 1].

        Devolve [(bno, frame vazio)] na ordem dos blocos. Para no primeiro bloco já residente, fora do
        arquivo, sem vítima ou no teto de frames. Custo de I/O: 0 leituras; 1 gravação por vítima suja.
        """
        # As regras de parada são avaliadas antes de expulsar: nenhuma vítima sai à toa. Os frames
        # expulsos aqui não estão na política nem em `free`, por isso não podem ser escolhidos de novo
        # dentro do mesmo lote. Páginas pré-lidas de outros cursores estão fixadas e nunca são vítimas.
        batch: list[tuple[int, Frame]] = []
        try:
            for b in range(start, start + k):
                if (fid, b) in self._table:
                    break
                if not self.fm.is_valid_block(fid, b):
                    break
                # D-P1-12 (revista em 23/11): teto. Como as pré-lidas ficam fixadas, o prefetch para
                # antes de deixar a área com menos frames sem pin do que cursores abertos nela: cada
                # cursor aberto tem um frame para a página atual (SKS §13.5.1.2: "avoid pinning too
                # many blocks").
                # LIMITAÇÃO (D-P1-12, T0): isso não reserva frames para pins comuns. A garantia é:
                # pré-leitura não causa BufferFullError enquanto
                #     pins simultâneos fora dos cursores <= capacidade − cursores × (1 + k).
                # Com DATA = 128, k = 8 e 1 cursor: até 119 pins. Em área pequena (ex.: DATA = 3, k = 8)
                # dois pins comuns dentro de um cursor podem falhar. Melhoria possível: o cursor ceder
                # a pré-lida mais distante quando um pin não acha vítima (opção T2, preterida).
                if self._unpinned(area) - 1 < area.cursors:
                    break
                fr = self._victim_or_none(area)
                if fr is None:
                    break
                batch.append((b, fr))
        except Exception:
            # Falha ao gravar uma vítima suja (achado 26): os frames já escolhidos voltam para `free`.
            # A vítima que falhou continua residente e suja (_evict_frame grava antes de remover).
            area.free.extend(fr for _, fr in batch)
            raise
        return batch

    def _prefetch_read(
        self, area: _Area, fid: int, batch: list[tuple[int, Frame]], held: dict[int, Frame]
    ) -> None:
        """2ª fase do prefetch: lê os blocos do lote em sequência e os instala FIXADOS (pin_count = 1).

        O pin pertence ao cursor que pediu o prefetch (D-P1-12, revista em 23/11): cada página
        instalada entra em `held` (o `_held` do cursor) na hora, para que um erro no meio do lote
        não deixe pin sem dono. Custo de I/O: len(batch) leituras sequenciais.
        Levanta: qualquer erro de fm.read, inclusive o ValueError de bloco além do fim físico
        (achados 26 e 30).
        """
        for i, (b, fr) in enumerate(batch):
            try:
                raw = self.fm.read(fid, b)
            except Exception:
                # Achado 30: nenhum erro aqui é fim normal do lote. A 1ª fase já conferiu
                # is_valid_block, então até o ValueError de bloco além do fim físico é bug de quem
                # chamou. Devolve este frame e os que sobraram para `free` e propaga.
                area.free.extend(f for _, f in batch[i:])
                raise
            fr.data[:] = raw
            self._install(fr, area, fid, b, area.ptype, pin_count=1, dirty=False)
            held[b] = fr
            area.stats.prefetched += 1
            self.stats.prefetched += 1

    @contextmanager
    def seq_scan(self, fid: int, ptype: PageType = PageType.DATA) -> Iterator["SeqScan"]:
        """Abre um cursor de varredura sequencial sobre fid na área ptype (D-P1-12, revista em 23/11).

        Dentro do cursor, `cur.page(bno)` funciona como `bp.page`, mas um miss dispara a leitura
        antecipada de até cfg.prefetch blocos seguintes (se fid foi declarado com allow_prefetch e
        ptype é DATA ou INDEX). As páginas pré-lidas ficam FIXADAS em nome do cursor: quando ele chega
        numa delas, assume esse pin (não soma outro) e o solta no fim do `with cur.page(...)`. Ao sair
        do `with bp.seq_scan(...)` (fim, break ou exceção), o cursor solta as pré-lidas que não usou.
        Se outro cursor também fixou a página, ela só pode sair quando os dois soltarem (SKS §13.5.1.2).

        Uso típico na camada 3:
            with bp.seq_scan(fid, PageType.DATA) as cur:
                for bno in range(1, n + 1):
                    with cur.page(bno, toss=True) as p:
                        ...
        Custo de I/O: o mesmo dos pins que o cursor fizer, mais as leituras antecipadas (sequenciais).
        Memória: no máximo cfg.prefetch pré-lidas por cursor, mais as páginas em uso por ele: 1 + k
        num scan simples, d + k com cur.page aninhado em profundidade d (um miss fora das pré-lidas
        solta as restantes, D-P1-12 opção S2); o teto da área pode encurtar o lote.
        Garantia (T0): pré-leitura não causa BufferFullError enquanto os pins simultâneos fora dos
        cursores forem <= capacidade − cursores × (1 + cfg.prefetch).
        LIMITAÇÃO: com MRU, o lote tira k + 1 vítimas das páginas mais recentes e pode fragmentar o
        conjunto retido num laço interno, subindo seeks (ai/P1/review.md, achado 28). A matriz usa LRU.
        """
        area = self._areas[ptype]
        cur = SeqScan(self, fid, ptype)
        area.cursors += 1
        try:
            yield cur
        finally:
            area.cursors -= 1
            cur._closed = True  # achado 25: cur.page depois do with levanta RuntimeError
            cur._release()

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
        self._record_ptype(fid, ptype)
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
            bno: número do bloco lógico (1-indexado, bno >= 1; Bloco 0 é o cabeçalho do arquivo).
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
        self._monotyped.pop(fid, None)
        self._seen_ptypes.pop(fid, None)
        self._mixed_fids.discard(fid)

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
            bno: número do bloco lógico (1-indexado, bno >= 1; Bloco 0 é o cabeçalho do arquivo) a liberar.
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


class SeqScan:
    """Cursor de varredura sequencial, dono das páginas que pré-leu (D-P1-12, revista em 23/11).

    Criado só por `BufferPool.seq_scan`. Guarda em `_held` as páginas pré-lidas que ainda não usou,
    cada uma com 1 pin em nome do cursor. Custo de I/O: o dos pins que fizer.
    """

    def __init__(self, bp: BufferPool, fid: int, ptype: PageType) -> None:
        self._bp = bp
        self.fid = fid
        self.ptype = ptype
        self._held: dict[int, Frame] = {}
        self._closed = False

    @contextmanager
    def page(self, bno: int, toss: bool = False) -> Iterator[Page]:
        """Fixa o bloco bno do arquivo do cursor e o solta na saída (como `bp.page`).

        Se bno é uma página que este cursor pré-leu, assume o pin de pré-leitura (conta 1 request e
        1 hit, 0 leituras) em vez de somar outro. Senão, faz um pin normal, e um miss dispara o
        prefetch dos blocos seguintes. Um miss fora das pré-lidas solta antes as que sobraram
        (opção S2): o cursor segura no máximo cfg.prefetch pré-lidas, mais as páginas em uso por ele
        (1 + k num scan simples; d + k com cur.page aninhado em profundidade d).
        Custo de I/O: hit = 0; miss = 1 leitura + até cfg.prefetch leituras sequenciais
        (+ 1 gravação por vítima suja).
        Levanta: RuntimeError se usado depois do fim do `with bp.seq_scan(...)`; as exceções de pin.
        """
        if self._closed:
            raise RuntimeError("cursor seq_scan usado depois do fim do with (D-P1-12)")
        bp = self._bp
        fr = self._held.pop(bno, None)
        if fr is None and self._held and (self.fid, bno) not in bp._table:
            # D-P1-12 (S2, ai/P1/review.md achados 23 e 29): um MISS fora das pré-lidas = o cursor saiu
            # da sequência que pré-leu e vai pré-ler de novo. Solta as restantes antes do pin, para
            # segurar no máximo k pré-lidas. Num hit (ex.: pedir de novo a página atual) não solta:
            # um hit não pré-lê, então o limite continua valendo.
            self._release()
        if fr is not None:
            # D-P1-12 (revista em 23/11): a pré-leitura é deste cursor; o pin dela passa a ser o pin de uso.
            bp._count_hit(bp._areas[self.ptype], fr)
            p = fr
        else:
            p = bp._pin(self.fid, bno, self.ptype, cursor=self)
        try:
            yield p
        finally:
            bp.unpin(p, dirty=p.dirty, toss=toss)

    def _release(self) -> None:
        # D-P1-12 (revista em 23/11): solta o pin de cada pré-leitura que o cursor não usou. Chamado no
        # fim do cursor e num pedido fora das pré-lidas (S2).
        held, self._held = self._held, {}
        for fr in held.values():
            self._bp.unpin(fr)
