"""Testes do hash estático primário (P2, #48). Decisões: D-P2-14 a D-P2-17.

N1/N2: o HashFile sobre o buffer pool real (fixture `bp`). N3: a base do gerador.
N4: contagens de blocos com cache frio. N5: operações aleatórias contra um dict.
Registros de teste: k INT (4 B) + v VARCHAR (2 B de tamanho + texto). Com v de 1 000 B o registro tem
1 006 B, mais 4 B de slot: 4 por bloco (4 · 1 010 = 4 040 ≤ 4 096 − 10).
"""
import math
import random
from collections import defaultdict

import pytest

from cbd.contracts import (DuplicateKey, Field, FType, NotApplicable, Org, OrgSpec, PageType, RID,
                           RecordTooLarge, Schema)
from cbd.files.hash_file import HashFile
from cbd.records.hashing import hash_value
from cbd.records.slotted_page import SlottedPage

S = Schema("t", (Field("k", FType.INT), Field("v", FType.VARCHAR, 3000)))
STEXT = Schema("u", (Field("k", FType.VARCHAR, 20), Field("v", FType.VARCHAR, 100)))


def spec(unique=True, **params):
    return OrgSpec(Org.HASH, "k", unique, params)


def rec(i, n=1000):
    return (i, "x" * n)


def cold(bp):
    # O mesmo que Engine.cold(): grava os sujos, esvazia o buffer e zera os contadores.
    bp.clear()
    bp.fm.reset_stats()
    bp.reset_stats()


def keys_of(rows):
    return sorted(r[0] for _, r in rows)


def read_temp_after_crash(root, name, schema):
    # "Queda": outro FileManager e outro BufferPool leem só o que está no disco (pelo buffer, nunca fm.read).
    from cbd.contracts import BufferConfig, StorageConfig
    from cbd.records.codec import RecordCodec
    from cbd.storage.buffer_pool import BufferPool
    from cbd.storage.file_manager import FileManager
    fm = FileManager(StorageConfig(root_dir=str(root)))
    pool = BufferPool(fm, BufferConfig())
    fid, codec, out = fm.open(name), RecordCodec(schema), []
    for bno in range(1, fm.n_blocks(fid) + 1):
        with pool.page(fid, bno, PageType.WORK) as p:
            sp = SlottedPage(p.data)
            out += [codec.decode(sp.get(s)) for s in range(sp.n_slots())]
    pool.close_file(fid)
    fm.close_all()
    return sorted(out)


def chain_bnos(h, i):
    # Blocos do bucket i na ordem da cadeia, tirados dos Ptrs do scan_bucket (sem repetir).
    out = []
    for ptr, _ in h.scan_bucket(i):
        if not out or out[-1] != ptr[0]:
            out.append(ptr[0])
    return out


# ---------- critérios de aceite da #48 ----------

def test_hash_unique(bp):
    # N = 1 e 20 registros de 1 006 B: 5 blocos (o primário e 4 de overflow).
    h = HashFile.create(bp, "h", S, spec(buckets=1))
    ptrs = {i: h.insert(rec(i)) for i in range(20)}
    assert h.stats()["overflow_blocks"] == 4
    for i in range(20):                                   # acha também quem está no overflow
        assert [r for _, r in h.search(i)] == [rec(i)]
    with pytest.raises(DuplicateKey):
        h.insert(rec(19))                                 # a 19 está no último bloco de overflow
    h.delete(ptrs[17])                                    # remoção no overflow
    assert list(h.search(17)) == []
    assert h.stats()["records"] == 19
    h.insert(rec(17))                                     # depois de apagada, a chave volta a ser aceita
    assert [r for _, r in h.search(17)] == [rec(17)]


def test_hash_overflow_chain(bp):
    # Critério: N = 1 com 10 blocos de registros. 40 registros de 4 por bloco: 1 primário + 9 de overflow.
    h = HashFile.create(bp, "h", S, spec(buckets=1))
    for i in range(40):
        h.insert(rec(i))
    st = h.stats()
    assert (st["buckets"], st["overflow_blocks"], st["max_chain"]) == (1, 9, 9)
    assert st["blocks"] == 1 + 10                          # meta + 10 blocos do bucket
    assert all([r for _, r in h.search(i)] == [rec(i)] for i in range(40))
    assert chain_bnos(h, 0) == list(range(2, 12))          # cadeia: bloco 2, depois 3, 4, ... na ordem


