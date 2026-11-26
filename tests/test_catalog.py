"""Testes do Table e do Catalog (P2, #46). Decisões: D-P2-12 (catálogo como 4 relações, Fig. 13.12 do SKS)
e D-P2-13 (gravação forçada no fim de cada operação, detalhes primeiro e a linha da tabela por último).

N1/N2: pilha real (FileManager + BufferPool + HeapFile); os índices secundários ainda não existem (#52, P3),
então um dublê em memória (`FakeIndex`) entra no lugar da fábrica de índices.
N3: o adaptador do P6 (`bench.build.load_physical`) carregando uma base minúscula.
"""
import math
from datetime import date
from types import SimpleNamespace

import pytest

from cbd.contracts import (BufferConfig, DuplicateKey, Field, FType, IndexKind, IndexSpec, Org, OrgSpec,
                           PageType, Schema, StorageConfig, TableStats)
from cbd.engine import Engine
from cbd.files import catalog as catalog_mod
from cbd.files.heap import HeapFile
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
    log, f0 = forced_files(e), e.bp.stats.forced_writes        # a base nova já forçou as 4 relações vazias
    e.catalog.create_table(S, SPEC)
    assert "catalog__relacoes" in log and "catalog__atributos" in log and "catalog__params" in log
    first_rel = log.index("catalog__relacoes")
    assert all(n == "catalog__relacoes" for n in log[first_rel:])
    assert e.bp.stats.forced_writes - f0 == len(log) > 0


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


def test_load_physical_tiny_base_hash_variants(engine):
    # #48: as variantes hash do plano do P6 (seed, load, distinct; os pares _eq_ com buckets forçado) carregam
    # pelo catálogo e voltam iguais depois de reabrir. Sem índices: HashIndex (#52) e a B+ (P3) ainda não existem.
    from bench.build import load_physical, physical_plan
    from bench.generator import Dataset
    from bench.schema import ACCESS_COLUMNS, SCHEMAS
    ds = Dataset(scale=0.002)
    stats = {t: {"n": ds.counts[t], "estimated_bfr": 10.0,
                 "distinct": {c: len({r[SCHEMAS[t].idx(c)] for r in ds.records(t)}) for c in cols}}
             for t, cols in ACCESS_COLUMNS.items()}
    plan = [dict(item, indexes=[]) for item in physical_plan(stats) if item["org"]["org"] == Org.HASH]
    assert len(plan) == 6 + 6          # 6 colunas de acesso (__hs_<col>) + 3 joins × 2 lados (_eq_)
    e = engine()
    load_physical(e, SimpleNamespace(records=ds.records), plan)
    before = {p["name"]: e.catalog.table(p["name"]).data.stats() for p in plan}
    e = reopen(e, engine)
    for p in plan:
        t = e.catalog.table(p["name"])
        assert t.data.stats() == before[p["name"]]
        params = p["org"]["params"]
        assert t.data.seed == params["seed"]
        if "buckets" in params:
            assert t.data.n_buckets == params["buckets"]
        else:                                          # load e distinct chegaram do plano ao HashFile (D-P2-16)
            n = math.ceil(params["expected_records"] / (t.data._bfr * params["load"]))
            assert t.data.n_buckets == min(n, params.get("distinct", n))
        recs = list(ds.records(p["source"]))
        assert sorted(r for _, r in t.data.scan()) == sorted(map(tuple, recs))
        k = SCHEMAS[p["source"]].idx(p["org"]["key"])
        v = recs[0][k]
        assert sorted(r for _, r in t.data.search(v)) == sorted(tuple(r) for r in recs if r[k] == v)
        assert t.stats().n_blocks == before[p["name"]]["blocks"] - 1    # analyze: blocos menos o meta


# ---------- 1ª revisão (25/11): testes do revisor de qualidade que matam os mutantes sobreviventes ----------
def test_k_params_come_back_as_int(engine):
    e = engine()
    e.catalog.create_table(S, SPEC)
    e = reopen(e, engine)
    p = e.catalog.table("t").data.spec.params
    assert p == {"fill": 0.9, "seed": 480} and type(p["seed"]) is int


