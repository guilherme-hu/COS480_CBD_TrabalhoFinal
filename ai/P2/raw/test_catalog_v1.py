"""Testes do Table e do Catalog (P2, #46). Decisões: D-P2-12 (catálogo como 4 relações, Fig. 13.12 do SKS)
e D-P2-13 (gravação forçada no fim de cada operação, detalhes primeiro e a linha da tabela por último).

N1/N2: pilha real (FileManager + BufferPool + HeapFile); os índices secundários ainda não existem (#52, P3),
então um dublê em memória (`FakeIndex`) entra no lugar da fábrica de índices.
N3: o adaptador do P6 (`bench.build.load_physical`) carregando uma base minúscula.
"""
from datetime import date
from types import SimpleNamespace

import pytest

from cbd.contracts import (BufferConfig, DuplicateKey, Field, FType, IndexKind, IndexSpec, Org, OrgSpec,
                           PageType, Schema, StorageConfig, TableStats)
from cbd.engine import Engine
from cbd.files import catalog as catalog_mod
from cbd.records.codec import RecordCodec

S = Schema("t", (Field("id", FType.INT, unique=True), Field("nome", FType.VARCHAR, 20),
                 Field("nasc", FType.DATE, nullable=True), Field("cr", FType.FLOAT),
                 Field("sigla", FType.CHAR, 3), Field("tags", FType.LIST, 10, nullable=True)))
SPEC = OrgSpec(Org.HEAP, params={"fill": 0.9, "seed": 480})


def rows(n):
    return [(i, f"n{i}", None if i % 5 == 0 else date(2000, 1, 1 + i % 28), i / 4, "ab" + str(i % 3),
             None if i % 7 == 0 else (f"t{i}",)) for i in range(1, n + 1)]


class FakeIndex:
    """Índice secundário em memória (dublê de HashIndex/BTreeIndex). O conteúdo sobrevive ao reabrir."""
    store = {}
    log = []

    def __init__(self, name, spec):
        self.name, self.spec = name, spec

    @classmethod
    def create(cls, bp, name, spec, key_field, data):
        cls.store[name] = []
        return cls(name, spec)

    @classmethod
    def open(cls, bp, name, spec, key_field, data):
        assert name in cls.store
        return cls(name, spec)

    def insert(self, key, ptr):
        if self.spec.unique and any(k == key for k, _ in self.store[self.name]):
            raise DuplicateKey(key)
        self.store[self.name].append((key, ptr))
        self.log.append(("idx.insert", key))

    def delete(self, key, ptr):
        self.store[self.name].remove((key, ptr))
        self.log.append(("idx.delete", key))

    def search(self, key):
        return [p for k, p in self.store[self.name] if k == key]

    def bulk_load(self, pairs):
        assert not self.store[self.name]
        self.store[self.name].extend(pairs)

    def stats(self):
        return {"blocks": 0, "records": len(self.store[self.name]), "overflow_blocks": 0, "levels": 1,
                "leaf_blocks": 0, "buckets": 0, "meta_blocks": 0}

    def close(self):
        pass


@pytest.fixture(autouse=True)
def fake_index(monkeypatch):
    FakeIndex.store, FakeIndex.log = {}, []
    monkeypatch.setattr(catalog_mod, "_index_cls", lambda kind: FakeIndex)


@pytest.fixture
def engine(tmp_path):
    """Abre o Engine na mesma pasta quantas vezes o teste pedir; confere pin e fecha no fim."""
    opened = []

    def make(block_size=4096):
        e = Engine(StorageConfig(root_dir=str(tmp_path), block_size=block_size), BufferConfig())
        opened.append(e)
        return e
    yield make
    for e in opened:
        if not getattr(e, "closed", False):
            assert e.bp.pinned() == 0, "pin sem unpin"
            e.close()


def reopen(e, engine):
    assert e.bp.pinned() == 0
    e.close()
    e.closed = True
    return engine()


# ---------- persistência (critério de aceite da #46) ----------
def test_catalog_persist(engine):
    e = engine()
    e.catalog.create_table(S, SPEC)
    e.catalog.load_table("t", rows(40))
    e.catalog.create_index(IndexSpec("t", "id", IndexKind.HASH, True))
    before = (e.catalog.tables(), e.catalog.table("t").stats())
    t = e.catalog.table("t")
    schema, spec, idx_spec = t.schema, t.data.spec, t.indexes["id"].spec

    e = reopen(e, engine)
    t = e.catalog.table("t")
    assert e.catalog.tables() == before[0] == ["t"]
    assert t.schema == schema == S
    assert t.data.spec == spec == SPEC
    assert t.indexes["id"].spec == idx_spec
    assert t.stats() == before[1]
    assert t.stats().min["nasc"] == date(2000, 1, 1)            # DATE volta como date, não como texto
    assert sorted(r for _, r in t.data.scan()) == rows(40)