def test_hash_overflow_chain_not_unique(bp):
    # NOT UNIQUE: a busca percorre a cadeia toda e devolve todos os iguais.
    h = HashFile.create(bp, "h", S, spec(unique=False, buckets=1))
    for i in range(40):
        h.insert(rec(i % 3))
    assert keys_of(h.search(0)) == [0] * 14
    assert keys_of(h.search(2)) == [2] * 13


def test_hash_search_cost(bp):
    # Critério: sem overflow e cache frio, search(k) = 1 leitura. 64 buckets, 640 registros de 106 B:
    # 10 por bucket, 1 100 B, cabem no bloco. Vale para UNIQUE, NOT UNIQUE e chave ausente.
    for unique in (True, False):
        h = HashFile.create(bp, f"h{unique}", S, spec(unique=unique, buckets=64))
        h.bulk_load([rec(i, 100) for i in range(640)])
        assert h.stats()["overflow_blocks"] == 0
        for k in (5, 600, 9999):
            cold(bp)
            assert len(list(h.search(k))) == (k < 640)
            assert (bp.fm.io.reads, bp.fm.io.writes) == (1, 0)


def test_unique_search_stops_at_first_match(bp):
    # UNIQUE: a busca para no 1º achado. A chave 0 está no primário de uma cadeia de 10 blocos.
    h = HashFile.create(bp, "h", S, spec(buckets=1))
    for i in range(40):
        h.insert(rec(i))
    cold(bp)
    assert keys_of(h.search(0)) == [0]
    assert bp.fm.io.reads == 1
    cold(bp)
    assert list(h.search(999)) == []                       # ausente: lê a cadeia inteira
    assert bp.fm.io.reads == 10


def test_explicit_buckets_and_seed(bp):
    # Critério: dois arquivos com o mesmo N e a mesma seed particionam igual (bucket-join do P5, D-P6-01).
    a = HashFile.create(bp, "a", S, spec(unique=False, buckets=7, seed=3))
    b = HashFile.create(bp, "b", S, spec(unique=False, buckets=7, seed=3))
    c = HashFile.create(bp, "c", S, spec(unique=False, buckets=7, seed=4))
    a.bulk_load([rec(i, 10) for i in range(0, 100)])
    b.bulk_load([rec(i % 50, 10) for i in range(0, 300)])
    assert (a.n_buckets, a.seed) == (b.n_buckets, b.seed) == (7, 3)
    for i in range(7):
        ka, kb = {r[0] for _, r in a.scan_bucket(i)}, {r[0] for _, r in b.scan_bucket(i)}
        assert all(a.bucket_of(k) == i for k in ka) and all(b.bucket_of(k) == i for k in kb)
        assert kb <= ka                                    # chave comum cai no mesmo bucket nos dois
    assert any(a.bucket_of(k) != c.bucket_of(k) for k in range(100))


def test_bucket_of_by_type(bp):
    # D-P2-14: INT usa (k + seed) mod N; os outros tipos, hash_value(k, seed) mod N.
    h = HashFile.create(bp, "h", S, spec(buckets=7, seed=3))
    assert [h.bucket_of(k) for k in (0, 4, 5, 11)] == [3, 0, 1, 0]
    t = HashFile.create(bp, "t", STEXT, spec(buckets=7, seed=3))
    assert t.bucket_of("abc") == hash_value("abc", 3) % 7


def test_bucket_follows_the_column_type(bp):
    # Defeito da versão bruta: o ramo do mod seguia o tipo do valor. Como 5 == 5.0 no Python, a busca com o
    # outro tipo numérico tem de achar o registro: o bucket depende do tipo da coluna, não do valor.
    f = HashFile.create(bp, "f", Schema("f", (Field("k", FType.FLOAT), Field("v", FType.VARCHAR, 10))),
                        spec(buckets=7, seed=3))
    f.insert((5.0, "a"))
    assert [r for _, r in f.search(5)] == [(5.0, "a")]
    i = HashFile.create(bp, "i", S, spec(buckets=7, seed=3))
    i.insert(rec(5, 10))
    assert [r for _, r in i.search(5.0)] == [rec(5, 10)]
    assert i.bucket_of(5.0) == i.bucket_of(5) == (5 + 3) % 7


def test_bucket_i_is_block_2_plus_i(bp):
    # D-P2-15: o bucket i é o bloco 2 + i (o 0 é do FileManager, o 1 é o meta).
    h = HashFile.create(bp, "h", S, spec(buckets=64))
    assert h.insert(rec(5, 10)) == RID(2 + 5, 0)
    assert h.insert(rec(64 + 3, 10)) == RID(2 + 3, 0)


# ---------- N e fator de carga (D-P2-16) ----------

