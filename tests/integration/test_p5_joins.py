"""P5 sobre armazenamento e ordenação reais, com I/O frio e oráculo SQLite."""

from collections import Counter
from contextlib import closing
import json
import random
import sqlite3
from types import SimpleNamespace

import pytest

from ai.P5.raw.hash_join_ingenuo import hash_join_ingenuo
from bench.build import load_physical, prepare
from bench.generator import Dataset
from bench.sqlite_ref import SQLiteReference, output_signature
from bench.workload import JOINS
from cbd.contracts import (
    BufferConfig, Field, FType, JoinSide, NotApplicable, Org, OrgSpec, PageType,
    Path, Schema, StorageConfig,
)
from cbd.engine import Engine
from cbd.exec.hash_join import hash_join, hash_join_cost
from cbd.exec.sort_merge import sort_merge_join, sort_merge_join_cost
from cbd.records.hashing import hash_value


@pytest.fixture
def engine(tmp_path):
    opened = []

    def make(work):
        root = tmp_path / str(len(opened))
        cfg = BufferConfig()
        cfg.areas[PageType.WORK] = work
        eng = Engine(StorageConfig(str(root), 2048), cfg)
        opened.append((eng, root))
        return eng, root

    yield make
    for eng, root in opened:
        try:
            assert eng.bp.pinned() == 0
            assert not list(root.glob("__temp*"))
        finally:
            eng.close()


def load(eng, name, rows, unique, buckets=None, seed=17, length=1200):
    schema = Schema(name, (
        Field("k", FType.INT, nullable=not unique, unique=unique),
        Field("payload", FType.CHAR, length),
    ))
    spec = (OrgSpec(Org.HEAP) if buckets is None else
            OrgSpec(Org.HASH, "k", unique, {"buckets": buckets, "seed": seed}))
    table = eng.catalog.create_table(schema, spec)
    eng.catalog.load_table(name, rows)
    return JoinSide(table, "k", Path.SCAN if buckets is None else Path.PRIMARY)


def sqlite_join(rr, ss):
    with closing(sqlite3.connect(":memory:")) as db:
        db.executescript("CREATE TABLE r(k INT,p TEXT); CREATE TABLE s(k INT,p TEXT);")
        db.executemany("INSERT INTO r VALUES (?,?)", rr)
        db.executemany("INSERT INTO s VALUES (?,?)", ss)
        return Counter(db.execute("SELECT r.k,r.p,s.k,s.p FROM r JOIN s ON r.k=s.k"))


def test_hj_bucket_join(engine):
    eng, _ = engine(4)
    rr = [(i, f"r{i}") for i in range(11)]
    ss = [(i, f"s{i}") for i in range(11)]
    r, s = load(eng, "r", rr, True, 11), load(eng, "s", ss, False, 11)
    eng.cold()
    info = {}
    assert Counter(hash_join(eng, r, s, info=info)) == sqlite_join(rr, ss)
    assert info["variant"] == "bucket-join" and info["index_used"]
    # 11 cadeias de um bloco de cada lado, sem ler meta e sem temporários.
    measured = eng.snapshot()
    assert (measured["io.reads"], measured["io.writes"]) == (22, 0)
    cost = hash_join_cost(eng, r, s)
    assert (cost.reads, cost.writes) == (22, 0)


def test_hj_bucket_overflow_without_writes(engine):
    eng, root = engine(3)
    rr = [(i, f"r{i}") for i in range(12)]
    ss = [(1, f"s{i}") for i in range(15)]
    r, s = load(eng, "r", rr, True, 3), load(eng, "s", ss, False, 3)
    before = set(root.iterdir())
    eng.cold()
    info = {}
    assert Counter(hash_join(eng, r, s, build="s", info=info)) == sqlite_join(rr, ss)
    assert info["variant"] == "bucket-join" and info["skew_fallback"] > 0
    assert eng.snapshot()["io.writes"] == 0
    assert set(root.iterdir()) == before


