"""Testes do índice hash secundário (P2, #52). Decisão: D-P2-18 (um HashFile com páginas INDEX cujos registros
são (chave, bloco, slot)); herda D-P2-14 a D-P2-17 do HashFile.

N1/N2: o índice sobre um heap real (fixture `bp`). N2 pelo catálogo: Table.insert/delete mantêm o índice.
N3: o plano do P6 com os índices hash. N4: contagens com cache frio, por área do buffer.
"""
import pytest

from cbd.contracts import (DuplicateKey, Field, FType, IndexKind, IndexSpec, NotApplicable, Org, OrgSpec,
                           PageType, RID, Schema)
from cbd.files.hash_index import HashIndex
from cbd.files.heap import HeapFile

S = Schema("t", (Field("k", FType.INT, unique=True), Field("v", FType.VARCHAR, 3000)))


def ispec(**params):
    return IndexSpec("t", "k", IndexKind.HASH, True, params)


def cold(bp):
    bp.clear()
    bp.fm.reset_stats()
    bp.reset_stats()


def heap_with_index(bp, n, size=100, **params):
    h = HeapFile.create(bp, "t", S, OrgSpec(Org.HEAP))
    h.bulk_load([(i, "x" * size) for i in range(n)])
    idx = HashIndex.create(bp, "t__k", ispec(**params), S.get("k"), h)
    idx.bulk_load(sorted((r[0], p) for p, r in h.scan()))
    return h, idx


# ---------- critérios de aceite da #52 ----------

def test_hash_index(bp):
    # Critério: heap + índice em k; busca com cache frio = 1 bloco do índice (área INDEX) + 1 do heap (DATA).
    # 5 000 entradas de 12 B (+4 de slot): 255 por bloco, N = ceil(5000 / (255 · 0,8)) = 25, sem overflow.
    h, idx = heap_with_index(bp, 5000)
    assert idx.stats()["buckets"] == 25 and idx.stats()["overflow_blocks"] == 0
    cold(bp)
    ptrs = list(idx.search(1234))
    assert len(ptrs) == 1 and h.fetch(ptrs[0]) == (1234, "x" * 100)
    assert bp.fm.io.reads == 2
    assert bp.stats_by_area[PageType.INDEX].misses == 1 and bp.stats_by_area[PageType.DATA].misses == 1


def test_index_overflow_chain_is_followed(bp):
    # Critério: a busca segue a cadeia do índice. N = 1 e 1 000 entradas (255 por bloco): 4 blocos.
    h, idx = heap_with_index(bp, 1000, size=10, buckets=1)
    assert idx.stats()["overflow_blocks"] == 3
    for k in (0, 300, 999):                              # no primário e no último bloco de overflow
        assert [h.fetch(p)[0] for p in idx.search(k)] == [k]
    cold(bp)
    assert list(idx.search(5000)) == []                  # ausente: lê a cadeia inteira
    assert bp.fm.io.reads == 4


def test_table_keeps_the_index_coherent(tmp_path):
    # Critério: remoção e atualização (delete + insert) pelo Table mantêm o índice; chave repetida desfaz o heap.
    from cbd.contracts import BufferConfig, StorageConfig
    from cbd.engine import Engine
    e = Engine(StorageConfig(root_dir=str(tmp_path)), BufferConfig())
    e.catalog.create_table(S, OrgSpec(Org.HEAP))
    e.catalog.load_table("t", [(i, f"v{i}") for i in range(300)])
    idx = e.catalog.create_index(ispec())
    assert isinstance(idx, HashIndex)
    t = e.catalog.table("t")
    p = next(idx.search(7))
    t.delete(p)                                          # remoção
    assert list(idx.search(7)) == []
    p2 = t.insert((7, "novo"))                           # "atualização" = remove + insere
    assert [t.data.fetch(q) for q in idx.search(7)] == [(7, "novo")]
    with pytest.raises(DuplicateKey):
        t.insert((7, "outro"))
    assert sum(1 for _, r in t.data.scan() if r[0] == 7) == 1      # o heap foi desfeito
    assert list(idx.search(7)) == [p2]
    e.close()
    e = Engine(StorageConfig(root_dir=str(tmp_path)), BufferConfig())   # reabre pelo catálogo
    t = e.catalog.table("t")
    assert [t.data.fetch(q) for q in t.indexes["k"].search(7)] == [(7, "novo")]
    assert all(len(list(t.indexes["k"].search(i))) == 1 for i in range(300))
    assert e.bp.pinned() == 0
    e.close()


# ---------- operações ----------

def test_delete_needs_the_exact_pair(bp):
    h, idx = heap_with_index(bp, 50, buckets=3)
    p = next(idx.search(10))
    with pytest.raises(KeyError):
        idx.delete(10, RID(p[0], p[1] + 1))              # chave certa, ponteiro errado
    with pytest.raises(KeyError):
        idx.delete(11, p)                                 # ponteiro certo, chave errada
    idx.delete(10, p)
    assert list(idx.search(10)) == [] and idx.stats()["records"] == 49