def test_stats_follow_the_catalog_formulas(engine):
    e = engine()
    e.catalog.create_table(S, SPEC)
    recs = rows(60)
    e.catalog.load_table("t", recs)
    st = e.catalog.table("t").stats()
    data = e.catalog.table("t").data.stats()
    codec = RecordCodec(S)
    assert isinstance(st, TableStats)
    assert st.n_records == 60
    assert st.n_blocks == data["blocks"] - data["meta_blocks"]   # heap: blocos de dados (guia P2 §8.3)
    assert st.bfr == pytest.approx(60 / st.n_blocks)
    assert st.record_size == pytest.approx(sum(len(codec.encode(r)) for r in recs) / 60)
    # V(A, r) conta só valores não nulos, como o stadistinct do PostgreSQL.
    assert st.distinct == {"id": 60, "nome": 60, "nasc": 28, "cr": 60, "sigla": 3,
                           "tags": len({r[5] for r in recs if r[5] is not None})}
    assert st.min == {"id": 1, "nasc": date(2000, 1, 1), "cr": 0.25, "sigla": "ab0"}
    assert st.max == {"id": 60, "nasc": date(2000, 1, 28), "cr": 15.0, "sigla": "ab2"}


def test_stats_before_any_load_are_empty(engine):
    e = engine()
    t = e.catalog.create_table(S, SPEC)
    assert t.stats() == TableStats(0, 0, 0.0, 0.0, {}, {}, {})


# ---------- erros: nada é criado ----------
def test_create_table_errors_create_nothing(engine):
    e = engine()
    e.catalog.create_table(S, SPEC)
    with pytest.raises(ValueError, match="existe"):
        e.catalog.create_table(S, SPEC)
    with pytest.raises(ValueError, match="chave"):
        e.catalog.create_table(Schema("u", S.fields), OrgSpec(Org.HASH, "naoexiste", True))
    with pytest.raises(ValueError, match="número"):
        e.catalog.create_table(Schema("v", S.fields), OrgSpec(Org.HEAP, params={"modo": "x"}))
    with pytest.raises(ValueError, match="reservado"):
        e.catalog.create_table(Schema("catalog__x", S.fields), SPEC)
    assert e.catalog.tables() == ["t"]
    assert not any(e.fm.exists(n) for n in ("u", "v", "catalog__x"))
    with pytest.raises(KeyError):
        e.catalog.table("nada")


def test_create_index_errors(engine):
    e = engine()
    e.catalog.create_table(S, SPEC)
    with pytest.raises(KeyError):
        e.catalog.create_index(IndexSpec("nada", "id", IndexKind.HASH, True))
    with pytest.raises(KeyError):
        e.catalog.create_index(IndexSpec("t", "naoexiste", IndexKind.HASH, True))
    e.catalog.create_index(IndexSpec("t", "id", IndexKind.HASH, True))
    with pytest.raises(ValueError, match="existe"):
        e.catalog.create_index(IndexSpec("t", "id", IndexKind.HASH, True))


# ---------- Table ----------
def test_table_delete_removes_index_entry_before_the_slot(engine):
    # Armadilha do guia P2 §10: o índice precisa do registro (a chave) antes de o slot ser liberado.
    e = engine()
    e.catalog.create_table(S, SPEC)
    e.catalog.create_index(IndexSpec("t", "id", IndexKind.HASH, True))
    t = e.catalog.table("t")
    ptr = t.insert(rows(3)[2])
    orig = t.data.delete
    t.data.delete = lambda p: (FakeIndex.log.append(("data.delete", p)), orig(p))[1]
    t.delete(ptr)
    assert FakeIndex.log[-2:] == [("idx.delete", 3), ("data.delete", ptr)]
    assert t.indexes["id"].search(3) == []
    assert [r for _, r in t.data.scan()] == []