def test_k_index_params_persist_and_stay_separate(engine):
    e = engine()
    e.catalog.create_table(S, SPEC)
    e.catalog.create_index(IndexSpec("t", "id", IndexKind.HASH, True, {"buckets": 7, "seed": 3}))
    e = reopen(e, engine)
    t = e.catalog.table("t")
    assert t.indexes["id"].spec.params == {"buckets": 7, "seed": 3}
    assert t.data.spec.params == SPEC.params


def test_k_orgspec_key_unique_persist(engine, monkeypatch):
    monkeypatch.setattr(catalog_mod, "_datafile_cls", lambda org: HeapFile)   # HashFile (#48) ainda não existe
    e = engine()
    spec = OrgSpec(Org.HASH, "id", True, {"buckets": 5})
    e.catalog.create_table(Schema("u", S.fields), spec)
    e = reopen(e, engine)
    assert e.catalog.table("u").data.spec == spec


def _catalog_rows(e):
    return {k: [r for _, r in h.scan()] for k, h in e.catalog._heaps.items()}


def test_k_drop_table_leaves_no_rows(engine):
    e = engine()
    e.catalog.create_table(S, SPEC)
    e.catalog.create_index(IndexSpec("t", "id", IndexKind.HASH, True, {"buckets": 3}))
    e.catalog.drop_table("t")
    assert all(v == [] for v in _catalog_rows(e).values())
    # recriar com o mesmo nome e outros parâmetros não herda nada da tabela antiga
    e.catalog.create_table(S, OrgSpec(Org.HEAP, params={"fill": 0.5}))
    e = reopen(e, engine)
    assert e.catalog.table("t").data.spec.params == {"fill": 0.5}
    assert e.catalog.table("t").schema == S


def test_k_drop_index_leaves_no_rows(engine):
    e = engine()
    e.catalog.create_table(S, SPEC)
    e.catalog.create_index(IndexSpec("t", "id", IndexKind.HASH, True, {"buckets": 3}))
    e.catalog.drop_index("t", "id")
    r = _catalog_rows(e)
    assert r["indices"] == [] and not any(o.startswith("t.") for o, _, _ in r["params"])


def test_k_analyze_writes_new_before_deleting_old(engine):
    e = engine()
    e.catalog.create_table(S, SPEC)
    e.catalog.load_table("t", rows(5))
    log = []
    for k, h in e.catalog._heaps.items():
        for op in ("insert", "delete", "force"):
            orig = getattr(h, op)
            setattr(h, op, (lambda o, k, op: lambda *a: (log.append((op, k)), o(*a))[1])(orig, k, op))
    e.catalog.analyze("t")
    for k in ("atributos", "relacoes"):
        ev = [op for op, kk in log if kk == k]
        assert ev.index("force") < ev.index("delete"), k            # a linha nova chegou ao disco antes
    forces = [k for op, k in log if op == "force"]
    assert max(i for i, k in enumerate(forces) if k == "atributos") < forces.index("relacoes")


def test_k_create_table_flushes_the_new_file(engine):
    e = engine()
    t = e.catalog.create_table(S, SPEC)
    assert [f for a in e.bp._areas.values() for f in a.frames if f.fid == t.data.fid and f.dirty] == []


def test_k_long_or_dotted_name_creates_nothing(engine):
    e = engine()
    with pytest.raises(ValueError):
        e.catalog.create_table(Schema("x" * 65, S.fields), SPEC)
    with pytest.raises(ValueError, match="reservado"):
        e.catalog.create_table(Schema("a.b", S.fields), SPEC)
    assert not e.fm.exists("x" * 65) and not e.fm.exists("a.b") and e.catalog.tables() == []


def test_k_bad_params_rejected(engine):
    e = engine()
    for p in ({"x": True}, {"x": 2 ** 60}):
        with pytest.raises(ValueError):
            e.catalog.create_table(S, OrgSpec(Org.HEAP, params=p))
    assert e.catalog.tables() == []