def test_insert_and_duplicate_key(bp):
    h, idx = heap_with_index(bp, 10, buckets=2)
    p = h.insert((10, "a"))
    idx.insert(10, p)
    assert list(idx.search(10)) == [p]
    with pytest.raises(DuplicateKey):
        idx.insert(10, RID(p[0], p[1]))


def test_search_returns_the_rid_type(bp):
    h, idx = heap_with_index(bp, 10, buckets=2)
    p = next(idx.search(3))
    assert type(p) is RID and h.fetch(p)[0] == 3


def test_range_is_not_applicable(bp):
    h, idx = heap_with_index(bp, 10, buckets=2)
    with pytest.raises(NotApplicable):
        idx.range(1, 5)


def test_index_pages_are_index_pages(bp):
    # D-P2-18: o índice fica na área INDEX do buffer, separado do heap (DATA). Um scan do heap não o expulsa.
    h, idx = heap_with_index(bp, 500, buckets=4)
    cold(bp)
    list(idx.search(1))
    list(h.scan())
    assert bp.stats_by_area[PageType.INDEX].misses == 1
    list(idx.search(1))
    assert bp.stats_by_area[PageType.INDEX].hits == 1


def test_close_and_open_keep_the_entries(bp):
    h, idx = heap_with_index(bp, 200, buckets=5, seed=4)
    before = idx.stats()
    idx.close()
    idx2 = HashIndex.open(bp, "t__k", ispec(buckets=5, seed=4), S.get("k"), h)
    assert idx2.stats() == before
    assert all([h.fetch(p)[0] for p in idx2.search(i)] == [i] for i in range(200))


def test_stats_keys(bp):
    h, idx = heap_with_index(bp, 100, buckets=2)
    st = idx.stats()
    assert {"blocks", "records", "overflow_blocks", "levels", "leaf_blocks", "buckets", "meta_blocks"} <= set(st)
    assert st["records"] == 100 and st["buckets"] == 2


def test_index_on_an_empty_table_then_inserts(bp):
    # Índice criado com a tabela vazia e enchido registro a registro (load_table com bulk=False): sem
    # params, o N sai das entradas que já existem (nenhuma: N = 1). Funciona, com cadeia longa (LIMITAÇÃO).
    h = HeapFile.create(bp, "t", S, OrgSpec(Org.HEAP))
    idx = HashIndex.create(bp, "t__k", ispec(), S.get("k"), h)
    idx.bulk_load([])
    for i in range(300):
        idx.insert(i, h.insert((i, "x" * 10)))
    assert idx.stats()["buckets"] == 1
    assert all(len(list(idx.search(i))) == 1 for i in range(300))


def test_text_key(bp):
    # Chave CHAR (ex.: curso.codigo seria INT; um índice sobre texto usa o hash_value, D-P2-14).
    s = Schema("u", (Field("c", FType.CHAR, 11, unique=True), Field("v", FType.INT)))
    h = HeapFile.create(bp, "u", s, OrgSpec(Org.HEAP))
    ptrs = {f"{i:011d}": h.insert((f"{i:011d}", i)) for i in range(100)}
    idx = HashIndex.create(bp, "u__c", IndexSpec("u", "c", IndexKind.HASH, True, {"buckets": 3}), s.get("c"), h)
    idx.bulk_load(sorted(ptrs.items()))
    assert all(list(idx.search(c)) == [p] for c, p in ptrs.items())


# ---------- plano do P6 (N3) ----------

def test_load_physical_heap_variants_with_hash_indexes(tmp_path):
    # As variantes heap do plano com os índices hash dos campos UNIQUE (os B+ ficam de fora até o P3).
    from types import SimpleNamespace
    from bench.build import load_physical, physical_plan
    from bench.generator import Dataset
    from bench.schema import ACCESS_COLUMNS, SCHEMAS
    from cbd.contracts import BufferConfig, StorageConfig
    from cbd.engine import Engine
    ds = Dataset(scale=0.002)
    stats = {t: {"n": ds.counts[t], "estimated_bfr": 10.0,
                 "distinct": {c: len({r[SCHEMAS[t].idx(c)] for r in ds.records(t)}) for c in cols}}
             for t, cols in ACCESS_COLUMNS.items()}
    plan = [dict(item, indexes=[i for i in item["indexes"] if i["kind"] == IndexKind.HASH])
            for item in physical_plan(stats) if item["org"]["org"] == Org.HEAP]
    assert sum(len(p["indexes"]) for p in plan) == 2           # aluno__heap.dre e curso__heap.codigo
    e = Engine(StorageConfig(root_dir=str(tmp_path)), BufferConfig())
    load_physical(e, SimpleNamespace(records=ds.records), plan)
    e.close()
    e = Engine(StorageConfig(root_dir=str(tmp_path)), BufferConfig())
    for p in plan:
        t = e.catalog.table(p["name"])
        for i in p["indexes"]:
            k = SCHEMAS[p["source"]].idx(i["column"])
            for ptr, r in t.data.scan():
                assert list(t.indexes[i["column"]].search(r[k])) == [ptr]
    e.close()