@pytest.mark.parametrize("buckets,seed", [(11, 19), (13, 17)])
def test_incompatible_real_hash_indexes(engine, buckets, seed):
    eng, _ = engine(4)
    rr, ss = [(k, f"r{k}") for k in range(11)], [(k, f"s{k}") for k in range(11)]
    r, s = load(eng, "r", rr, True, 11), load(eng, "s", ss, False, buckets, seed)
    eng.cold()
    info = {}
    assert Counter(hash_join(eng, r, s, info=info)) == sqlite_join(rr, ss)
    assert info["variant"] == "grace"
    assert not info["index_used"]


@pytest.mark.parametrize("build", ["r", "s"])
def test_hj_prepartitioned(engine, monkeypatch, build):
    eng, root = engine(16)
    # N=19 não é divisível por n_h=2 ou 4. Usar h(k)%n_h em vez de
    # bucket_of(k)%n_h falharia quando k+seed cruza uma fronteira de N.
    rr = [(i, f"r{i}") for i in range(19)]
    ss = [(i % 19, f"s{i}") for i in range(38)] + [(None, "nulo")]
    r, s = load(eng, "r", rr, True, 19), load(eng, "s", ss, False)
    actual_temp = eng.catalog.temp
    schemas = []

    def trace_temp(schema):
        schemas.append(schema.name)
        return actual_temp(schema)

    monkeypatch.setattr(eng.catalog, "temp", trace_temp)
    before = set(root.iterdir())
    eng.cold()
    info = {}
    assert Counter(hash_join(eng, r, s, build=build, info=info)) == sqlite_join(rr, ss)
    assert info["variant"] == "prepartitioned" and info["index_used"]
    n = 2 if build == "r" else 4
    assert schemas == ["s"] * n and set(root.iterdir()) == before
    measured = eng.snapshot()
    cost = hash_join_cost(eng, r, s, build=build)
    # r: 19 leituras de buckets. s: 39 do heap e até 38 do temporário.
    # Cache WORK retém partições finais, e o NULL não ocupa bloco temporário.
    assert info["skew_fallback"] == 0
    assert 19 + 39 <= measured["io.reads"] <= 19 + 39 + 38
    assert cost.reads == 19 + 2 * 39 + n
    print("prepartitioned", build, "estimado", (cost.reads, cost.writes),
          "medido", (measured["io.reads"], measured["io.writes"]))


@pytest.mark.parametrize("build", ["r", "s"])
def test_hj_prepartitioned_hash_on_s(engine, monkeypatch, build):
    eng, _ = engine(5)
    rr = [(i, f"r{i}") for i in range(12)]
    ss = [(i % 12, f"s{i}") for i in range(36)] + [(None, "nulo")]
    r, s = load(eng, "r", rr, True), load(eng, "s", ss, False, 7)
    actual_temp = eng.catalog.temp
    schemas = []

    def trace_temp(schema):
        schemas.append(schema.name)
        return actual_temp(schema)

    monkeypatch.setattr(eng.catalog, "temp", trace_temp)
    eng.cold()
    info = {}
    assert Counter(hash_join(eng, r, s, build=build, info=info)) == sqlite_join(rr, ss)
    assert info["variant"] == "prepartitioned" and info["index_used"]
    assert schemas == ["r"] * (eng.M - 1)


@pytest.mark.parametrize("ordered_r", [True, False])
def test_smj_one_ordered_side_with_minimum_work(engine, ordered_r):
    eng, _ = engine(3)
    master = [(i, f"m{i}") for i in range(9)]
    detail = [(i % 9, f"d{i}") for i in reversed(range(36))]
    # Só a B+ é substituída. O adaptador, o sort, o heap e WORK são reais.
    schema = Schema("master", (Field("k", FType.INT, unique=True),
                               Field("payload", FType.CHAR, 1200)))
    data = SimpleNamespace(spec=OrgSpec(Org.BTREE, "k"), height=1,
                           range=lambda lo, hi: iter(enumerate(master)))
    ordered_side = JoinSide(SimpleNamespace(
        name="master", schema=schema, data=data, indexes={},
        stats=lambda: SimpleNamespace(n_blocks=9, n_records=9),
    ), "k", Path.PRIMARY)
    heap = load(eng, "detail", detail, False)
    r, s = (ordered_side, heap) if ordered_r else (heap, ordered_side)
    eng.cold()
    info = {}
    expected = sqlite_join(master, detail) if ordered_r else sqlite_join(detail, master)
    assert Counter(sort_merge_join(eng, r, s, info)) == expected
    assert info["variant"] == "sort-one" and info["mem_each"] == 3
    assert (info["sorted_r"], info["sorted_s"]) == (not ordered_r, ordered_r)
    assert info["runs"] > 0
    assert sort_merge_join_cost(eng, r, s).writes > 0