def test_n_from_expected_records(bp):
    # Registros de 106 B: bfr_est = max_record(4096) // (106 + 4) = 4082 // 110 = 37.
    # N = ceil(1000 / (37 · 0,8)) = ceil(33,78) = 34; com carga 0,5: ceil(1000 / 18,5) = 55.
    assert SlottedPage.max_record(4096) // 110 == 37
    recs = [rec(i, 100) for i in range(1000)]
    h = HashFile.create(bp, "h", S, spec(expected_records=1000))
    h.bulk_load(recs)
    assert h.n_buckets == 34 == math.ceil(1000 / (37 * 0.8))
    assert h.stats()["load_x1000"] == 1000 * 1000 // (34 * 37)
    h5 = HashFile.create(bp, "h5", S, spec(expected_records=1000, load=0.5))
    h5.bulk_load(recs)
    assert h5.n_buckets == 55


def test_n_is_limited_by_distinct_and_buckets_win(bp):
    # NOT UNIQUE: N ≤ V(A) (params["distinct"]). E params["buckets"] passa na frente da conta.
    recs = [rec(i % 5, 100) for i in range(1000)]
    h = HashFile.create(bp, "h", S, spec(unique=False, expected_records=1000, distinct=5))
    h.bulk_load(recs)
    assert h.n_buckets == 5
    g = HashFile.create(bp, "g", S, spec(unique=False, buckets=9, expected_records=1000, distinct=5))
    g.bulk_load(recs)
    assert g.n_buckets == 9


def test_n_without_expected_records_uses_the_load(bp):
    # bulk_load sem expected_records: n = registros carregados. 1000 registros de 106 B: N = 34.
    h = HashFile.create(bp, "h", S, spec())
    h.bulk_load([rec(i, 100) for i in range(1000)])
    assert h.n_buckets == 34


# ---------- overflow, carga em massa e remoção (D-P2-17) ----------

def test_bulk_load_chains_are_contiguous(bp):
    # C3: o bulk_load ordena por bucket, e a cadeia de cada bucket fica contígua no disco.
    # N = 3, skew: chave 0 com 60 registros (15 blocos), chave 1 com 20 (5), chave 2 com 4 (1).
    recs = [rec(0)] * 60 + [rec(1)] * 20 + [rec(2)] * 4
    random.Random(1).shuffle(recs)
    h = HashFile.create(bp, "h", S, spec(unique=False, buckets=3))
    h.bulk_load(recs)
    c0, c1, c2 = (chain_bnos(h, i) for i in range(3))
    assert c0 == [2] + list(range(5, 19))                 # primário 2, overflow 5..18 em sequência
    assert c1 == [3] + list(range(19, 23))
    assert c2 == [4]
    cold(bp)
    assert len(list(h.search(0))) == 60
    assert (bp.fm.io.reads, bp.fm.io.seeks) == (15, 2)   # 1 seek no primário, 1 no início da cadeia


def test_insert_goes_to_first_block_with_space(bp):
    # I1 + R1: apagar deixa um buraco; o insert seguinte da mesma cadeia o reaproveita.
    h = HashFile.create(bp, "h", S, spec(buckets=1))
    ptrs = [h.insert(rec(i)) for i in range(12)]          # 3 blocos: 2, 3, 4
    assert [p[0] for p in ptrs] == [2] * 4 + [3] * 4 + [4] * 4
    h.delete(ptrs[1])                                     # buraco no primário
    h.delete(ptrs[9])                                     # buraco no 3º bloco
    assert h.insert(rec(100))[0] == 2
    assert h.insert(rec(101))[0] == 4
    assert h.stats()["blocks"] == 1 + 3                    # nada de bloco novo
    assert h.insert(rec(102))[0] == 5                      # cadeia cheia: bloco novo no fim


def test_delete_keeps_an_empty_overflow_block_in_the_chain(bp):
    # R1: o bloco de overflow que esvazia continua na cadeia (só o reorganize o tira).
    h = HashFile.create(bp, "h", S, spec(buckets=1))
    ptrs = [h.insert(rec(i)) for i in range(8)]
    for p in ptrs[4:]:
        h.delete(p)
    assert h.stats()["overflow_blocks"] == 1
    assert chain_bnos(h, 0) == [2]                         # o bloco 3 está vazio, sem registro para listar
    assert keys_of(h.search(5)) == []