def test_table_insert_rolls_back_on_duplicate_key(engine):
    e = engine()
    e.catalog.create_table(S, SPEC)
    e.catalog.create_index(IndexSpec("t", "cr", IndexKind.BTREE, False))    # aceita antes ...
    e.catalog.create_index(IndexSpec("t", "id", IndexKind.HASH, True))      # ... de este recusar
    t = e.catalog.table("t")
    a, b = rows(2)
    t.insert(a)
    with pytest.raises(DuplicateKey):
        t.insert((a[0],) + b[1:])                               # dre repetido, cr novo
    assert [r for _, r in t.data.scan()] == [a]                 # o registro não ficou no arquivo
    assert t.indexes["cr"].search(b[3]) == []                   # nem no índice que já tinha aceitado


def test_create_index_uses_existing_rows_and_skips_none(engine):
    e = engine()
    e.catalog.create_table(S, SPEC)
    e.catalog.load_table("t", rows(10))
    idx = e.catalog.create_index(IndexSpec("t", "nasc", IndexKind.BTREE, False))
    keys = [k for k, _ in FakeIndex.store["t__nasc"]]
    assert len(keys) == 8 and keys == sorted(keys)               # 5 e 10 têm nasc NULL
    t = e.catalog.table("t")
    assert all(t.data.fetch(p)[2] == k for k, p in FakeIndex.store["t__nasc"])
    assert t.indexes["nasc"] is idx


def test_load_table_bulk_false_inserts_one_by_one(engine, monkeypatch):
    e = engine()
    e.catalog.create_table(S, SPEC)
    e.catalog.create_index(IndexSpec("t", "id", IndexKind.HASH, True))
    calls = []
    t = e.catalog.table("t")
    orig = t.insert
    monkeypatch.setattr(t, "insert", lambda r: (calls.append(r), orig(r))[1])
    e.catalog.load_table("t", rows(25), bulk=False)
    assert calls == rows(25)
    assert len(FakeIndex.store["t__id"]) == 25
    assert t.stats().n_records == 25


def test_load_table_bulk_rebuilds_existing_indexes(engine):
    e = engine()
    e.catalog.create_table(S, SPEC)
    e.catalog.create_index(IndexSpec("t", "id", IndexKind.HASH, True))
    e.catalog.load_table("t", rows(30))
    t = e.catalog.table("t")
    assert sorted(k for k, _ in FakeIndex.store["t__id"]) == list(range(1, 31))
    assert all(t.data.fetch(p)[0] == k for k, p in FakeIndex.store["t__id"])


def test_reorganize_rebuilds_the_indexes(engine):
    e = engine()
    e.catalog.create_table(S, SPEC)
    e.catalog.create_index(IndexSpec("t", "id", IndexKind.HASH, True))
    e.catalog.load_table("t", rows(50))
    t = e.catalog.table("t")
    for k, p in list(FakeIndex.store["t__id"]):
        if k % 2:
            t.delete(p)
    t.reorganize()
    assert sorted(k for k, _ in FakeIndex.store["t__id"]) == list(range(2, 51, 2))
    assert all(t.data.fetch(p)[0] == k for k, p in FakeIndex.store["t__id"])


# ---------- D-P2-13: gravação forçada, em ordem ----------
def forced_files(e):
    log = []
    orig = e.bp.force

    def spy(page):
        log.append(e.fm.name_of(page.fid))
        orig(page)
    e.bp.force = spy
    return log


def test_create_table_forces_details_before_the_table_row(engine):
    e = engine()
    log = forced_files(e)
    e.catalog.create_table(S, SPEC)
    assert "catalog__relacoes" in log and "catalog__atributos" in log and "catalog__params" in log
    first_rel = log.index("catalog__relacoes")
    assert all(n == "catalog__relacoes" for n in log[first_rel:])
    assert e.bp.stats.forced_writes == len(log) > 0


def test_create_index_forces_params_before_the_index_row(engine):
    e = engine()
    e.catalog.create_table(S, SPEC)
    log = forced_files(e)
    e.catalog.create_index(IndexSpec("t", "id", IndexKind.HASH, True, {"buckets": 7}))
    first = log.index("catalog__indices")
    assert "catalog__params" in log[:first] and all(n == "catalog__indices" for n in log[first:])


def test_drop_table_forces_the_table_row_first(engine):
    e = engine()
    e.catalog.create_table(S, SPEC)
    e.catalog.create_index(IndexSpec("t", "id", IndexKind.HASH, True))
    e.catalog.load_table("t", rows(5))
    log = forced_files(e)
    e.catalog.drop_table("t")
    assert log[0] == "catalog__relacoes"
    assert e.catalog.tables() == [] and not e.fm.exists("t")
    e = reopen(e, engine)
    assert e.catalog.tables() == []