def test_abandon_real_temp_files(engine):
    eng, root = engine(4)
    rr = [(i, f"r{i}") for i in range(20)]
    ss = [(i % 20, f"s{i}") for i in range(60)]
    r, s = load(eng, "r", rr, True), load(eng, "s", ss, False)
    before = set(root.iterdir())
    for operator in (hash_join, sort_merge_join):
        if operator is sort_merge_join:
            # O sort do P4 exige três páginas por fluxo. Usar outro engine.
            eng, root = engine(6)
            r, s = load(eng, "r", rr, True), load(eng, "s", ss, False)
            before = set(root.iterdir())
        eng.cold()
        with closing(operator(eng, r, s)) as result:
            for _ in range(5):
                next(result)
            assert set(root.iterdir()) > before
        assert set(root.iterdir()) == before
        assert eng.bp.pinned() == 0


def test_hash_empty_catalog_stats_dont_hide_insert(engine):
    eng, _ = engine(4)
    r, s = load(eng, "r", [], True), load(eng, "s", [(7, "s")], False)
    r.table.insert((7, "r"))
    assert r.table.stats().n_records == 0  # metadados de analyze permanecem antigos
    assert list(hash_join(eng, r, s)) == [(7, "r", 7, "s")]
    assert hash_join_cost(eng, r, s).reads == 2


@pytest.mark.parametrize("master_r", [True, False])
def test_smj_vs_bruteforce(engine, master_r):
    eng, _ = engine(9)
    rr = [(i, f"r{i}") for i in range(14)]
    ss = [(i % 17, f"s{i}") for i in range(60)] + [(None, "nulo")]
    rng = random.Random(480)
    rng.shuffle(rr)
    rng.shuffle(ss)
    if not master_r:
        rr, ss = ss, rr
    r, s = load(eng, "r", rr, master_r), load(eng, "s", ss, not master_r)
    eng.cold()
    info = {}
    expected = Counter(a + b for a in rr for b in ss if a[0] is not None and a[0] == b[0])
    assert Counter(sort_merge_join(eng, r, s, info)) == expected == sqlite_join(rr, ss)
    assert info["sorted_r"] and info["sorted_s"] and info["mem_each"] == 4
    assert eng.bp.pinned() == 0


def test_smj_no_unique(engine):
    eng, _ = engine(6)
    r, s = (load(eng, name, [(1, name)], False) for name in ("r", "s"))
    with pytest.raises(NotApplicable, match="UNIQUE"):
        list(sort_merge_join(eng, r, s))
    with pytest.raises(NotApplicable, match="UNIQUE"):
        sort_merge_join_cost(eng, r, s)


def test_smj_work_minimum(engine):
    eng, _ = engine(3)
    r, s = load(eng, "r", [(1, "r")], True), load(eng, "s", [(1, "s")], False)
    with pytest.raises(NotApplicable, match="M >= 6"):
        list(sort_merge_join(eng, r, s))


def test_p5_tiny_base_signatures(engine, tmp_path):
    root = tmp_path / "prepared"
    prepare(root, Dataset(0.002), block_sizes=(2048,))
    physical = json.loads((root / "physical_plan_2048.json").read_text(encoding="utf8"))
    # P3 ainda não está em main. As variantes heap são carregadas pelo
    # catálogo real sem índices B+ e exercitam os três joins da bancada.
    plan = [{**item, "indexes": []} for item in physical if item["org"]["org"] == "HEAP"]
    eng, _ = engine(6)
    cardinalities = {"small_small": 2, "small_large": 100, "large_large": 1000}
    with SQLiteReference(root / "ref.sqlite") as ref:
        load_physical(eng, ref, plan)
        tables = {item["source"]: eng.catalog.table(item["name"]) for item in plan}
        for size, rt, rk, st, sk in JOINS:
            query = dict(kind="join", r_table=rt, r_column=rk, s_table=st, s_column=sk)
            want = ref.result(query)
            assert want[0] == cardinalities[size]
            r = JoinSide(tables[rt], rk, Path.SCAN)
            s = JoinSide(tables[st], sk, Path.SCAN)
            for op in (hash_join, sort_merge_join):
                eng.cold()
                assert output_signature(query, op(eng, r, s)) == want
                assert eng.bp.pinned() == 0