def test_reorganize_recomputes_n(bp):
    # L1: reorganize() por comando refaz o hash com o N do n atual (mesma carga e seed).
    # Planejado para 200 registros de 106 B: N = ceil(200 / 29,6) = 7. Com 1 000: N = 34, sem overflow.
    h = HashFile.create(bp, "h", S, spec(expected_records=200, seed=5))
    for i in range(1000):
        h.insert(rec(i, 100))
    assert h.n_buckets == 7 and h.stats()["overflow_blocks"] > 0
    h.reorganize()
    assert (h.n_buckets, h.seed) == (34, 5)
    assert h.stats()["overflow_blocks"] == 0
    assert keys_of(h.scan()) == list(range(1000))
    assert all(keys_of(h.search(i)) == [i] for i in range(0, 1000, 37))


def test_reorganize_keeps_explicit_buckets(bp):
    # Com buckets explícito (pares do bucket-join), o N não pode mudar: o par deixaria de casar.
    h = HashFile.create(bp, "h", S, spec(buckets=3, seed=2))
    ptrs = [h.insert(rec(i)) for i in range(30)]
    for p in ptrs[::2]:
        h.delete(p)
    h.reorganize()
    assert (h.n_buckets, h.seed) == (3, 2)
    assert keys_of(h.scan()) == list(range(1, 30, 2))


# ---------- custos com cache frio (N4) ----------

def test_scan_cost_is_every_block_once(bp):
    # scan em ordem física: primários e depois as cadeias, contíguos. Cache frio: b leituras, 1 seek.
    h = HashFile.create(bp, "h", S, spec(buckets=64))
    h.bulk_load([rec(i, 200) for i in range(3000)])
    st = h.stats()
    b = st["blocks"] - st["meta_blocks"]
    assert st["overflow_blocks"] > 0 and b > bp.capacity(PageType.DATA)
    cold(bp)
    assert keys_of(h.scan()) == list(range(3000))
    assert (bp.fm.io.reads, bp.fm.io.seeks, bp.fm.io.writes) == (b, 1, 0)


def test_scan_bucket_cost(bp):
    # scan_bucket(i) lê só os blocos do bucket i (primário + cadeia).
    h = HashFile.create(bp, "h", S, spec(unique=False, buckets=4))
    h.bulk_load([rec(i % 4) for i in range(40)])          # 10 por bucket: 3 blocos cada
    cold(bp)
    assert keys_of(h.scan_bucket(2)) == [2] * 10
    assert bp.fm.io.reads == 3


def test_fetch_cost_is_one_block(bp):
    h = HashFile.create(bp, "h", S, spec(buckets=1))
    ptrs = [h.insert(rec(i)) for i in range(40)]
    cold(bp)
    assert h.fetch(ptrs[33]) == rec(33)
    assert bp.fm.io.reads == 1


# ---------- básico ----------

def test_range_is_not_applicable(bp):
    h = HashFile.create(bp, "h", S, spec(buckets=2))
    with pytest.raises(NotApplicable):
        h.range(1, 5)


def test_record_too_large_touches_nothing(bp):
    # 4 + 2 + 4 500 = 4 506 B > max_record(4096) = 4 082 B (D-P2-06): recusado antes de tocar em página.
    big = Schema("b", (Field("k", FType.INT), Field("v", FType.VARCHAR, 5000)))
    for params in (dict(buckets=2), dict(expected_records=10)):  # buckets já alocados ou ainda não
        h = HashFile.create(bp, f"b{len(params)}{list(params)[0]}", big, spec(**params))
        before = h.stats()
        with pytest.raises(RecordTooLarge):
            h.insert(rec(1, 4500))
        assert h.stats() == before


def test_stats_keys(bp):
    h = HashFile.create(bp, "h", S, spec(buckets=1))
    for i in range(40):
        h.insert(rec(i))
    st = h.stats()
    assert {k: st[k] for k in ("blocks", "records", "overflow_blocks", "levels", "leaf_blocks", "buckets",
                               "meta_blocks", "max_chain")} == dict(
        blocks=11, records=40, overflow_blocks=9, levels=1, leaf_blocks=0, buckets=1, meta_blocks=1, max_chain=9)
    assert all(type(v) is int for v in st.values())


def test_ptr_is_six_bytes_and_bad_ptrs_raise(bp):
    h = HashFile.create(bp, "h", S, spec(buckets=2))
    p = h.insert(rec(1, 10))
    assert h.ptr_size == 6 and h.decode_ptr(h.encode_ptr(p)) == p
    for bad in (RID(0, 0), RID(1, 0), RID(99, 0), RID(p[0], 5), ("2", 0)):
        with pytest.raises(KeyError):
            h.fetch(bad)
        with pytest.raises(KeyError):
            h.delete(bad)


