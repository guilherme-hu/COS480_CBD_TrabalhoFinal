"""Heap (organização primária sem ordem) e arquivo temporário. Camada 3, dono: P2.

Implementa: DataFile (HeapFile) e TempFile (TempHeap) de cbd/contracts.py.
Usa: BufferPool (P1), SlottedPage e RecordCodec (P2).
Decisões: D-P2-06 (RecordTooLarge no insert, antes de tocar em página), D-P2-07 e D-P2-08 (slotted page,
compactação preguiçosa), D-P2-09 (mapa de espaço livre com 1 byte por bloco), D-P2-10 (RID de 6 B).
Ver docs/decisoes/P2.md. A carga em massa enche os blocos na ordem de chegada (guia P2 §5.3).

Layout do arquivo do heap:
    bno 1      meta: 'HEP1' | n_records u32 | n_deleted u32 | tail u32 | n_faixas u16 | n_mapa u16
                     | faixas de blocos de dados (u32 início, u32 fim)* | blocos do mapa u32*
    dados      slotted pages; o scan percorre só as faixas de dados
    mapa       1 byte por bloco: a página k do mapa guarda os blocos k·P .. k·P + P − 1 (P = bytes do
               bloco); o byte vale livre·256 // B, arredondado para baixo (D-P2-09)

Invariantes: no máximo 1 página fixada por iterador; a entrada do mapa da cauda não é mantida (a cauda é
tentada primeiro); o mapa é uma dica: uma entrada errada é corrigida quando o bloco é pinado.
Custo esperado: scan = b leituras de dados (pula meta e mapa); insert ≈ 1 bloco (a cauda costuma estar no
buffer); fetch = 1 bloco; delete = 1 bloco + a página do mapa (em geral no buffer).
LIMITAÇÃO: os contadores (n_records, n_deleted) só vão para a meta em mudanças de estrutura e no close();
sem close, o arquivo no disco fica com os contadores velhos. Não há recuperação de falhas no projeto.
"""
import struct

from cbd.contracts import NotApplicable, PageType, RID, RecordTooLarge
from cbd.records.codec import RecordCodec
from cbd.records.slotted_page import SlottedPage

_MAGIC = b"HEP1"
_META = struct.Struct("<4sIIIHH")
_RANGE = struct.Struct("<II")
_U32 = struct.Struct("<I")
_PTR = struct.Struct("<IH")          # D-P2-10: bloco u32 + slot u16


def _too_large(n, limit):
    return RecordTooLarge(f"registro de {n} B não cabe num bloco vazio (máximo {limit} B; D-P2-06)")