def test_drop_index(engine):
    e = engine()
    e.catalog.create_table(S, SPEC)
    e.catalog.create_index(IndexSpec("t", "id", IndexKind.HASH, True, {"buckets": 3}))
    log = forced_files(e)
    e.catalog.drop_index("t", "id")
    assert log[0] == "catalog__indices"
    assert e.catalog.table("t").indexes == {}
    e = reopen(e, engine)
    assert e.catalog.table("t").indexes == {}


def test_no_dirty_catalog_page_after_each_operation(engine):
    e = engine()
    ops = [lambda: e.catalog.create_table(S, SPEC),
           lambda: e.catalog.create_index(IndexSpec("t", "id", IndexKind.HASH, True)),
           lambda: e.catalog.load_table("t", rows(20)),
           lambda: e.catalog.analyze("t"),
           lambda: e.catalog.drop_index("t", "id")]
    for op in ops:
        op()
        dirty = [f for f in e.bp._areas[PageType.CATALOG].frames if f.fid >= 0 and f.dirty]
        assert dirty == []


# ---------- leitura tolerante (D-P2-13: linhas órfãs e repetidas) ----------
def test_orphan_and_repeated_rows_are_ignored_on_open(engine):
    e = engine()
    e.catalog.create_table(S, SPEC)
    e.catalog.load_table("t", rows(5))
    rel, atr = e.catalog._heaps["relacoes"], e.catalog._heaps["atributos"]
    # uma tabela que só tem linhas de atributos (queda antes da linha da tabela) ...
    atr.insert(("fantasma", 0, "x", "INT", 0, 0, 0, None, None, None))
    # ... e um analyze interrompido: a linha da tabela e um atributo repetidos
    rel.insert(next(r for _, r in rel.scan() if r[0] == "t"))
    atr.insert(next(r for _, r in atr.scan() if r[0] == "t" and r[1] == 0))
    stats = e.catalog.table("t").stats()
    e = reopen(e, engine)
    assert e.catalog.tables() == ["t"]
    assert e.catalog.table("t").schema == S
    assert e.catalog.table("t").stats() == stats


def test_analyze_twice_keeps_one_row_per_attribute(engine):
    e = engine()
    e.catalog.create_table(S, SPEC)
    e.catalog.load_table("t", rows(10))
    e.catalog.analyze("t")
    e.catalog.analyze("t")
    atr = [r for _, r in e.catalog._heaps["atributos"].scan()]
    rel = [r for _, r in e.catalog._heaps["relacoes"].scan()]
    assert len(atr) == len(S.fields) and len(rel) == 1


# ---------- temp ----------
def test_temp_is_outside_the_catalog(engine):
    e = engine()
    a, b = e.catalog.temp(S), e.catalog.temp(S)
    assert a.name != b.name
    for r in rows(3):
        a.append(r)
    a.finish()
    assert list(a.scan()) == rows(3)
    assert e.catalog.tables() == []
    a.drop()
    b.drop()


# ---------- N3: o adaptador do P6 com a pilha real ----------
def test_load_physical_tiny_base_heap_variants(engine):
    from bench.build import load_physical, physical_plan
    from bench.generator import Dataset
    from bench.schema import ACCESS_COLUMNS
    ds = Dataset(scale=0.002)
    stats = {t: {"n": ds.counts[t], "estimated_bfr": 10.0, "distinct": {c: 5 for c in cols}}
             for t, cols in ACCESS_COLUMNS.items()}
    # Só as variantes heap, sem índices: HashFile (#48), HashIndex (#52) e a B+ (P3) ainda não existem.
    plan = [dict(item, indexes=[]) for item in physical_plan(stats) if item["org"]["org"] == Org.HEAP]
    e = engine()
    out = load_physical(e, SimpleNamespace(records=ds.records), plan)
    assert [r["variant"] for r in out] == [p["name"] for p in plan]
    for r, p in zip(out, plan):
        assert r["n"] == ds.counts[p["source"]] and r["b"] > 0
    e = reopen(e, engine)
    assert e.catalog.tables() == sorted(p["name"] for p in plan)
    t = e.catalog.table("aluno__heap")
    assert sorted(r for _, r in t.data.scan()) == sorted(ds.records("aluno"))
