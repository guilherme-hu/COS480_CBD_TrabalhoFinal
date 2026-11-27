"""Estresse da P2 (#55; guia P1 §6, linha "Heap/Hash (P2)"): operações aleatórias contra um oráculo `dict`.

Cada rodada mistura insert, delete, fetch, search e erros esperados num buffer minúsculo (a área DATA com poucos
frames força despejo de páginas sujas) e, a cada `check_every` operações, compara o arquivo inteiro com o oráculo:
- scan = oráculo (mesmos registros, mesmos Ptrs) e stats()["records"] = tamanho do oráculo;
- hash: cada registro está no bucket `bucket_of(k)` e a união dos `scan_bucket(i)` é o scan;
- `bp.pinned() == 0` depois de toda operação, inclusive das que levantam e dos iteradores abandonados no meio.
No meio da rodada o arquivo é fechado e reaberto (os Ptrs e o stats() continuam iguais), depois reorganizado (o
conteúdo tem de ser o mesmo; só os Ptrs mudam), e no fim um FileManager e um BufferPool novos leem só o disco.
O registro tem um `id` único (a identidade no oráculo), a chave `k` (UNIQUE: k = id, às vezes a chave de um
registro apagado; NOT UNIQUE: 200 valores com skew de Zipf), um VARCHAR de 0 até o maior que cabe no bloco e um
FLOAT nulo às vezes. `n_ops` conta inserts + deletes (o "100 mil inserts/deletes" do guia P1 §6).
A mesma função roda as versões da IA em `ai/P2/raw/` para o ingênuo × refatorado (`ai/P2/stress.md`).
"""
import importlib.util
import os
import random
import time
from collections import Counter, defaultdict
from pathlib import Path

import pytest

from cbd.contracts import (RID, BufferConfig, DuplicateKey, Field, FType, NotApplicable, Org, OrgSpec, PageType,
                           Policy, RecordTooLarge, Schema, StorageConfig)
from cbd.files.hash_file import HashFile
from cbd.files.heap import HeapFile
from cbd.records.codec import RecordCodec
from cbd.records.slotted_page import SlottedPage
from cbd.storage.buffer_pool import BufferPool
from cbd.storage.file_manager import FileManager

S = Schema("s", (Field("id", FType.INT), Field("k", FType.INT), Field("v", FType.VARCHAR, 8200),
                 Field("f", FType.FLOAT, nullable=True)))
SF = Schema("s", (Field("id", FType.INT), Field("k", FType.FLOAT), Field("v", FType.VARCHAR, 8200),
                  Field("f", FType.FLOAT, nullable=True)))       # chave FLOAT: o ramo hash_value do bucket_of
N_OPS = int(os.environ.get("P2_STRESS_OPS", "100000"))
FAILED = (Exception, pytest.fail.Exception)                    # o Failed do pytest.raises é BaseException


def pool(root, block_size=4096, data=3, policy=Policy.LRU, prefetch=0, toss=False):
    areas = {PageType.CATALOG: 2, PageType.INDEX: 3, PageType.DATA: data, PageType.WORK: 3}
    return BufferPool(FileManager(StorageConfig(root_dir=str(root), block_size=block_size)),
                      BufferConfig(areas=areas, policy=policy, prefetch=prefetch, toss_immediate=toss))


def close_pool(p):
    p.flush_all()
    p.fm.close_all()


def cold(p):
    p.clear()
    p.fm.reset_stats()
    p.reset_stats()