def test_close_and_open_keep_everything(bp):
    h = HashFile.create(bp, "h", S, spec(unique=False, buckets=5, seed=9))
    h.bulk_load([rec(i % 7) for i in range(50)])
    before = h.stats()
    h.close()
    h2 = HashFile.open(bp, "h", S, spec(unique=False, buckets=5, seed=9))
    assert (h2.n_buckets, h2.seed, h2.stats()) == (5, 9, before)
    assert keys_of(h2.search(3)) == [3] * 7
    h2.insert(rec(3))
    assert keys_of(h2.search(3)) == [3] * 8


def test_open_rejects_a_file_that_is_not_a_hash(bp):
    from cbd.files.heap import HeapFile
    HeapFile.create(bp, "x", S, OrgSpec(Org.HEAP)).close()
    with pytest.raises(ValueError):
        HashFile.open(bp, "x", S, spec(buckets=2))


def test_bulk_load_needs_an_empty_file(bp):
    h = HashFile.create(bp, "h", S, spec(buckets=2))
    h.insert(rec(1))
    with pytest.raises(ValueError):
        h.bulk_load([rec(2)])


def test_bulk_load_rejects_duplicate_keys_in_unique(bp):
    h = HashFile.create(bp, "h", S, spec(buckets=2))
    with pytest.raises(DuplicateKey):
        h.bulk_load([rec(1), rec(2), rec(1)])


# ---------- base real (N3) ----------

@pytest.mark.parametrize("table,column", [("curso", "codigo"), ("disciplina", "curso"),
                                          ("aluno", "dre"), ("aluno", "curso"), ("aluno", "cr")])
def test_generated_base_in_hash(bp, table, column):
    from bench.generator import Dataset, SEEDS
    from bench.schema import SCHEMAS
    sch = SCHEMAS[table]
    recs = list(Dataset(scale=0.01).records(table))
    i = sch.idx(column)
    unique = sch.get(column).unique
    params = dict(expected_records=len(recs), seed=SEEDS[table])
    if not unique:
        params["distinct"] = len({r[i] for r in recs})
    h = HashFile.create(bp, f"{table}_{column}", sch, OrgSpec(Org.HASH, column, unique, params))
    h.bulk_load(recs)
    assert sorted(r for _, r in h.scan()) == sorted(tuple(r) for r in recs)
    oracle = defaultdict(list)
    for r in recs:
        oracle[r[i]].append(tuple(r))
    for k, rows in oracle.items():
        assert sorted(r for _, r in h.search(k)) == sorted(rows)


def test_aluno_dre_at_scale_1_has_no_overflow(bp):
    # D-P2-16: aluno por dre, escala 1, carga 0,8: N = 2 084 (bfr_est 30) e nenhum bloco de overflow.
    from bench.generator import Dataset, SEEDS
    from bench.schema import SCHEMAS
    recs = Dataset().records("aluno")
    h = HashFile.create(bp, "a", SCHEMAS["aluno"],
                        OrgSpec(Org.HASH, "dre", True, dict(expected_records=50000, seed=SEEDS["aluno"])))
    h.bulk_load(recs)
    st = h.stats()
    assert (h.n_buckets, st["overflow_blocks"], st["blocks"]) == (2084, 0, 1 + 2084)


@pytest.mark.stress
def test_inscricao_dre_at_scale_1(bp):
    # D-P2-16: inscricao por dre, escala 1 (500 000 registros de 24 B, 145 por bloco), carga 0,8:
    # N = ceil(500 000 / (145 · 0,8)) = 4 311 e 39 blocos de overflow (simulação da D-P2-16). Leva ~10 s;
    # marcado stress como os do P1 (roda na suíte inteira; `-m "not stress"` o pula).
    from bench.generator import Dataset, SEEDS
    from bench.schema import SCHEMAS
    h = HashFile.create(bp, "i", SCHEMAS["inscricao"], OrgSpec(
        Org.HASH, "dre", False, dict(expected_records=500000, distinct=49997, seed=SEEDS["inscricao"])))
    h.bulk_load(Dataset().records("inscricao"))
    assert (h.n_buckets, h.stats()["overflow_blocks"]) == (4311, 39)


# ---------- estresse pequeno (N5) ----------

@pytest.mark.parametrize("seed", range(3))
def test_random_ops_match_dict_oracle(bp, seed):
    # Inserções, remoções e buscas ao acaso num N pequeno (cadeias longas), contra um dict.
    rng = random.Random(seed)
    h = HashFile.create(bp, "h", S, spec(unique=False, buckets=3, seed=seed))
    live = {}                                              # ptr -> registro
    for _ in range(1500):
        op = rng.random()
        if op < 0.55 or not live:
            r = rec(rng.randrange(40), rng.randrange(1, 600))
            live[h.insert(r)] = r
        elif op < 0.8:
            p = rng.choice(list(live))
            h.delete(p)
            del live[p]
        else:
            k = rng.randrange(40)
            assert sorted(r for _, r in h.search(k)) == sorted(r for r in live.values() if r[0] == k)
    assert sorted(h.scan()) == sorted(live.items())
    assert h.stats()["records"] == len(live)


