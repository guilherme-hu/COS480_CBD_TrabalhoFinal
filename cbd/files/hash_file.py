"""Hash estático primário (organização por hash). Camada 3, dono: P2.

Implementa: DataFile e os extras do HashFile (arquitetura §5.2: n_buckets, seed, bucket_of, scan_bucket).
Usa: BufferPool (P1), SlottedPage, RecordCodec, hash_value e os auxiliares do heap (P2).
Decisões: D-P2-10 (RID de 6 B), D-P2-14 (bucket_of), D-P2-15 (bucket = 1 bloco + cadeia pelo next; bucket i
no bloco 2 + i; meta no bloco 1), D-P2-16 (N = params["buckets"], ou ⌈n / (bfr_est · carga)⌉ com carga 0,8,
limitado a params["distinct"]; sem forçar primo), D-P2-17 (insert no 1º bloco da cadeia com espaço; delete só
apaga o slot; bulk_load ordenado por bucket; reorganize só por comando). Ver docs/decisoes/P2.md.

Layout do arquivo:
    bno 1        meta: 'HSH1' | N u32 | seed i64 | n_records u32 | n_overflow u32 | bfr_est u32 | max_chain u32
                       | key_idx u16 | unique u8
    bno 2..N+1   bloco primário do bucket i em 2 + i, alocados juntos: no create se params["buckets"] veio,
                 senão na 1ª carga (o bfr_est precisa do tamanho médio dos 100 primeiros registros)
    bno > N+1    blocos de overflow, ligados pelo next da slotted page (0 = fim da cadeia)

Invariantes: todo bloco de 2 até o último alocado é de dados (nenhum é devolvido: D-P2-17, R1); no máximo 1 página
fixada por iterador (2 só no instante de ligar um bloco novo à cadeia). Quem para um iterador no meio o fecha
(close), senão a página fica fixada. UNIQUE trata NULL como valor: dois NULL na chave são duplicata (nenhuma chave
de hash da bancada é nula).
Custo esperado (cache frio): search = blocos da cadeia do bucket (1 sem overflow; UNIQUE para no 1º achado);
scan = todos os blocos de dados em ordem física, 1 seek; scan_bucket(i) = blocos do bucket i; fetch = 1 bloco;
insert e delete = os blocos da cadeia que tocam, mais o meta (bloco 1), que em geral está no buffer.
"""
import math
import struct

from cbd.contracts import DuplicateKey, FType, NotApplicable, PageType, RID
from cbd.files.heap import TempHeap, _PTR, _encode, _fresh_page
from cbd.records.codec import RecordCodec
from cbd.records.hashing import hash_value
from cbd.records.slotted_page import SlottedPage

_MAGIC = b"HSH1"
_META = struct.Struct("<4sIqIIIIHB")
_DATA = PageType.DATA