def test_k_create_index_bad_param_creates_nothing(engine):
    e = engine()
    e.catalog.create_table(S, SPEC)
    with pytest.raises(ValueError):
        e.catalog.create_index(IndexSpec("t", "id", IndexKind.HASH, True, {"k" * 40: 1}))
    assert "t__id" not in FakeIndex.store and e.catalog.table("t").indexes == {}


def test_k_repeated_rows_last_wins(engine):
    e = engine()
    e.catalog.create_table(S, SPEC)
    e.catalog.load_table("t", rows(5))
    rel, atr = e.catalog._heaps["relacoes"], e.catalog._heaps["atributos"]
    r = next(r for _, r in rel.scan() if r[0] == "t")
    rel.insert(r[:5] + (999,) + r[6:])
    a = next(r for _, r in atr.scan() if r[0] == "t" and r[1] == 0)
    atr.insert(a[:7] + (77,) + a[8:])
    # "vale a última lida" na ordem do scan (o slot apagado é reusado: a cópia nova pode vir antes)
    last_n = [r[5] for _, r in rel.scan() if r[0] == "t"][-1]
    last_d = [r[7] for _, r in atr.scan() if r[0] == "t" and r[1] == 0][-1]
    e = reopen(e, engine)
    st = e.catalog.table("t").stats()
    assert (st.n_records, st.distinct["id"]) == (last_n, last_d)


def test_k_orphan_index_and_param_rows_ignored(engine):
    e = engine()
    e.catalog.create_table(S, SPEC)
    e.catalog._heaps["indices"].insert(("fantasma", "x", "HASH", 0, "fantasma__x"))
    e.catalog._heaps["params"].insert(("fantasma", "b", 1.0))
    e.catalog._heaps["params"].insert(("fantasma.x", "b", 1.0))
    e = reopen(e, engine)
    assert e.catalog.tables() == ["t"]


def test_k_null_keys_skip_the_index_on_insert_and_delete(engine):
    e = engine()
    e.catalog.create_table(S, SPEC)
    e.catalog.create_index(IndexSpec("t", "nasc", IndexKind.BTREE, False))
    t = e.catalog.table("t")
    r5 = rows(5)[4]                                                  # nasc NULL
    assert r5[2] is None
    p = t.insert(r5)
    assert FakeIndex.store["t__nasc"] == []
    t.delete(p)
    assert list(t.data.scan()) == []


def test_k_rollback_undoes_every_accepting_index(engine):
    e = engine()
    e.catalog.create_table(S, SPEC)
    e.catalog.create_index(IndexSpec("t", "cr", IndexKind.BTREE, False))
    e.catalog.create_index(IndexSpec("t", "nome", IndexKind.BTREE, False))
    e.catalog.create_index(IndexSpec("t", "id", IndexKind.HASH, True))
    t = e.catalog.table("t")
    a, b = rows(2)
    t.insert(a)
    with pytest.raises(DuplicateKey):
        t.insert((a[0],) + b[1:])
    assert t.indexes["cr"].search(b[3]) == [] and t.indexes["nome"].search(b[1]) == []


def test_k_close_closes_indexes(engine, monkeypatch):
    closed = []
    monkeypatch.setattr(FakeIndex, "close", lambda self: closed.append(self.name))
    e = engine()
    e.catalog.create_table(S, SPEC)
    e.catalog.create_index(IndexSpec("t", "id", IndexKind.HASH, True))
    reopen(e, engine)
    assert "t__id" in closed


def test_k_temp_skips_existing_file(engine):
    e = engine()
    e.fm.create("__temp0") if hasattr(e.fm, "create") else pytest.skip()
    t = e.catalog.temp(S)
    assert t.name != "__temp0"
    t.drop()


# ---------- 1ª revisão (25/11): defeitos achados pelos revisores de corretude, I/O e contrato ----------
def dirty_frames(e, fid=None, ptype=None):
    return [(e.fm.name_of(f.fid), f.bno) for a, area in e.bp._areas.items() if ptype in (None, a)
            for f in area.frames if f.fid >= 0 and f.dirty and fid in (None, f.fid)]