# ---------- testes da revisão independente da #48 (26/11): defeitos e mutantes sobreviventes ----------

def test_scan_bucket_does_not_prefetch_other_buckets(tmp_path):
    # Defeito: scan_bucket pinava com seq=True. O bloco seguinte ao primário 2 + i é o primário do bucket
    # i + 1, então o prefetch (P1, D-P1-12) lia buckets vizinhos. N = 64, 640 registros: 1 bloco por bucket.
    from cbd.contracts import BufferConfig, StorageConfig
    from cbd.storage.buffer_pool import BufferPool
    from cbd.storage.file_manager import FileManager
    fm = FileManager(StorageConfig(root_dir=str(tmp_path)))
    pool = BufferPool(fm, BufferConfig(prefetch=8))
    h = HashFile.create(pool, "h", S, spec(buckets=64))
    h.bulk_load([rec(i, 100) for i in range(640)])
    pool.allow_prefetch(h.fid, PageType.DATA)
    cold(pool)
    assert keys_of(h.scan_bucket(3)) == list(range(3, 640, 64))
    assert pool.fm.io.reads == 1
    pool.flush_all()
    fm.close_all()


def test_every_change_survives_cold(bp):
    # Mutantes sem mark_dirty (insert no slot, insert que liga o overflow, delete): a mudança some do disco.
    h = HashFile.create(bp, "h", S, spec(buckets=1))
    ptrs = [h.insert(rec(i)) for i in range(4)]            # primário cheio
    cold(bp)
    h.insert(rec(4))                                       # liga o 1º overflow ao primário, que estava limpo
    cold(bp)
    assert keys_of(h.search(4)) == [4]
    h.delete(ptrs[1])
    cold(bp)
    assert keys_of(h.search(1)) == []
    assert h.insert(rec(9))[0] == 2                        # o buraco do primário
    cold(bp)
    assert keys_of(h.search(9)) == [9]


def test_meta_is_the_source_after_reopen(bp):
    # A meta (não o spec) guarda N e seed; e ela é gravada depois de um insert com cache frio.
    h = HashFile.create(bp, "h", S, spec(unique=False, buckets=5, seed=9))
    h.bulk_load([rec(i % 7) for i in range(50)])
    cold(bp)
    h.insert(rec(3))
    before = h.stats()
    h.close()
    cold(bp)
    h2 = HashFile.open(bp, "h", S, spec(unique=False))    # spec sem buckets nem seed
    assert (h2.n_buckets, h2.seed, h2.stats()) == (5, 9, before)


def test_open_rejects_another_key_or_uniqueness(bp):
    # A meta guarda a coluna da chave e se é UNIQUE: abrir com outro spec mandaria buscas ao bucket errado.
    S2 = Schema("t", (Field("k", FType.INT), Field("j", FType.INT)))
    HashFile.create(bp, "h", S2, OrgSpec(Org.HASH, "k", True, {"buckets": 3})).close()
    for bad in (OrgSpec(Org.HASH, "j", True), OrgSpec(Org.HASH, "k", False)):
        with pytest.raises(ValueError):
            HashFile.open(bp, "h", S2, bad)
    assert HashFile.open(bp, "h", S2, OrgSpec(Org.HASH, "k", True)).n_buckets == 3


def test_open_and_close_without_change_writes_nothing(bp):
    h = HashFile.create(bp, "h", S, spec(buckets=2))
    h.insert(rec(1))
    h.close()
    cold(bp)
    HashFile.open(bp, "h", S, spec(buckets=2)).close()
    assert bp.fm.io.writes == 0


def test_bulk_load_stats(bp):
    # Cadeias de 14 e 4 blocos de overflow (60 e 20 registros de 4 por bloco): max_chain 14, média 18/3.
    h = HashFile.create(bp, "h", S, spec(unique=False, buckets=3))
    h.bulk_load([rec(0)] * 60 + [rec(1)] * 20 + [rec(2)] * 4)
    st = h.stats()
    assert (st["overflow_blocks"], st["max_chain"], st["records"], st["avg_chain_x1000"]) == (18, 14, 84, 6000)