class HashFile:
    """Arquivo organizado por hash estático na chave spec.key (UNIQUE ou NOT UNIQUE)."""

    def __init__(self, bp, name, schema, spec, fid):
        self.bp, self.name, self.schema, self.spec, self.fid = bp, name, schema, spec, fid
        self.codec = RecordCodec(schema)
        self.ptr_size = _PTR.size
        self._max = SlottedPage.max_record(bp.block_size)
        self._k = schema.idx(spec.key)
        self._ktype = schema.get(spec.key).ftype
        self.seed = int(spec.params.get("seed", 0))
        self._reset()

    def _reset(self):
        self.n_buckets = 0
        self._n = self._overflow = self._bfr = self._max_chain = 0

    # ------------------------------------------------------------ abrir e fechar
    @classmethod
    def create(cls, bp, name, schema, spec):
        """Cria o arquivo: meta no bloco 1 e, se params["buckets"] veio, os N primários. Custo: nenhuma leitura."""
        h = cls(bp, name, schema, spec, bp.fm.create(name))
        h._new_meta()
        return h

    @classmethod
    def open(cls, bp, name, schema, spec):
        """Abre um hash existente. Custo: 1 leitura (a meta). N e seed vêm da meta, não do spec.

        ValueError se não for um hash, ou se o spec tiver outra chave ou outro UNIQUE que o da criação.
        """
        h = cls(bp, name, schema, spec, bp.fm.open(name))
        try:
            with bp.page(h.fid, 1, _DATA) as p:
                meta = bytes(p.data[:_META.size])
        except ValueError:                               # arquivo sem nenhum bloco
            meta = b""
        if meta[:4] != _MAGIC:
            bp.close_file(h.fid)
            raise ValueError(f"{name}: o bloco 1 não é a meta de um hash")
        _, h.n_buckets, h.seed, h._n, h._overflow, h._bfr, h._max_chain, k, unique = _META.unpack(meta)
        if (k, bool(unique)) != (h._k, bool(spec.unique)):
            bp.close_file(h.fid)
            raise ValueError(f"{name}: criado com a chave na coluna {k} (unique={bool(unique)}), aberto com outro spec")
        return h

    def close(self):
        """Grava a meta e fecha o arquivo (grava as páginas sujas dele). Idempotente."""
        if self.fid is None:
            return
        self._write_meta()
        self.bp.close_file(self.fid)
        self.fid = None

    def _new_meta(self):
        page = self.bp.new_page(self.fid, _DATA)
        bno = page.bno
        self.bp.unpin(page, dirty=True)
        if bno != 1:
            raise RuntimeError(f"{self.name}: a meta precisa ser o bloco 1, veio {bno}")
        if "buckets" in self.spec.params:
            self._alloc(int(self.spec.params["buckets"]))
        self._write_meta()

    def _write_meta(self):
        body = _META.pack(_MAGIC, self.n_buckets, self.seed, self._n, self._overflow, self._bfr, self._max_chain,
                          self._k, self.spec.unique)
        page = self.bp.pin(self.fid, 1, _DATA)
        changed = page.data[:len(body)] != body
        page.data[:len(body)] = body
        self.bp.unpin(page, dirty=changed)              # meta igual não vira gravação

    # ------------------------------------------------------------ buckets (D-P2-15, D-P2-16)
    def _alloc(self, n_buckets):
        """Aloca os N primários em sequência: o bucket i fica no bloco 2 + i."""
        if n_buckets < 1:
            raise ValueError(f"{self.name}: o número de buckets precisa ser ≥ 1, veio {n_buckets}")
        for i in range(n_buckets):
            page = self.bp.new_page(self.fid, _DATA)
            bno = page.bno
            SlottedPage(page.data).init()
            self.bp.unpin(page, dirty=True)
            if bno != 2 + i:
                raise RuntimeError(f"{self.name}: o bucket {i} precisa ser o bloco {2 + i}, veio {bno}")
        self.n_buckets = n_buckets

    def _ensure(self, sizes, n_target):
        """Na 1ª carga: bfr_est pelos 100 primeiros registros e, se ainda não há buckets, N pela carga.

        sizes: tamanhos codificados (bytes) dos registros que chegaram; n_target: registros esperados.
        D-P2-16: N = ⌈n / (bfr_est · carga)⌉, carga = params["load"] (0,8), limitado a params["distinct"].
        """
        if not self._bfr:
            sample = sizes[:100]
            self._bfr = max(1, self._max // math.ceil(sum(sample) / len(sample) + 4))
        if not self.n_buckets:
            p = self.spec.params
            n = math.ceil(n_target / (self._bfr * float(p.get("load", 0.8))))
            if "distinct" in p:
                n = min(n, int(p["distinct"]))
            self._alloc(max(1, n))

    def bucket_of(self, key):
        """Bucket da chave (D-P2-14): coluna INT, (key + seed) mod N; outros tipos, hash_value(key, seed) mod N.

        O ramo segue o tipo da coluna, não o do valor: 5 == 5.0 no Python, e as duas formas caem no mesmo bucket.
        Chave de outro tipo (texto numa coluna INT) vai para o hash_value: a busca não acha nada, sem erro.
        ValueError se o arquivo ainda não tem buckets (nenhuma carga e nenhum params["buckets"]).
        """
        if not self.n_buckets:
            raise ValueError(f"{self.name}: ainda sem buckets (carregue o arquivo ou passe params['buckets'])")
        if isinstance(key, (int, float)):
            if self._ktype is FType.INT:
                return (int(key) + self.seed) % self.n_buckets
            if self._ktype is FType.FLOAT:
                key = float(key)
        return hash_value(key, self.seed) % self.n_buckets

    # ------------------------------------------------------------ leitura
    def search(self, key):
        """(RID, registro) com a chave igual a key: o bucket e a cadeia inteira (UNIQUE para no 1º achado).

        Não insira no mesmo bucket com a busca aberta: ela seguiria os blocos de overflow que o insert cria.
        """
        if not self.n_buckets:
            return
        bno = 2 + self.bucket_of(key)
        while bno:
            page = self.bp.pin(self.fid, bno, _DATA)
            try:
                sp = SlottedPage(page.data)
                for slot in range(sp.n_slots()):
                    view = sp.get(slot)
                    if view is not None:
                        rec = self.codec.decode(view)
                        if rec[self._k] == key:
                            yield RID(bno, slot), rec
                            if self.spec.unique:
                                return
                bno = sp.next
            finally:
                self.bp.unpin(page)

    def scan_bucket(self, i):
        """Todos os registros do bucket i (primário + cadeia), para o hash join do P5. Custo: blocos do bucket i.

        Sem seq=True: o bloco seguinte ao primário 2 + i é o primário de outro bucket, e o prefetch o leria.
        """
        if not 0 <= i < self.n_buckets:
            raise IndexError(f"{self.name}: bucket {i} fora de [0, {self.n_buckets})")
        bno = 2 + i
        while bno:
            page = self.bp.pin(self.fid, bno, _DATA)
            try:
                sp = SlottedPage(page.data)
                yield from self._live(bno, sp)
                bno = sp.next
            finally:
                self.bp.unpin(page, toss=True)

    def scan(self):
        """Todos os registros em ordem física (primários, depois o overflow). Custo: b leituras, 1 seek."""
        for bno in range(2, self._last() + 1):
            page = self.bp.pin(self.fid, bno, _DATA, seq=True)
            try:
                yield from self._live(bno, SlottedPage(page.data))
            finally:
                self.bp.unpin(page, toss=True)

    def _live(self, bno, sp):
        for slot in range(sp.n_slots()):
            view = sp.get(slot)
            if view is not None:
                yield RID(bno, slot), self.codec.decode(view)

    def _last(self):
        return 1 + self.n_buckets + self._overflow

    def _locate(self, ptr):
        """Valida o RID e devolve (bno, slot). KeyError se não apontar para um bloco de dados."""
        bno, slot = ptr
        if type(bno) is not int or type(slot) is not int or not 2 <= bno <= self._last():
            raise KeyError(f"{self.name}: {ptr} não é um bloco de dados")
        return bno, slot

    def fetch(self, ptr):
        """Registro do RID. KeyError se o slot não existir ou estiver apagado. Custo: 1 bloco."""
        bno, slot = self._locate(ptr)
        with self.bp.page(self.fid, bno, _DATA) as page:
            sp = SlottedPage(page.data)
            view = sp.get(slot) if 0 <= slot < sp.n_slots() else None
            if view is None:
                raise KeyError(f"{self.name}: {ptr} não existe")
            return self.codec.decode(view)

    def range(self, lo, hi, lo_inc=True, hi_inc=True):
        raise NotApplicable("hash não guarda ordem: faixa só com B+ (organização ou índice)")

    # ------------------------------------------------------------ escrita (D-P2-17)
    def insert(self, rec):
        """Insere no 1º bloco da cadeia do bucket com espaço; se nenhum serve, bloco novo no fim do arquivo,
        ligado ao último da cadeia. Devolve o RID.

        RecordTooLarge antes de tocar em página (D-P2-06); DuplicateKey em UNIQUE (lê a cadeia inteira).
        ValueError se ainda não há buckets e não veio params["buckets"] nem params["expected_records"].
        """
        data = _encode(self.codec, rec, self._max)
        key = rec[self._k]
        if not self.n_buckets and "expected_records" not in self.spec.params:
            raise ValueError(f"{self.name}: sem buckets: passe params['buckets'] ou ['expected_records'], "
                             "ou carregue com bulk_load")
        # LIMITAÇÃO: sem buckets, o 1º insert estima o bfr só com o próprio registro (o guia pede a média dos
        # 100 primeiros, que só o bulk_load tem). Para carga registro a registro, passe params["buckets"].
        self._ensure([len(data)], self.spec.params.get("expected_records"))
        if self.spec.unique:
            found = self.search(key)
            try:
                if next(found, None) is not None:
                    raise DuplicateKey(f"{self.name}: chave {key!r} já existe")
            finally:
                found.close()                            # solta a página se a busca parou no meio
        bno, length = 2 + self.bucket_of(key), 0
        while True:
            page = self.bp.pin(self.fid, bno, _DATA)
            try:
                sp = SlottedPage(page.data)
                slot = sp.insert(data)
                if slot is not None:
                    page.mark_dirty()
                    return self._inserted(bno, slot)
                if not sp.next:
                    new, _ = _fresh_page(self.bp, self.fid, _DATA, data)
                    new_bno = new.bno
                    self.bp.unpin(new, dirty=True)
                    sp.next = new_bno                    # liga o bloco novo ao fim da cadeia
                    page.mark_dirty()
                    self._overflow += 1
                    self._max_chain = max(self._max_chain, length + 1)
                    return self._inserted(new_bno, 0)
                bno, length = sp.next, length + 1
            finally:
                self.bp.unpin(page)

    def _inserted(self, bno, slot):
        self._n += 1
        self._write_meta()
        return RID(bno, slot)

    def delete(self, ptr):
        """Apaga o registro: o slot vira buraco e a cadeia fica como está (D-P2-17, R1). KeyError se não existir.

        Custo: 1 bloco, mais o meta (bloco 1), em geral no buffer.
        """
        bno, slot = self._locate(ptr)
        with self.bp.page(self.fid, bno, _DATA) as page:
            sp = SlottedPage(page.data)
            if not 0 <= slot < sp.n_slots() or sp.get(slot) is None:
                raise KeyError(f"{self.name}: {ptr} não existe")
            sp.delete(slot)
            page.mark_dirty()
        self._n -= 1
        self._write_meta()

    def bulk_load(self, recs):
        """Carga num hash vazio, ordenada por bucket (D-P2-17, C3): a cadeia de cada bucket fica contígua.

        N sai de params["expected_records"] ou, sem ele, do número de registros carregados. Quem ordena é o próprio
        bulk_load, porque o N só existe depois de ver os registros (o load_table só ordena para a B+).
        Custo (fora da medição): os blocos do arquivo, gravados quando saem do buffer. LIMITAÇÃO: com N maior que a
        área DATA, os primários vazios saem do buffer antes de receber registros: cada um é gravado 2 vezes e
        relido 1 vez (aluno.dre: ~2 084 leituras e ~4 168 gravações na carga).
        """
        self._bulk(recs, None)

    def _bulk(self, recs, n_target):
        if self._n or self._overflow:
            raise ValueError(f"{self.name}: bulk_load exige um arquivo novo, sem registros nem cadeias "
                             "(para refazer um arquivo usado, reorganize)")
        recs = list(recs)                                # preparação: fora da medição (como o sorted da B+)
        if not recs:
            return
        datas = [_encode(self.codec, r, self._max) for r in recs]   # RecordTooLarge antes de tocar em página
        keys = [r[self._k] for r in recs]
        if self.spec.unique and len(set(keys)) != len(keys):
            raise DuplicateKey(f"{self.name}: chave repetida na carga de um hash UNIQUE")
        self._ensure([len(d) for d in datas], n_target or self.spec.params.get("expected_records") or len(recs))
        bk = [self.bucket_of(k) for k in keys]
        page = sp = None
        cur = length = -1
        try:
            for j in sorted(range(len(recs)), key=bk.__getitem__):   # estável: a ordem de chegada no bucket
                if bk[j] != cur:
                    if page is not None:
                        self.bp.unpin(page, dirty=True)
                        page = None
                    cur, length = bk[j], 0
                    page = self.bp.pin(self.fid, 2 + cur, _DATA)
                    sp = SlottedPage(page.data)
                if sp.insert(datas[j]) is None:
                    new, new_sp = _fresh_page(self.bp, self.fid, _DATA, datas[j])
                    sp.next = new.bno
                    self.bp.unpin(page, dirty=True)
                    page, sp = new, new_sp
                    self._overflow += 1
                    length += 1
                    self._max_chain = max(self._max_chain, length)
                self._n += 1
        finally:
            if page is not None:
                self.bp.unpin(page, dirty=True)
            self._write_meta()

    def reorganize(self):
        """Refaz o hash com o N do n atual (D-P2-16), ou com params["buckets"] se veio. Os RIDs mudam.

        Só por comando (D-P2-17, L1). Custo: lê o arquivo (b), grava e relê o temporário (até 2·b', só o que sai
        da área WORK) e grava o arquivo novo (b', mais o efeito da carga com N grande, ver bulk_load).
        LIMITAÇÃO: não é atômico, como o do heap (o FileManager não tem rename), e a recarga põe a tabela em memória.
        """
        k = 0
        while self.bp.fm.exists(f"{self.name}__reorg{k}"):
            k += 1
        tmp = TempHeap(self.bp, f"{self.name}__reorg{k}", self.schema)
        try:
            for _, rec in self.scan():
                tmp.append(rec)
            tmp.finish()
            n, bfr = self._n, self._bfr
            self.bp.drop_file(self.fid)
            self.bp.fm.delete(self.fid)
            self.fid = self.bp.fm.create(self.name)
            self._reset()
            self._new_meta()
            rows = tmp.scan()
            try:
                self._bulk(rows, n)
            finally:
                rows.close()
            if not self.n_buckets and bfr:                # arquivo vazio: N pela conta, com o bfr de antes
                self._bfr = bfr
                self._ensure([], max(n, 1))
                self._write_meta()
        finally:
            tmp.drop()

    # ------------------------------------------------------------ ponteiros e estatísticas
    def encode_ptr(self, ptr):
        try:
            return _PTR.pack(*ptr)
        except struct.error as e:
            raise ValueError(f"RID fora da faixa (bloco u32, slot u16): {ptr}") from e

    def decode_ptr(self, b):
        return RID(*_PTR.unpack(b))

    def stats(self):
        n_b = self.n_buckets
        return {"blocks": self.bp.fm.n_blocks(self.fid), "records": self._n, "overflow_blocks": self._overflow,
                "levels": 1, "leaf_blocks": 0, "buckets": n_b, "meta_blocks": 1,
                "load_x1000": 1000 * self._n // (n_b * self._bfr) if n_b and self._bfr else 0,
                "max_chain": self._max_chain,
                "avg_chain_x1000": 1000 * self._overflow // n_b if n_b else 0}