def test_orphans_are_removed_on_open_and_do_not_leak_into_a_new_table(engine):
    # Queda num drop_table depois de a linha de `relacoes` sair: sobram atributos e params órfãos.
    e = engine()
    e.catalog._heaps["atributos"].insert(("fantasma", 1, "velho", "INT", 0, 0, 0, None, None, None))
    e.catalog._heaps["params"].insert(("fantasma", "fill", 0.3))
    e = reopen(e, engine)                                   # a reabertura apaga as órfãs ...
    assert all(r[0] != "fantasma" for k in ("atributos", "params") for _, r in e.catalog._heaps[k].scan())
    e.catalog.create_table(Schema("fantasma", S.fields[:1]), OrgSpec(Org.HEAP))
    e = reopen(e, engine)                                   # ... e elas não voltam na tabela recriada
    t = e.catalog.table("fantasma")
    assert t.schema.fields == S.fields[:1] and t.data.spec.params == {}


def test_table_and_index_file_names_do_not_collide(engine):
    e = engine()
    e.catalog.create_table(Schema("a__b", S.fields), SPEC)
    e.catalog.create_table(Schema("a", (S.fields[0], Field("b", FType.INT))), OrgSpec(Org.HEAP))
    with pytest.raises(ValueError, match="nome"):
        e.catalog.create_index(IndexSpec("a", "b", IndexKind.HASH, False))   # seria o arquivo a__b
    e.catalog.create_index(IndexSpec("a", "id", IndexKind.HASH, True))
    with pytest.raises(ValueError, match="nome"):
        e.catalog.create_table(Schema("a__id", S.fields), SPEC)              # já é o arquivo do índice a.id
    with pytest.raises(ValueError, match="reservado"):
        e.catalog.create_table(Schema("__temp7", S.fields), SPEC)


def test_reorganize_temp_name_does_not_hit_a_table(engine):
    e = engine()
    e.catalog.create_table(S, SPEC)
    e.catalog.load_table("t", rows(20))
    e.catalog.create_table(Schema("t__reorg0", S.fields), SPEC)
    e.catalog.load_table("t__reorg0", rows(7))
    e.catalog.table("t").reorganize()
    assert sorted(r for _, r in e.catalog.table("t__reorg0").data.scan()) == rows(7)
    assert sorted(r for _, r in e.catalog.table("t").data.scan()) == rows(20)


def test_failed_rebuild_keeps_a_live_index(engine, monkeypatch):
    e = engine()
    e.catalog.create_table(S, SPEC)
    e.catalog.create_index(IndexSpec("t", "sigla", IndexKind.HASH, True))
    t = e.catalog.table("t")
    old = t.indexes["sigla"]                                 # o arquivo dele é apagado na reconstrução
    # Falha simulada ao encher o índice novo. Sem chave repetida: desde a revisão da #52, a chave repetida é
    # recusada antes de carregar o heap (test_bulk_load_with_a_repeated_key_leaves_nothing) e não chega aqui.
    monkeypatch.setattr(FakeIndex, "bulk_load", lambda self, pairs: (_ for _ in ()).throw(DuplicateKey("ab0")))
    with pytest.raises(DuplicateKey):
        e.catalog.load_table("t", rows(3))                   # siglas ab1, ab2, ab0: distintas
    assert t.indexes["sigla"] is not old                     # a Table aponta para o índice novo, vivo
    t.insert((99, "z", None, 1.0, "zz9", None))
    assert t.indexes["sigla"].search("zz9")


def test_failed_create_index_leaves_nothing(engine, monkeypatch):
    orig = FakeIndex.create.__func__
    monkeypatch.setattr(FakeIndex, "create", classmethod(
        lambda cls, bp, name, *a: (bp.fm.create(name), orig(cls, bp, name, *a))[1]))   # índice com arquivo
    e = engine()
    e.catalog.create_table(S, SPEC)
    e.catalog.load_table("t", rows(3))
    monkeypatch.setattr(FakeIndex, "bulk_load", lambda self, pairs: (_ for _ in ()).throw(RuntimeError("falhou")))
    with pytest.raises(RuntimeError):
        e.catalog.create_index(IndexSpec("t", "id", IndexKind.HASH, True))
    assert e.catalog.table("t").indexes == {} and [r for _, r in e.catalog._heaps["indices"].scan()] == []
    assert not e.fm.exists("t__id")                          # o arquivo novo não ficou aberto e órfão
    monkeypatch.setattr(FakeIndex, "bulk_load", lambda self, pairs: self.store[self.name].extend(pairs))
    e.catalog.create_index(IndexSpec("t", "id", IndexKind.HASH, True))      # dá para criar de novo