def test_n_uses_expected_records_not_the_loaded_count(bp):
    # expected_records = 2 000 com 1 000 carregados: N = ceil(2000 / (37 · 0,8)) = 68.
    h = HashFile.create(bp, "h", S, spec(expected_records=2000))
    h.bulk_load([rec(i, 100) for i in range(1000)])
    assert h.n_buckets == math.ceil(2000 / (37 * 0.8)) == 68


def test_bfr_uses_the_mean_of_the_first_100(bp):
    # 1º registro de 1 006 B e o resto de 106 B: média dos 100 primeiros = 115 B, bfr = 4082 // 119 = 34,
    # N = ceil(1000 / 27,2) = 37. Só com o 1º registro daria bfr 4 e N 313.
    recs = [rec(0, 1000)] + [rec(i, 100) for i in range(1, 1000)]
    h = HashFile.create(bp, "h", S, spec(expected_records=1000))
    h.bulk_load(recs)
    assert h.n_buckets == math.ceil(1000 / ((4082 // math.ceil((1006 + 99 * 106) / 100 + 4)) * 0.8)) == 37


def test_insert_without_buckets_or_expected_records_is_refused(bp):
    # Sem buckets nem expected_records, o insert não tem como dimensionar N (a versão bruta fixava N = 1).
    h = HashFile.create(bp, "h", S, spec())
    with pytest.raises(ValueError):
        h.insert(rec(1))
    assert h.stats()["blocks"] == 1


def test_bucket_of_before_any_bucket_is_a_clear_error(bp):
    h = HashFile.create(bp, "h", S, spec(expected_records=10))
    with pytest.raises(ValueError):
        h.bucket_of(1)
    assert list(h.search(1)) == [] and list(h.scan()) == []


def test_search_with_a_key_of_another_type_finds_nothing(bp):
    # Coluna INT, chave de texto: a busca devolve vazio, sem quebrar (a versão corrigida fazia int("abc")).
    h = HashFile.create(bp, "h", S, spec(buckets=3))
    h.insert(rec(5, 10))
    assert list(h.search("abc")) == [] and list(h.search("5")) == []


def test_scan_bucket_out_of_range(bp):
    h = HashFile.create(bp, "h", S, spec(buckets=3))
    for i in (-1, 3):
        with pytest.raises(IndexError):
            list(h.scan_bucket(i))


def test_delete_keeps_the_empty_block_linked(bp):
    # R1: o bloco de overflow que esvazia continua ligado: a busca de uma chave ausente lê os 2 blocos.
    h = HashFile.create(bp, "h", S, spec(buckets=1))
    ptrs = [h.insert(rec(i)) for i in range(8)]
    for p in ptrs[4:]:
        h.delete(p)
    cold(bp)
    assert list(h.search(999)) == []
    assert bp.fm.io.reads == 2


def test_reorganize_load_and_temp(bp):
    # Depois do reorganize: carga ≈ 0,8 (1000 · 1000 // (34 · 37) = 794) e o temporário apagado.
    h = HashFile.create(bp, "h", S, spec(expected_records=200))
    for i in range(1000):
        h.insert(rec(i, 100))
    h.reorganize()
    assert h.stats()["load_x1000"] == 794
    assert not bp.fm.exists("h__reorg0")


def test_reorganize_of_an_empty_file_keeps_buckets(bp):
    # A versão bruta deixava N = 0 (bucket_of dividia por zero) quando o arquivo reorganizado estava vazio.
    h = HashFile.create(bp, "h", S, spec(expected_records=100))
    p = h.insert(rec(1))
    h.delete(p)
    h.reorganize()
    assert h.n_buckets > 0 and h.bucket_of(1) == 1 % h.n_buckets
    h.insert(rec(1))
    assert keys_of(h.search(1)) == [1]


@pytest.mark.parametrize("seed", range(3))
def test_random_ops_unique_with_cold_cache(bp, seed):
    # Oráculo UNIQUE, com cache esvaziado ao acaso: pega página alterada sem dirty e DuplicateKey errado.
    rng = random.Random(seed)
    h = HashFile.create(bp, "h", S, spec(buckets=2, seed=seed))
    live = {}                                              # chave -> (ptr, registro)
    for _ in range(800):
        k = rng.randrange(60)
        op = rng.random()
        if op < 0.5:
            r = rec(k, rng.randrange(1, 600))
            if k in live:
                with pytest.raises(DuplicateKey):
                    h.insert(r)
            else:
                live[k] = (h.insert(r), r)
        elif op < 0.75 and k in live:
            h.delete(live.pop(k)[0])
        else:
            assert [r for _, r in h.search(k)] == ([live[k][1]] if k in live else [])
        if rng.random() < 0.05:
            cold(bp)
    assert sorted(h.scan()) == sorted(live.values())


# ---------- /code-review e /ponytail-review do PR #102 (26/11) ----------

def test_failed_reorganize_keeps_the_temp_copy(bp, tmp_path, monkeypatch):
    # Defeito: uma falha depois de apagar o arquivo original apagava também o temporário, a única cópia que
    # restava (o hash ficava com 0 registros). Agora o temporário fica no disco e a exceção diz o nome dele.
    # 30 registros de 1 006 B, 4 por bloco: o temporário tem ceil(30 / 4) = 8 blocos WORK.
    h = HashFile.create(bp, "h", S, spec(buckets=3))
    h.bulk_load([rec(i) for i in range(30)])
    real = bp.new_page

    def flaky(fid, ptype=PageType.DATA):
        if ptype is PageType.DATA:                         # a recarga: meta e buckets do arquivo novo
            raise OSError("disco cheio")
        return real(fid, ptype)
    monkeypatch.setattr(bp, "new_page", flaky)
    with pytest.raises(OSError) as err:
        h.reorganize()
    monkeypatch.undo()
    assert bp.pinned() == 0
    assert any("h__reorg0" in note for note in err.value.__notes__)
    # Revisor de testes (26/11): n_blocks vem do contador em memória e não prova que o temporário foi gravado;
    # lido por outro FileManager, como depois de uma queda, ele tem de ter os 30 registros.
    assert read_temp_after_crash(tmp_path, "h__reorg0", S) == [rec(i) for i in range(30)]


def test_failure_during_the_reorganize_copy_cleans_up(bp, monkeypatch):
    # Se a falha vem antes de apagar o original (na cópia para o temporário), o original fica inteiro e o
    # temporário some.
    h = HashFile.create(bp, "h", S, spec(buckets=3))
    h.bulk_load([rec(i) for i in range(30)])
    real, calls = bp.new_page, []

    def flaky(fid, ptype=PageType.DATA):
        calls.append(1)
        if ptype is PageType.WORK and len(calls) > 3:
            raise OSError("disco cheio")
        return real(fid, ptype)
    monkeypatch.setattr(bp, "new_page", flaky)
    with pytest.raises(OSError):
        h.reorganize()
    monkeypatch.undo()
    assert bp.pinned() == 0 and not bp.fm.exists("h__reorg0")
    assert keys_of(h.scan()) == list(range(30))


def test_scan_bucket_rejects_a_float_index(bp):
    # Defeito: scan_bucket(1.0) era aceito e devolvia RID(block=3.0, ...), que fetch e encode_ptr recusam.
    h = HashFile.create(bp, "h", S, spec(buckets=4))
    h.bulk_load([rec(i, 10) for i in range(8)])
    with pytest.raises(IndexError):
        list(h.scan_bucket(1.0))


def test_failure_to_save_the_temp_keeps_the_original_error(bp, monkeypatch):
    # Revisor de testes (26/11): se gravar o temporário também falhar, a exceção que sai é a da recarga, com as
    # duas notas (onde estão os registros e que a gravação falhou).
    h = HashFile.create(bp, "h", S, spec(buckets=3))
    h.bulk_load([rec(i) for i in range(30)])
    real_new = bp.new_page

    def flaky(fid, ptype=PageType.DATA):
        if ptype is PageType.DATA:
            raise OSError("disco cheio")
        return real_new(fid, ptype)

    def bad_close(fid):
        raise OSError("de novo")
    monkeypatch.setattr(bp, "new_page", flaky)
    monkeypatch.setattr(bp, "close_file", bad_close)
    with pytest.raises(OSError, match="disco cheio") as err:
        h.reorganize()
    monkeypatch.undo()
    assert any("h__reorg0" in n for n in err.value.__notes__)
    assert any("também falhou" in n for n in err.value.__notes__)


def test_fetch_and_delete_at_the_slot_boundaries(bp):
    # Revisor de testes (26/11): os RIDs ruins dos outros testes ficam longe das bordas do _view. Com n_slots = 1,
    # os slots -1 e 1 são os vizinhos da faixa válida e precisam dar KeyError (contrato), não AssertionError.
    h = HashFile.create(bp, "h", S, spec(buckets=2))
    p = h.insert(rec(1, 10))
    for bad in (RID(p.block, -1), RID(p.block, 1)):
        with pytest.raises(KeyError):
            h.fetch(bad)
        with pytest.raises(KeyError):
            h.delete(bad)
    assert h.fetch(p) == rec(1, 10)