def test_costs(engine):
    # M=20 e 180 blocos no build dão 12 partições de 15 blocos (cabem em 18).
    # Uma linha CHAR(1200) por página de 2048 B. As chaves são balanceadas
    # deliberadamente para isolar a rodada prevista da recursão por skew.
    groups = [[] for _ in range(12)]
    key = 0
    while any(len(group) < 15 for group in groups):
        group = groups[hash_value(key, 100) % 12]
        if len(group) < 15:
            group.append(key)
        key += 1
    keys = sorted(key for group in groups for key in group)
    rr = [(key, f"r{i}") for i, key in enumerate(keys)]
    ss = [(key, f"s{i}") for i, key in enumerate(keys * 2)]
    expected = sqlite_join(rr, ss)
    for op, estimator in ((hash_join, hash_join_cost), (sort_merge_join, sort_merge_join_cost)):
        eng, _ = engine(20)
        r, s = load(eng, "r", rr, True), load(eng, "s", ss, False)
        estimate = estimator(eng, r, s)
        eng.cold()
        observed = Counter(op(eng, r, s))
        snapshot = eng.snapshot()
        measured = snapshot["io.reads"] + snapshot["io.writes"]
        assert observed == expected
        # GRACE: 3*(180+360)+4*12=1668. O modelo arredonda partições,
        # mas os registros já ocupam uma página cada; WORK retém páginas.
        # Sort: usa 10 páginas por lado e o custo de passadas do P4.
        # A junção pode encerrar antes da última página da entrada restante.
        assert snapshot["io.writes"] > 0
        assert measured == pytest.approx(estimate.transfers, rel=0.10), (op.__name__, snapshot, estimate)
        print(op.__name__, "estimado", (estimate.reads, estimate.writes),
              "medido", (snapshot["io.reads"], snapshot["io.writes"]))
        assert eng.bp.pinned() == 0


@pytest.mark.stress
def test_skew_50_times_work_against_sqlite(engine):
    rr = [(i, f"r{i}") for i in range(300)]
    ss = [(7, f"s{i}") for i in range(400)]
    rng = random.Random(480)
    rng.shuffle(rr)
    rng.shuffle(ss)
    expected = sqlite_join(rr, ss)
    eng, _ = engine(3)
    r, s = load(eng, "r", rr, True), load(eng, "s", ss, False)
    assert min(r.table.stats().n_blocks, s.table.stats().n_blocks) >= 50 * eng.M
    eng.cold()
    naive = {}
    assert Counter(hash_join_ingenuo(eng, s, r, naive)) == sqlite_join(ss, rr)
    assert naive["peak_build_records"] == 400
    naive_io = eng.snapshot()
    assert eng.bp.pinned() == 0
    eng.cold()
    fixed = {}
    assert Counter(hash_join(eng, r, s, build="s", info=fixed)) == expected
    assert fixed["skew_fallback"] > 0 and fixed["levels"] == 3
    assert eng.bp.pinned() == 0
    hash_io = eng.snapshot()
    assert hash_io["io.writes"] > naive_io["io.writes"]

    # #66 usa dois sorts concorrentes. Cada sort real exige pelo menos 3.
    sorted_eng, _ = engine(6)
    r, s = load(sorted_eng, "r", rr, True), load(sorted_eng, "s", ss, False)
    assert min(r.table.stats().n_blocks, s.table.stats().n_blocks) >= 50 * sorted_eng.M
    sorted_eng.cold()
    assert Counter(sort_merge_join(sorted_eng, r, s)) == expected
    assert sorted_eng.bp.pinned() == 0
    for label, counters in (("ingênuo", naive_io), ("GRACE limitado", hash_io),
                            ("sort-merge", sorted_eng.snapshot())):
        print(label, "leituras", counters["io.reads"], "gravações", counters["io.writes"])