def test_load_table_writes_the_data_before_the_stats(engine):
    e = engine()
    e.catalog.create_table(S, SPEC)
    e.catalog.load_table("t", rows(300))
    assert dirty_frames(e, fid=e.catalog.table("t").data.fid) == []


def test_no_dirty_catalog_page_with_a_bigger_area(tmp_path):
    # Com 16 frames de CATALOG nada é expulso: as páginas da base nova precisam ser forçadas de propósito.
    areas = {PageType.CATALOG: 16, PageType.INDEX: 64, PageType.DATA: 128, PageType.WORK: 64}
    e = Engine(StorageConfig(root_dir=str(tmp_path)), BufferConfig(areas=areas))
    try:
        assert dirty_frames(e, ptype=PageType.CATALOG) == []
        e.catalog.create_table(S, SPEC)
        e.catalog.load_table("t", rows(30))
        e.catalog.drop_table("t")
        assert dirty_frames(e, ptype=PageType.CATALOG) == []
    finally:
        e.close()


def test_empty_stats_are_not_shared(engine):
    e = engine()
    a = e.catalog.create_table(S, SPEC)
    b = e.catalog.create_table(Schema("u", S.fields), SPEC)
    a.stats().distinct["id"] = 99
    assert b.stats().distinct == {}


def test_spec_with_string_org_is_normalized(engine):
    e = engine()
    t = e.catalog.create_table(S, OrgSpec("HEAP"))
    idx = e.catalog.create_index(IndexSpec("t", "id", "HASH", True))
    assert t.data.spec.org is Org.HEAP and idx.spec.kind is IndexKind.HASH


def test_drop_deletes_index_files_by_name(engine, monkeypatch):
    # O contrato não expõe o fid: o catálogo acha o arquivo do índice pelo nome no FileManager.
    orig = FakeIndex.create.__func__

    def create(cls, bp, name, spec, key_field, data):
        bp.fm.create(name)
        return orig(cls, bp, name, spec, key_field, data)
    monkeypatch.setattr(FakeIndex, "create", classmethod(create))
    e = engine()
    e.catalog.create_table(S, SPEC)
    e.catalog.create_index(IndexSpec("t", "id", IndexKind.HASH, True))
    e.catalog.create_index(IndexSpec("t", "cr", IndexKind.BTREE, False))
    assert e.fm.exists("t__id") and e.fm.exists("t__cr")
    e.catalog.drop_index("t", "id")
    assert not e.fm.exists("t__id")
    e.catalog.drop_table("t")
    assert not e.fm.exists("t__cr") and not e.fm.exists("t")


def test_data_stats_have_the_keys_of_section_6_3(engine):
    e = engine()
    t = e.catalog.create_table(S, SPEC)
    keys = {"blocks", "records", "overflow_blocks", "levels", "leaf_blocks", "buckets", "meta_blocks"}
    assert keys <= set(t.data.stats())


# ---------- issue #105 (revisão do P6 dos PRs #99 a #102) ----------

def test_reorganize_updates_the_stats(engine):
    # #105, item 1: Table.reorganize não rodava o analyze; depois de apagar metade e reorganizar,
    # stats().n_blocks continuava com o tamanho antigo.
    e = engine()
    e.catalog.create_table(S, SPEC)
    e.catalog.load_table("t", rows(3000))
    t = e.catalog.table("t")
    before = t.stats().n_blocks
    for p, r in list(t.data.scan()):
        if r[0] % 2:
            t.delete(p)
    t.reorganize()
    ds = t.data.stats()
    assert t.stats().n_records == 1500
    assert t.stats().n_blocks == ds["blocks"] - ds["meta_blocks"] < before