class Oracle:
    """id -> (Ptr, registro); a verdade contra a qual o arquivo é comparado.

    Sorteio, remoção e busca por chave em O(1) (lista de ids + posição, e chave -> ids), para que o tempo do
    estresse seja o da P2 e não o do oráculo.
    """

    def __init__(self):
        self.reset({})

    def reset(self, live):
        self.live, self._ids, self._pos, self._keys = {}, [], {}, defaultdict(set)
        for i in sorted(live):                    # ordem fixa: as duas versões sorteiam os mesmos ids depois
            self.add(i, *live[i])

    def add(self, i, ptr, r):
        self.live[i] = (ptr, r)
        self._pos[i] = len(self._ids)
        self._ids.append(i)
        self._keys[r[1]].add(i)

    def pop(self, i):
        ptr, r = self.live.pop(i)
        j, last = self._pos.pop(i), self._ids.pop()
        if last != i:
            self._ids[j], self._pos[last] = last, j
        self._keys[r[1]].discard(i)
        return ptr, r

    def pick(self, rng):
        return self._ids[rng.randrange(len(self._ids))]

    def by_key(self, k):
        return set(self._keys.get(k, ()))

    def records(self):
        return {i: r for i, (_, r) in self.live.items()}


def check_file(df, oracle, p, hashed, contract=True):
    """Compara o arquivo inteiro com o oráculo. Levanta AssertionError com o primeiro desvio.

    contract=False pula só as chaves de stats() da arquitetura §6.3 (a versão ingênua usa outros nomes);
    as checagens funcionais (scan, buckets, pins) valem sempre.
    """
    got = {}
    for ptr, r in df.scan():
        assert r[0] not in got, f"id {r[0]} aparece duas vezes no scan"
        got[r[0]] = (ptr, r)
    assert p.pinned() == 0, "scan deixou página fixada"
    missing, extra = oracle.live.keys() - got.keys(), got.keys() - oracle.live.keys()
    assert not missing and not extra, f"faltam {sorted(missing)[:5]}, sobram {sorted(extra)[:5]}"
    for i, (ptr, r) in oracle.live.items():
        assert got[i] == (ptr, r), f"id {i}: arquivo {got[i]} × oráculo {(ptr, r)}"
    if contract:
        assert df.stats()["records"] == len(oracle.live)
    if hashed:
        seen = []
        for b in range(df.n_buckets):
            for ptr, r in df.scan_bucket(b):
                assert df.bucket_of(r[1]) == b, f"id {r[0]} (k={r[1]}) no bucket {b}, devia estar no {df.bucket_of(r[1])}"
                seen.append(r[0])
        assert Counter(seen) == Counter(got.keys()), "scan_bucket não cobre o arquivo exatamente uma vez"
        if contract:
            st = df.stats()
            assert st["blocks"] == 1 + st["buckets"] + st["overflow_blocks"]
    assert p.pinned() == 0


def check_search_cost(df, oracle, p, rng, n=5):
    """N4 no hash UNIQUE, cache frio: a busca lê os blocos da cadeia até o que tem a chave e para (D-P2-17)."""
    for _ in range(n):
        ptr, r = oracle.live[oracle.pick(rng)]
        blocks, bno = [], 2 + df.bucket_of(r[1])              # a cadeia inteira, inclusive blocos que esvaziaram
        while bno:                                            # (lida pelo buffer, nunca pelo FileManager)
            blocks.append(bno)
            if bno == ptr.block:
                break
            with p.page(df.fid, bno, df.ptype) as page:
                bno = SlottedPage(page.data).next
        cold(p)
        assert [x for _, x in df.search(r[1])] == [r]
        assert p.fm.io.reads == len(blocks), (r[1], p.fm.io.reads, len(blocks))


def make_record(rng, i, k, vmax):
    roll = rng.random()
    n = 0 if roll < .1 else rng.randint(1, 60) if roll < .6 else rng.randint(61, 600) if roll < .9 \
        else rng.randint(vmax - 50, vmax)                    # 10% perto do maior registro que cabe no bloco
    f = None if rng.random() < .2 else round(rng.uniform(-1e6, 1e6), 3)
    return (i, k, "v" * n, f)


