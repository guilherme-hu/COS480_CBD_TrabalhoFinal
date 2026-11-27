"""Hash estático primário, versão ingênua v1 (código bruto da IA). Camada 3, dono: P2.

Implementa: DataFile (cbd/contracts.py). Usa: BufferPool, SlottedPage, RecordCodec, hash_value.
Layout do arquivo: blocos 1..N = página primária de cada bucket (bucket i no bloco i + 1);
overflow alocado sob demanda e encadeado pelo campo `next` da SlottedPage.
Ponteiro: RID(block, slot), codificado em 6 B (u32 block, u16 slot).
Custo esperado: search = 1 + overflow do bucket leituras; insert = idem + 1 gravação;
scan = N + overflow leituras.
"""
from __future__ import annotations

import struct
from typing import Any, Iterable, Iterator

from cbd.contracts import (
    RID, DuplicateKey, NotApplicable, OrgSpec, PageType, Record, RecordTooLarge, Schema,
)
from cbd.records.codec import RecordCodec
from cbd.records.hashing import hash_value
from cbd.records.slotted_page import SlottedPage

_PTR = struct.Struct("<IH")
_DATA = PageType.DATA


class HashFile:
    ptr_size = _PTR.size

    def __init__(self, bp, fid: int, schema: Schema, spec: OrgSpec):
        if not spec.key:
            raise ValueError("HashFile precisa de spec.key")
        self.bp = bp
        self.fid = fid
        self.schema = schema
        self.spec = spec
        self.codec = RecordCodec(schema)
        self._k = schema.idx(spec.key)
        self.n_buckets = int(spec.params["buckets"])
        self.seed = int(spec.params.get("seed", 0))
        self._max = SlottedPage.max_record(bp.block_size)
        self._n = 0

    # ------------------------------------------------------------ abrir / fechar
    @classmethod
    def create(cls, bp, name: str, schema: Schema, spec: OrgSpec) -> "HashFile":
        fid = bp.fm.create(name)
        h = cls(bp, fid, schema, spec)
        if h.n_buckets < 1:
            raise ValueError("buckets precisa ser >= 1")
        for i in range(h.n_buckets):
            p = bp.new_page(fid, _DATA)
            assert p.bno == i + 1
            SlottedPage(p.data).init()
            bp.unpin(p, dirty=True)
        return h

    @classmethod
    def open(cls, bp, name: str, schema: Schema, spec: OrgSpec) -> "HashFile":
        h = cls(bp, bp.fm.open(name), schema, spec)
        # ponytail: n_records recontado com um scan (b blocos lidos); guardar no cabeçalho se pesar.
        h._n = sum(1 for _ in h.scan())
        return h

    def close(self) -> None:
        self.bp.close_file(self.fid)

    # ------------------------------------------------------------ hash
    def bucket_of(self, key: Any) -> int:
        return hash_value(key, self.seed) % self.n_buckets

    def _chain(self, bucket: int) -> Iterator[int]:
        """Blocos do bucket: primário e overflow, em ordem. Lê cada um (via buffer) para achar o next."""
        bno = bucket + 1
        while bno:
            yield bno
            with self.bp.page(self.fid, bno, _DATA) as p:
                bno = SlottedPage(p.data).next

    def _page_records(self, bno: int) -> list[tuple[RID, Record]]:
        """Registros vivos do bloco; o pin sai antes de devolver (nada fica fixado entre yields)."""
        out = []
        with self.bp.page(self.fid, bno, _DATA) as p:
            sp = SlottedPage(p.data)
            for s in range(sp.n_slots()):
                raw = sp.get(s)
                if raw is not None:
                    out.append((RID(bno, s), self.codec.decode(raw)))
        return out

    def scan_bucket(self, bucket: int) -> Iterator[tuple[RID, Record]]:
        for bno in self._chain(bucket):
            yield from self._page_records(bno)

    # ------------------------------------------------------------ leitura
    def scan(self) -> Iterator[tuple[RID, Record]]:
        for b in range(self.n_buckets):
            yield from self.scan_bucket(b)

    def search(self, key: Any) -> Iterator[tuple[RID, Record]]:
        for rid, rec in self.scan_bucket(self.bucket_of(key)):
            if rec[self._k] == key:
                yield rid, rec
                if self.spec.unique:
                    return

    def range(self, lo, hi, lo_inc: bool = True, hi_inc: bool = True):
        raise NotApplicable("hash não suporta busca por faixa")

    def fetch(self, ptr: RID) -> Record:
        with self.bp.page(self.fid, ptr.block, _DATA) as p:
            raw = SlottedPage(p.data).get(ptr.slot)
            if raw is None:
                raise KeyError(f"RID {ptr} apagado")
            return self.codec.decode(raw)

    # ------------------------------------------------------------ escrita
    def insert(self, rec: Record) -> RID:
        key = rec[self._k]
        if key is None:
            raise ValueError(f"{self.spec.key}: chave do hash não pode ser NULL")
        data = self.codec.encode(rec)
        if len(data) > self._max:
            raise RecordTooLarge(f"{len(data)} B > {self._max} B")
        if self.spec.unique and next(self.search(key), None) is not None:
            raise DuplicateKey(f"{self.spec.key}={key!r}")
        bno = self.bucket_of(key) + 1
        while True:
            p = self.bp.pin(self.fid, bno, _DATA)
            sp = SlottedPage(p.data)
            slot = sp.insert(data)
            if slot is not None:
                self.bp.unpin(p, dirty=True)
                break
            nxt = sp.next
            if nxt:
                self.bp.unpin(p)
                bno = nxt
                continue
            # bucket cheio: novo bloco de overflow no fim da cadeia
            q = self.bp.new_page(self.fid, _DATA)
            qs = SlottedPage(q.data)
            qs.init()
            slot = qs.insert(data)
            sp.next = q.bno
            bno = q.bno
            self.bp.unpin(q, dirty=True)
            self.bp.unpin(p, dirty=True)
            break
        self._n += 1
        return RID(bno, slot)

    def delete(self, ptr: RID) -> None:
        with self.bp.page(self.fid, ptr.block, _DATA) as p:
            SlottedPage(p.data).delete(ptr.slot)
            p.mark_dirty()
        self._n -= 1

    def bulk_load(self, recs: Iterable[Record]) -> None:
        if self._n:
            raise ValueError("bulk_load exige arquivo vazio")
        for r in recs:
            self.insert(r)

    def reorganize(self) -> None:
        # LIMITAÇÃO: guarda todos os registros numa lista Python (memória O(n), fora do buffer);
        # só serve para tabelas pequenas. Melhorar: copiar para um TempFile e reinserir.
        recs = [r for _, r in self.scan()]
        for b in range(self.n_buckets):
            overflow = list(self._chain(b))[1:]
            with self.bp.page(self.fid, b + 1, _DATA) as p:
                SlottedPage(p.data).init()
                p.mark_dirty()
            for bno in overflow:
                self.bp.free_block(self.fid, bno)
        self._n = 0
        for r in recs:
            self.insert(r)

    # ------------------------------------------------------------ ponteiros e estatísticas
    def encode_ptr(self, ptr: RID) -> bytes:
        return _PTR.pack(ptr.block, ptr.slot)

    def decode_ptr(self, b: bytes) -> RID:
        return RID(*_PTR.unpack(b))

    def stats(self) -> dict[str, int]:
        n_blocks = self.bp.fm.n_blocks(self.fid)
        return {
            "n_records": self._n,
            "n_blocks": n_blocks,
            "n_buckets": self.n_buckets,
            "overflow_blocks": n_blocks - self.n_buckets,
        }