class HeapFile:
    """Arquivo heap: registros em qualquer bloco com espaço, achado pelo mapa depois de remoções."""

    _FSM_PER_PAGE = None             # entradas por página do mapa; None = tamanho do bloco (1 byte por bloco)

    def __init__(self, bp, name, schema, spec, ptype, fid):
        self.bp, self.name, self.schema, self.spec, self.ptype, self.fid = bp, name, schema, spec, ptype, fid
        self.codec = RecordCodec(schema)
        self.ptr_size = _PTR.size
        self._b = bp.block_size
        self._max = SlottedPage.max_record(self._b)
        self._per = self._FSM_PER_PAGE or self._b
        self._reset()

    def _reset(self):
        self._n = self._deleted = self._tail = 0
        self._ranges = []            # faixas [início, fim] de blocos de dados, em ordem de alocação
        self._fsm = []               # bno de cada página do mapa

    # ------------------------------------------------------------ abrir e fechar
    @classmethod
    def create(cls, bp, name, schema, spec, ptype=PageType.DATA):
        """Cria o arquivo `name` vazio (só a meta, no bloco 1). Custo: nenhuma leitura."""
        h = cls(bp, name, schema, spec, ptype, bp.fm.create(name))
        h._new_meta()
        return h

    @classmethod
    def open(cls, bp, name, schema, spec, ptype=PageType.DATA):
        """Abre um heap existente. Custo: 1 leitura (a meta). ValueError se não for um heap."""
        h = cls(bp, name, schema, spec, ptype, bp.fm.open(name))
        with bp.page(h.fid, 1, ptype) as p:
            magic, h._n, h._deleted, h._tail, nr, nf = _META.unpack_from(p.data, 0)
            if magic != _MAGIC:
                raise ValueError(f"{name}: o bloco 1 não é a meta de um heap")
            pos = _META.size
            for _ in range(nr):
                h._ranges.append(list(_RANGE.unpack_from(p.data, pos)))
                pos += _RANGE.size
            h._fsm = [_U32.unpack_from(p.data, pos + 4 * i)[0] for i in range(nf)]
        return h

    def close(self):
        """Grava a meta e fecha o arquivo (grava as páginas sujas dele)."""
        self._write_meta()
        self.bp.close_file(self.fid)

    def _new_meta(self):
        page = self.bp.new_page(self.fid, self.ptype)
        if page.bno != 1:
            self.bp.unpin(page)
            raise RuntimeError(f"{self.name}: a meta precisa ser o bloco 1, veio {page.bno}")
        self.bp.unpin(page, dirty=True)
        self._write_meta()

    def _write_meta(self):
        body = (_META.pack(_MAGIC, self._n, self._deleted, self._tail, len(self._ranges), len(self._fsm))
                + b"".join(_RANGE.pack(*r) for r in self._ranges)
                + b"".join(_U32.pack(b) for b in self._fsm))
        if len(body) > self._b:
            raise RuntimeError(f"{self.name}: a meta não cabe num bloco ({len(body)} B)")
        page = self.bp.pin(self.fid, 1, self.ptype)
        page.data[:len(body)] = body
        self.bp.unpin(page, dirty=True)

    # ------------------------------------------------------------ mapa de espaço livre (D-P2-09)
    def _fsm_page(self, k):
        """bno da página k do mapa, alocando as que faltam (zeradas: 'sem espaço')."""
        while k >= len(self._fsm):
            page = self.bp.new_page(self.fid, self.ptype)
            self._fsm.append(page.bno)
            self.bp.unpin(page, dirty=True)
            self._write_meta()
        return self._fsm[k]

    def _set_free(self, bno, free):
        """Grava a entrada do bloco: free·256 // B, no máximo 255 (arredonda para baixo)."""
        k, i = divmod(bno, self._per)
        e = min(255, free * 256 // self._b)
        page = self.bp.pin(self.fid, self._fsm_page(k), self.ptype)
        changed = page.data[i] != e
        page.data[i] = e
        self.bp.unpin(page, dirty=changed)

    def _get_free(self, bno):
        """Bytes livres que o mapa garante para o bloco (0 se o bloco não tem página de mapa)."""
        k, i = divmod(bno, self._per)
        if k >= len(self._fsm):
            return 0
        with self.bp.page(self.fid, self._fsm[k], self.ptype) as p:
            return p.data[i] * self._b // 256

    def _candidates(self, n):
        """Blocos de dados cuja entrada garante n bytes, em ordem de bno (fora a cauda)."""
        need = -(-n * 256 // self._b)
        if need > 255:
            return
        for k, fbno in enumerate(self._fsm):
            with self.bp.page(self.fid, fbno, self.ptype) as p:
                entries = bytes(p.data[:self._per])          # cópia: os inserts abaixo mexem no mapa
            for i, e in enumerate(entries):
                bno = k * self._per + i
                if e >= need and bno != self._tail and self._is_data(bno):
                    yield bno

    # ------------------------------------------------------------ blocos de dados
    def _is_data(self, bno):
        return any(a <= bno <= z for a, z in self._ranges)

    def _add_data_block(self, bno):
        if self._ranges and self._ranges[-1][1] == bno - 1:
            self._ranges[-1][1] = bno
        else:
            self._ranges.append([bno, bno])

    def _try_insert(self, bno, data):
        """Tenta pôr data no bloco; devolve (slot ou None, bytes livres depois)."""
        page = self.bp.pin(self.fid, bno, self.ptype)
        sp = SlottedPage(page.data)
        slot = sp.insert(data)
        free = sp.free_space()
        self.bp.unpin(page, dirty=slot is not None)
        return slot, free

    def _new_block(self, data):
        page = self.bp.new_page(self.fid, self.ptype)
        sp = SlottedPage(page.data)
        sp.init()
        slot = sp.insert(data)
        self.bp.unpin(page, dirty=True)
        self._add_data_block(page.bno)
        return page.bno, slot

    def _locate(self, ptr):
        """Valida o RID e devolve (bno, slot). KeyError se não apontar para um slot de dados."""
        bno, slot = ptr
        if not self._is_data(bno):
            raise KeyError(f"{self.name}: {ptr} não é um bloco de dados")
        return bno, slot

    # ------------------------------------------------------------ DataFile
    def insert(self, rec):
        """Insere e devolve o RID. Cauda; se não couber e houver apagados, o mapa; senão bloco novo.

        RecordTooLarge antes de tocar em página (D-P2-06). Custo típico: 1 bloco (a cauda no buffer).
        """
        data = self.codec.encode(rec)
        if len(data) > self._max:
            raise _too_large(len(data), self._max)
        old_tail = self._tail
        if old_tail:
            slot, tail_free = self._try_insert(old_tail, data)
            if slot is not None:
                self._n += 1
                return RID(old_tail, slot)
            if self._deleted:
                for bno in self._candidates(len(data)):
                    slot, free = self._try_insert(bno, data)
                    self._set_free(bno, free)            # acerta a entrada, servindo ou não (é só uma dica)
                    if slot is not None:
                        self._n += 1
                        return RID(bno, slot)
        bno, slot = self._new_block(data)
        if old_tail:
            self._set_free(old_tail, tail_free)          # a cauda antiga passa a ser achada pelo mapa
        self._tail, self._n = bno, self._n + 1
        self._write_meta()
        return RID(bno, slot)

    def delete(self, ptr):
        """Apaga o registro (o slot fica reservado, D-P2-08). KeyError se não existir. Custo: 1 bloco."""
        bno, slot = self._locate(ptr)
        page = self.bp.pin(self.fid, bno, self.ptype)
        sp = SlottedPage(page.data)
        if not 0 <= slot < sp.n_slots() or sp.get(slot) is None:
            self.bp.unpin(page)
            raise KeyError(f"{self.name}: {ptr} não existe")
        sp.delete(slot)
        free = sp.free_space()
        self.bp.unpin(page, dirty=True)
        self._n -= 1
        self._deleted += 1
        if bno != self._tail:
            self._set_free(bno, free)

    def fetch(self, ptr):
        """Registro do RID. KeyError se o slot não existir ou estiver apagado. Custo: 1 bloco."""
        bno, slot = self._locate(ptr)
        with self.bp.page(self.fid, bno, self.ptype) as page:
            sp = SlottedPage(page.data)
            view = sp.get(slot) if 0 <= slot < sp.n_slots() else None
            if view is None:
                raise KeyError(f"{self.name}: {ptr} não existe")
            return self.codec.decode(view)

    def scan(self):
        """Todos os registros, (RID, registro), bloco a bloco. 1 página fixada por vez. Custo: b leituras."""
        for a, z in list(self._ranges):
            for bno in range(a, z + 1):
                page = self.bp.pin(self.fid, bno, self.ptype, seq=True)
                try:
                    sp = SlottedPage(page.data)
                    for slot in range(sp.n_slots()):
                        view = sp.get(slot)
                        if view is not None:
                            yield RID(bno, slot), self.codec.decode(view)
                finally:
                    self.bp.unpin(page, toss=True)

    def search(self, key):
        raise NotApplicable("heap não tem chave de organização: use scan ou um índice")

    def range(self, lo, hi, lo_inc=True, hi_inc=True):
        raise NotApplicable("heap não é ordenado: use scan ou um índice B+")

    def bulk_load(self, recs):
        """Carga num heap vazio: enche cada bloco até o próximo não caber, na ordem de chegada (guia P2 §5.3).

        Grava os dados contíguos e o mapa depois deles. Custo: b gravações (quando as páginas saem do buffer).
        """
        if self._n or self._ranges:
            raise ValueError(f"{self.name}: bulk_load exige um heap vazio")
        page = sp = None
        closed = []                                        # (bno, livre) dos blocos que não são a cauda
        try:
            for rec in recs:
                data = self.codec.encode(rec)
                if len(data) > self._max:
                    raise _too_large(len(data), self._max)
                if sp is None or sp.insert(data) is None:
                    if page is not None:
                        closed.append((page.bno, sp.free_space()))
                        self.bp.unpin(page, dirty=True)
                    page = self.bp.new_page(self.fid, self.ptype)
                    sp = SlottedPage(page.data)
                    sp.init()
                    sp.insert(data)
                    self._add_data_block(page.bno)
                self._n += 1
        finally:
            if page is not None:
                self._tail = page.bno
                self.bp.unpin(page, dirty=True)
            for bno, free in closed:
                self._set_free(bno, free)
            self._write_meta()

    def reorganize(self):
        """Reescreve o heap só com os registros vivos (os RIDs mudam). Custo: b + 3·b' blocos.

        Copia os vivos para um temporário, recria o arquivo com o mesmo nome e recarrega com bulk_load.
        LIMITAÇÃO: não é atômico; o FileManager não tem rename (ver D-P2-11, em aberto).
        """
        tmp = TempHeap(self.bp, f"{self.name}__reorg", self.schema)
        try:
            for _, rec in self.scan():
                tmp.append(rec)
            tmp.finish()
            self.bp.drop_file(self.fid)
            self.bp.fm.delete(self.fid)
            self.fid = self.bp.fm.create(self.name)
            self._reset()
            self._new_meta()
            self.bulk_load(tmp.scan())
        finally:
            tmp.drop()

    def encode_ptr(self, ptr):
        try:
            return _PTR.pack(*ptr)
        except struct.error as e:
            raise ValueError(f"RID fora da faixa (bloco u32, slot u16): {ptr}") from e

    def decode_ptr(self, b):
        return RID(*_PTR.unpack(b))

    def stats(self):
        return {"blocks": self.bp.fm.n_blocks(self.fid), "records": self._n, "overflow_blocks": 0,
                "levels": 1, "leaf_blocks": 0, "buckets": 0, "meta_blocks": 1 + len(self._fsm),
                "deleted": self._deleted}


class TempHeap:
    """Arquivo temporário (runs, partições) em páginas WORK, sem meta e sem mapa (guia P2 §5.4).

    As páginas só vão para o disco quando saem do buffer: se tudo cabe na área WORK, o custo medido
    fica abaixo da fórmula, que supõe gravar tudo.
    """

    def __init__(self, bp, name, schema):
        self.bp, self.name = bp, name
        self.fid = bp.fm.create(name)
        self.codec = RecordCodec(schema)
        self._max = SlottedPage.max_record(bp.block_size)
        self._out = self._sp = None
        self._bnos = []
        self._n = 0

    def append(self, rec):
        """Acrescenta um registro, mantendo uma página de saída fixada."""
        data = self.codec.encode(rec)
        if len(data) > self._max:
            raise _too_large(len(data), self._max)
        if self._sp is None or self._sp.insert(data) is None:
            if self._out is not None:
                self.bp.unpin(self._out, dirty=True)
            self._out = self.bp.new_page(self.fid, PageType.WORK)
            self._sp = SlottedPage(self._out.data)
            self._sp.init()
            self._sp.insert(data)
            self._bnos.append(self._out.bno)
        self._n += 1

    def finish(self):
        """Solta a página de saída. Chamar antes do scan."""
        if self._out is not None:
            self.bp.unpin(self._out, dirty=True)
            self._out = self._sp = None

    def scan(self):
        """Os registros na ordem do append; 1 página fixada por vez, seq=True, toss=True."""
        if self._out is not None:
            raise RuntimeError(f"{self.name}: chame finish() antes do scan")
        for bno in self._bnos:
            page = self.bp.pin(self.fid, bno, PageType.WORK, seq=True)
            try:
                sp = SlottedPage(page.data)
                for slot in range(sp.n_slots()):
                    yield self.codec.decode(sp.get(slot))
            finally:
                self.bp.unpin(page, toss=True)

    def n_blocks(self):
        return len(self._bnos)

    def n_records(self):
        return self._n

    def drop(self):
        """Descarta as páginas sem gravar e apaga o arquivo. Idempotente."""
        if self.fid is None:
            return
        self.finish()
        self.bp.drop_file(self.fid)
        self.bp.fm.delete(self.fid)
        self.fid = None