def run_stress(cls, root, *, org, unique=True, n_ops=N_OPS, seed=55, block_size=4096, data=3,
               policy=Policy.LRU, prefetch=0, toss=False, n_keys=200, check_every=None, buckets=64,
               hseed=480, float_key=False, bulk=0, contract=True):
    """Faz n_ops inserts + deletes aleatórios (e as leituras e erros no meio) em `cls` contra o oráculo.

    cls: HeapFile, HashFile ou uma versão da IA. hseed: seed do hash. float_key: chave FLOAT (ramo hash_value).
    bulk > 0: começa com bulk_load de `bulk` registros e N calculado por expected_records e load (D-P2-16),
    como a bancada carrega; senão params["buckets"] fixo.
    Devolve um dict com o resultado (falha, operações feitas, I/O) para a tabela ingênuo × refatorado.
    """
    rng, t0 = random.Random(seed), time.perf_counter()
    check_every = check_every or max(500, n_ops // 10)   # ~13 checkpoints por rodada
    p = pool(root, block_size, data, policy, prefetch, toss)
    hashed = org is Org.HASH
    schema = SF if float_key else S
    key = (lambda j: j * .25) if float_key else (lambda j: j)   # j · 0,25 é exato em binário
    params = {}
    if hashed:
        params = {"seed": hseed, **({"expected_records": bulk, "load": .8} if bulk else {"buckets": buckets})}
    spec = OrgSpec(org, "k" if hashed else None, unique if hashed else False, params)
    codec = RecordCodec(schema)
    vmax = SlottedPage.max_record(block_size) - len(codec.encode((0, 0, "", 0.0)))
    oracle, dead_keys, next_id, muts, op = Oracle(), [], 0, 0, 0
    weights = [1 / (j + 1) for j in range(n_keys)]            # Zipf: a chave 0 recebe 1/H(200) ≈ 17%
    phase = {"reopen": n_ops // 3, "reorganize": 2 * n_ops // 3}
    df = None
    try:
        df = cls.create(p, "s", schema, spec)
        if bulk:
            recs = [make_record(rng, i, key(i if unique else rng.choices(range(n_keys), weights)[0]), vmax)
                    for i in range(bulk)]
            df.bulk_load(recs)
            next_id = bulk
            oracle.reset({r[0]: (ptr, r) for ptr, r in df.scan()})
            assert oracle.records() == {r[0]: r for r in recs}
            check_file(df, oracle, p, hashed, contract)
        while muts < n_ops:
            op += 1
            if muts >= phase.get("reopen", n_ops + 1):        # fechar e reabrir: Ptrs e stats() iguais
                del phase["reopen"]
                st = df.stats()
                df.close()
                p.clear()
                df = cls.open(p, "s", schema, spec)
                if contract:
                    assert df.stats() == st, "stats() mudou ao reabrir (a meta não guardou tudo)"
                check_file(df, oracle, p, hashed, contract)
            if muts >= phase.get("reorganize", n_ops + 1):    # reorganizar: o conteúdo fica, os Ptrs mudam
                del phase["reorganize"]
                want = oracle.records()
                df.reorganize()
                after = {r[0]: (ptr, r) for ptr, r in df.scan()}
                assert {i: r for i, (_, r) in after.items()} == want, "reorganize perdeu ou mudou registros"
                oracle.reset(after)
                check_file(df, oracle, p, hashed, contract)
            roll = rng.random()
            if roll < .45 or not oracle.live:
                if not unique:
                    k = key(rng.choices(range(n_keys), weights)[0])
                elif dead_keys and rng.random() < .05:       # UNIQUE: chave apagada volta (não é duplicata)
                    k = dead_keys.pop(rng.randrange(len(dead_keys)))
                else:
                    k = key(next_id)
                r = make_record(rng, next_id, k, vmax)
                oracle.add(next_id, df.insert(r), r)
                next_id += 1
                muts += 1
            elif roll < .75:
                ptr, r = oracle.pop(oracle.pick(rng))
                df.delete(ptr)
                if unique and len(dead_keys) < 1000:
                    dead_keys.append(r[1])
                muts += 1
            elif roll < .83:
                ptr, r = oracle.live[oracle.pick(rng)]
                assert df.fetch(ptr) == r, f"fetch de {ptr} errado"
            elif roll < .93:
                if not hashed:
                    with pytest.raises(NotApplicable) as ei:
                        next(iter(df.search(0)))
                    assert p.pinned() == 0                    # com ei vivo: o gerador ainda existe
                else:
                    j = rng.randrange(next_id + 2) if unique else rng.randrange(n_keys + 2)   # às vezes inexistente
                    found = {r[0]: (ptr, r) for ptr, r in df.search(key(j))}
                    want = oracle.by_key(key(j))
                    assert set(found) == want, f"search({key(j)}): {sorted(found)[:5]} × {sorted(want)[:5]}"
                    assert all(found[i] == oracle.live[i] for i in want)
            elif roll < .96:                                  # iterador abandonado no meio: solta tudo no close
                ptr, r = oracle.live[oracle.pick(rng)]
                its = [df.scan()]
                if hashed:
                    its += [df.search(r[1]), df.scan_bucket(df.bucket_of(r[1]))]
                for it in its:
                    assert next(it, None) is not None
                    assert p.pinned() >= 1 or not contract   # a ingênua solta antes do yield
                    it.close()
                    assert p.pinned() == 0, "iterador fechado no meio deixou página fixada"
            else:                                             # erros esperados: nada muda e nenhum pin sobra
                kind = rng.randrange(3)
                if kind == 0 and hashed and unique:
                    _, r = oracle.live[oracle.pick(rng)]
                    with pytest.raises(DuplicateKey) as ei:
                        df.insert((next_id, r[1], "dup", None))
                elif kind == 1 and contract:
                    ptr, _ = oracle.live[oracle.pick(rng)]
                    bad = rng.choice([RID(10 ** 6, 0), RID(ptr[0], 60_000)])
                    with pytest.raises(KeyError) as ei:
                        (df.fetch if rng.random() < .5 else df.delete)(bad)
                else:
                    with pytest.raises(RecordTooLarge) as ei:
                        df.insert((next_id, key(next_id), "v" * (vmax + 1), None))
                assert p.pinned() == 0                        # com ei vivo: um gerador preso seguraria a página
                next_id += 1
            assert p.pinned() == 0, f"pin vazou na operação {op}"
            if op % check_every == 0:
                check_file(df, oracle, p, hashed, contract)
                if contract and hashed and unique:
                    check_search_cost(df, oracle, p, rng)
        check_file(df, oracle, p, hashed, contract)
        assert p.stats.dirty_evictions > 0, "o buffer não despejou página suja: o estresse não testou o dirty"
        if contract:                                          # N4: o scan lê só os blocos de dados (nunca o mapa)
            cold(p)
            assert sum(1 for _ in df.scan()) == len(oracle.live)
            st = df.stats()
            assert p.fm.io.reads == st["blocks"] - st["meta_blocks"], (p.fm.io.reads, st)
        df.close()
        df = None
        close_pool(p)
        # "queda" depois do flush: só o disco, por outro FileManager e outro BufferPool
        q = pool(root, block_size, data, policy, prefetch, toss)
        df2 = cls.open(q, "s", schema, spec)
        check_file(df2, oracle, q, hashed, contract)
        df2.close()
        close_pool(q)
        falha = None
    except FAILED as e:                                       # para a tabela ingênuo × refatorado
        falha = f"{type(e).__name__}: {e}"[:300]
        p.fm.close_all()
    return {"falha": falha, "ops": muts, "registros": len(oracle.live), "tempo_s": round(time.perf_counter() - t0, 1),
            "leituras": p.fm.io.reads, "gravacoes": p.fm.io.writes}


CASES = [
    pytest.param(HeapFile, Org.HEAP, True, {}, id="heap"),
    pytest.param(HashFile, Org.HASH, True, {}, id="hash-unique"),
    pytest.param(HashFile, Org.HASH, False, {}, id="hash-not-unique-zipf"),
    pytest.param(HashFile, Org.HASH, True, {"float_key": True, "bulk": 3000}, id="hash-unique-float-bulk"),
    pytest.param(HashFile, Org.HASH, False, {"float_key": True, "bulk": 3000}, id="hash-not-unique-float-bulk"),
]


@pytest.mark.stress
@pytest.mark.parametrize("cls, org, unique, extra", CASES)
def test_p2_stress_100k_ops_match_dict(tmp_path, cls, org, unique, extra):
    # 100 mil inserts + deletes com DATA = 3 frames (o mínimo que o hash usa: cadeia + página nova + meta).
    # Os dois casos FLOAT começam de um bulk_load com N por expected_records e load (D-P2-16), como a bancada.
    res = run_stress(cls, tmp_path, org=org, unique=unique, **extra)
    assert res["falha"] is None, res


class SmallMapHeap(HeapFile):
    _FSM_PER_PAGE = 16                 # 1 página do mapa a cada 16 blocos: várias páginas de mapa e várias faixas


@pytest.mark.stress
def test_p2_stress_heap_with_many_map_pages(tmp_path):
    # Mapa com várias páginas entre as faixas de dados (o que um heap com mais de B blocos tem) e prefetch 8:
    # o scan pelo cursor não pode ler as páginas do mapa (fronteira da D-P2-19).
    res = run_stress(SmallMapHeap, tmp_path, org=Org.HEAP, n_ops=20_000, check_every=2_000, prefetch=8, data=12)
    assert res["falha"] is None, res


@pytest.mark.stress
@pytest.mark.parametrize("block_size", [2048, 4096, 8192])          # os 3 B da bancada (D-P1-01, D-P6-02)
@pytest.mark.parametrize("toss", [False, True], ids=["sem-toss", "toss"])     # D-P1-09
@pytest.mark.parametrize("policy", [Policy.LRU, Policy.MRU])     # D-P1-08: só LRU e MRU (CLOCK e FIFO: ValueError)
@pytest.mark.parametrize("cls, org, unique, extra", CASES[:3])
def test_p2_stress_every_policy_and_block_size(tmp_path, cls, org, unique, extra, policy, toss, block_size):
    # A P2 sobre as políticas do P1, com e sem toss-immediate, nos 3 tamanhos de bloco e com prefetch ligado
    # (D-P1-08, D-P1-09, D-P1-12, D-P2-19).
    res = run_stress(cls, tmp_path, org=org, unique=unique, n_ops=6_000, check_every=2_000,
                     block_size=block_size, policy=policy, prefetch=4, toss=toss, data=12, seed=block_size)
    assert res["falha"] is None, res


@pytest.mark.stress
def test_p2_stress_total_skew_one_key(tmp_path):
    # Skew total (guia P1 §6): todo registro com a mesma chave, numa cadeia de overflow só.
    res = run_stress(HashFile, tmp_path, org=Org.HASH, unique=False, n_ops=20_000, n_keys=1, buckets=8)
    assert res["falha"] is None, res


RAW = Path(__file__).resolve().parents[2] / "ai" / "P2" / "raw"
NAIVE = RAW / "hash_file_naive_v1.py"


def load_raw(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.HashFile


@pytest.mark.stress
@pytest.mark.skipif(not NAIVE.exists(), reason="versão ingênua da IA ainda não gerada")
def test_naive_hash_versus_refactored(tmp_path, capsys):
    # Ingênuo × refatorado (guia P1 §6, enunciado §7.2.3.d): o mesmo estresse nas três versões do hash
    # (a ingênua da #55, a 1ª versão bruta da #48 e a atual). Só a atual tem de passar; o resultado das outras
    # vai para ai/P2/stress.md (rodar com -s para ver). As chaves de stats() (arquitetura §6.3) e o KeyError em
    # RID inválido ficam de fora (contract=False), para comparar só o comportamento; o contrato é visto à parte.
    versions = (("ingênuo", load_raw(NAIVE)), ("raw v1 (#48)", load_raw(RAW / "hash_file_v1.py")),
                ("refatorado", HashFile))
    for unique in (True, False):
        for name, cls in versions:
            res = run_stress(cls, tmp_path / f"{name}-{unique}", org=Org.HASH, unique=unique, n_ops=20_000,
                             check_every=1_000, contract=False)
            print(f"\n{name} unique={unique}: {res}")
            if cls is HashFile:
                assert res["falha"] is None, res
    for name, cls in versions:
        p = pool(tmp_path / f"contrato-{name}")
        h = cls.create(p, "s", S, OrgSpec(Org.HASH, "k", True, {"buckets": 4, "seed": 7}))
        print(f"\n{name}: chaves de stats() que faltam:",
              sorted({"blocks", "records", "overflow_blocks", "buckets", "meta_blocks"} - h.stats().keys()),
              "| bucket_of(10) =", h.bucket_of(10), "(contrato §5.2: (10 + 7) % 4 =", (10 + 7) % 4, ")")
        h.close()
        close_pool(p)
    # Blocos com cache frio (o que a bancada mede): 3 000 registros de 114 B em 64 buckets (UNIQUE). Com ~31
    # registros por bloco, cada bucket tem ~47 registros: primário + 1 bloco de overflow.
    costs = {}
    for name, cls in versions:
        p = pool(tmp_path / f"custo-{name}", data=128)
        spec = OrgSpec(Org.HASH, "k", True, {"buckets": 64})
        h = cls.create(p, "s", S, spec)
        for i in range(3000):
            h.insert((i, i, "v" * 100, None))
        h.close()
        out = {}
        for op, run in (("open", lambda: cls.open(p, "s", S, spec)), ("search", lambda: list(h.search(1234))),
                        ("insert", lambda: h.insert((5000, 5000, "v" * 100, None)))):
            cold(p)
            res = run()
            if op == "open":
                h = res
            p.flush_all()
            out[op] = (p.fm.io.reads, p.fm.io.writes)
        costs[name] = out
        print(f"\nblocos (leituras, gravações) com cache frio, {name}: {out}")
        h.close()
        close_pool(p)
    # Atual: abrir lê só a meta; a busca de 1234 acha no primário (1 bloco); o insert UNIQUE de 5000 lê a meta e a
    # cadeia inteira do bucket (primário + overflow) para checar duplicata e grava o bloco e a meta (D-P2-17).
    assert costs["refatorado"] == {"open": (1, 0), "search": (1, 0), "insert": (3, 2)}


# ---------------------------------------------------------------- Table + Catalog + HashIndex (#55)
T = Schema("t", (Field("id", FType.INT, unique=True), Field("k", FType.INT), Field("e", FType.INT, nullable=True),
                 Field("v", FType.VARCHAR, 300), Field("tags", FType.LIST, 10, nullable=True)))


def run_table_stress(root, org, n_ops, seed=255, block_size=2048, reopen_every=None):
    """Table.insert/delete aleatórios pelo Engine, contra o oráculo.

    HEAP: índices hash UNIQUE em `id` e em `e` (com NULL, que não entra no índice), as combinações da arquitetura
    §5.3. HASH primário em `k` (NOT UNIQUE, skew): sem índice secundário hash (§5.3 só prevê B+ sobre hash).
    O insert com `e` repetido tem de falhar com DuplicateKey e desfazer o que já entrou (Table.insert).
    A cada `check` cada índice é conferido nos dois sentidos (cada chave acha os Ptrs certos; o índice não tem
    entrada a mais); o Engine é fechado e reaberto e a tabela reorganizada no meio (conteúdo igual antes e depois).
    """
    from cbd.contracts import IndexKind, IndexSpec
    from cbd.engine import Engine
    rng = random.Random(seed)
    areas = {PageType.CATALOG: 2, PageType.INDEX: 4, PageType.DATA: 4, PageType.WORK: 3}

    def open_engine():
        return Engine(StorageConfig(str(root), block_size), BufferConfig(areas=areas, prefetch=2))
    e = open_engine()
    heap = org is Org.HEAP
    spec = OrgSpec(Org.HEAP) if heap else OrgSpec(Org.HASH, "k", False, {"buckets": 16, "seed": 480})
    e.catalog.create_table(T, spec)
    cols = (("id", 0), ("e", 2)) if heap else ()
    for col, _ in cols:
        e.catalog.create_index(IndexSpec("t", col, IndexKind.HASH, True, {"buckets": 32}))
    t = e.catalog.table("t")
    oracle, used_e, next_id = Oracle(), {}, 0

    def check():
        got = {r[0]: (ptr, r) for ptr, r in t.data.scan()}
        assert got == oracle.live
        for col, pos in cols:
            want = defaultdict(set)
            for ptr, r in got.values():
                if r[pos] is not None:
                    want[r[pos]].add(ptr)
            n = 0
            for key, ptrs in want.items():
                found = list(t.indexes[col].search(key))
                assert len(found) == len(set(found)) and set(found) == ptrs, (col, key)
                n += len(found)
            assert t.indexes[col].stats()["records"] == n, f"índice {col} com entradas a mais"
        if not heap:
            for k in range(9):
                assert {r[0] for _, r in t.data.search(k)} == oracle.by_key(k)
        st = e.catalog.analyze("t")
        assert st.n_records == len(oracle.live)
        assert st.distinct["k"] == len({r[1] for _, r in got.values()})
        assert e.bp.pinned() == 0

    try:
        for op in range(n_ops):
            if op == n_ops // 2:
                want = oracle.records()
                t.reorganize()                                # Ptrs mudam; índices refeitos pelo catálogo
                after = {r[0]: (ptr, r) for ptr, r in t.data.scan()}
                assert {i: r for i, (_, r) in after.items()} == want, "reorganize perdeu ou mudou registros"
                oracle.reset(after)
                check()
            if (reopen_every and op % reopen_every == reopen_every - 1) or op == n_ops // 3:
                check()
                e.close()
                e = open_engine()
                t = e.catalog.table("t")
                check()
            roll = rng.random()
            if roll < .5 or not oracle.live:
                ev = rng.randrange(10 * n_ops) if rng.random() < .7 else None
                if heap and ev is not None and rng.random() < .1 and used_e:
                    ev = rng.choice(list(used_e))             # repetido: o índice UNIQUE recusa
                r = (next_id, rng.choices(range(8), [8, 4, 2, 1, 1, 1, 1, 1])[0], ev,
                     "v" * rng.randrange(300), None if rng.random() < .3 else ("a",) * rng.randrange(4))
                if heap and ev is not None and ev in used_e:
                    with pytest.raises(DuplicateKey) as ei:
                        t.insert(r)
                    assert e.bp.pinned() == 0                 # com ei vivo
                else:
                    oracle.add(next_id, t.insert(r), r)
                    if ev is not None:
                        used_e[ev] = next_id
                next_id += 1
            else:
                ptr, r = oracle.pop(oracle.pick(rng))
                t.delete(ptr)
                if r[2] is not None:
                    used_e.pop(r[2], None)
            assert e.bp.pinned() == 0
            if op % 500 == 499:
                check()
        check()
    finally:
        e.close()
    e = open_engine()                                         # "queda" depois do close: só o disco
    t = e.catalog.table("t")
    try:
        check()
    finally:
        e.close()


@pytest.mark.stress
@pytest.mark.parametrize("org", [Org.HEAP, Org.HASH], ids=["heap", "hash"])
def test_table_with_hash_indexes_random_ops_match_oracle(tmp_path, org):
    run_table_stress(tmp_path, org, n_ops=int(os.environ.get("P2_TABLE_OPS", "8000")), reopen_every=2_000)
