"""Índice hash secundário (campo UNIQUE de um heap). Camada 3, dono: P2.

Implementa: Index (cbd/contracts.py; arquitetura §5.2 e §5.3). Usa: HashFile (P2).
Decisões: D-P2-18 (o índice é um HashFile com páginas INDEX cujos registros são (chave, bloco, slot): a mesma
estrutura do hash primário, só que o bucket guarda o ponteiro em vez do registro, SKS §14.5, p. 659); herda
D-P2-14 a D-P2-17 (função, bucket, N e fator de carga, overflow). Ver docs/decisoes/P2.md.
Entrada: chave + bloco (INT 4 B) + slot (INT 4 B). O ponteiro é o RID do heap (D-P2-10): o índice hash só existe
sobre heap (arquitetura §5.3), então guarda os dois números do RID.
Custo esperado: search = blocos da cadeia do bucket do índice (1 sem overflow), mais 1 bloco do heap no fetch;
insert e delete = a cadeia do bucket, mais o meta.
LIMITAÇÃO: sem params["buckets"] nem ["expected_records"], o N sai do número de registros do arquivo de dados na
criação; índice criado com a tabela vazia e enchido registro a registro fica com N = 1 (cadeia longa) até ser
refeito (o load_table em massa e o reorganize refazem os índices).
"""
from cbd.contracts import Field, FType, NotApplicable, Org, OrgSpec, PageType, RID, Schema
from cbd.files.hash_file import HashFile


class HashIndex:
    """Chave -> RID do heap, num HashFile de registros (chave, bloco, slot)."""

    def __init__(self, spec, hf):
        self.spec, self._hf = spec, hf

    @staticmethod
    def _inner(spec, key_field, data):
        schema = Schema(f"{spec.table}__{spec.column}",
                        (Field("k", key_field.ftype, key_field.size), Field("bno", FType.INT), Field("slot", FType.INT)))
        params = dict(spec.params)
        if "buckets" not in params:
            params.setdefault("expected_records", max(1, data.stats()["records"]))
        return schema, OrgSpec(Org.HASH, "k", spec.unique, params)

    @classmethod
    def create(cls, bp, name, spec, key_field, data):
        """Cria o índice vazio (o catálogo enche com bulk_load). Custo: nenhuma leitura."""
        return cls(spec, HashFile.create(bp, name, *cls._inner(spec, key_field, data), PageType.INDEX))

    @classmethod
    def open(cls, bp, name, spec, key_field, data):
        """Abre um índice existente. Custo: 1 leitura (a meta)."""
        return cls(spec, HashFile.open(bp, name, *cls._inner(spec, key_field, data), PageType.INDEX))

    def search(self, key):
        """RIDs com a chave igual a key (o bucket e a cadeia do índice)."""
        for _, r in self._hf.search(key):
            yield RID(r[1], r[2])

    def range(self, lo, hi, lo_inc=True, hi_inc=True):
        raise NotApplicable("índice hash não guarda ordem: faixa só com B+")

    def insert(self, key, ptr):
        """Acrescenta (key, ptr). DuplicateKey se o índice é UNIQUE e a chave já existe."""
        self._hf.insert((key, ptr[0], ptr[1]))

    def delete(self, key, ptr):
        """Tira a entrada exata (key, ptr). KeyError se ela não existir."""
        found = self._hf.search(key)
        try:
            rid = next((rid for rid, r in found if (r[1], r[2]) == (ptr[0], ptr[1])), None)
        finally:
            found.close()                                # solta a página antes de apagar
        if rid is None:
            raise KeyError(f"{self.spec.table}.{self.spec.column}: entrada ({key!r}, {ptr}) não existe")
        self._hf.delete(rid)

    def bulk_load(self, pairs):
        """Carga num índice vazio a partir de pares (chave, RID); o HashFile ordena por bucket (D-P2-17)."""
        self._hf.bulk_load((k, p[0], p[1]) for k, p in pairs)

    def stats(self):
        return self._hf.stats()

    def close(self):
        self._hf.close()
