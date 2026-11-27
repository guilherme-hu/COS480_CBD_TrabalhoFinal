"""Integração e ponta a ponta da P2 (#55): P6 (base e referência) → P2 (catálogo, heap, hash, índice) → P1 (buffer).

N3 e N4 da AGENTS §7: uma base do gerador (`bench.build.prepare`, escala 0,01 com 50 cursos e 200 disciplinas,
para os joins terem vários buckets; o SQLite é a referência) carregada pelo adaptador do P6 (`load_physical`)
nas variantes HEAP e HASH do plano físico de cada tamanho de bloco. A B+ é do P3 e ainda não existe: as variantes
BTREE e os índices B+ ficam de fora.
- Conteúdo e estatísticas de cada variante = SQLite (antes e depois de reabrir o Engine).
- As consultas da carga de trabalho (`workload.json`) feitas só com a interface da P2 dão a assinatura que o SQLite
  calculou (`output_signature` × `expected`, D-P6-05): igualdade em aluno.dre pelo índice hash e pelo hash primário,
  faixa em aluno.cr por scan (o filtro é dublê do operador do P4) e os 3 joins.
- Contrato do P5 (arquitetura §5.2, guia P5 §6): os joins usam só `n_buckets`, `seed`, `bucket_of`, `scan_bucket` e
  `catalog.temp()`, como o bucket-join e a partição do hash join farão. O join em memória por bucket é dublê do P5.
- Contrato do P1: só o BufferPool chama FileManager.read/write (regra 1), cada arquivo usa uma área só
  (CATALOG, DATA, INDEX, WORK), nenhum pin sobra, e as contagens com cache frio batem com as fórmulas.
"""
import json
import math
import sys
from collections import defaultdict

import pytest

from bench.build import load_physical, prepare
from bench.generator import Dataset
from bench.matrix import areas_for_block
from bench.sqlite_ref import SQLiteReference, output_signature
from cbd.contracts import BufferConfig, FType, IndexKind, Org, PageType, StorageConfig
from cbd.engine import Engine
from cbd.records.hashing import hash_value
from cbd.storage.buffer_pool import BufferPool
from cbd.storage.file_manager import FileManager

SIZES = (2048, 4096, 8192)
N_H = 4                       # partições do dublê do P5: não divide os N do plano, então `% n_h` não é a identidade


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    out = tmp_path_factory.mktemp("base")
    prepare(out, Dataset(.01, (("curso", 50), ("disciplina", 200))), SIZES)
    return out


def p2_plan(base, block_size):
    """O plano do P6 sem o que é do P3: variantes BTREE e índices B+."""
    plan = json.loads((base / f"physical_plan_{block_size}.json").read_text())
    return [dict(item, indexes=[i for i in item["indexes"] if i["kind"] == IndexKind.HASH])
            for item in plan if item["org"]["org"] != Org.BTREE]


def engine(root, block_size, prefetch=0):
    return Engine(StorageConfig(str(root), block_size), BufferConfig(areas=areas_for_block(block_size),
                                                                       prefetch=prefetch))


@pytest.fixture(scope="module", params=SIZES, ids=lambda b: f"B{b}")
def loaded(request, base, tmp_path_factory):
    b = request.param
    root = tmp_path_factory.mktemp(f"db{b}")
    plan = p2_plan(base, b)
    with SQLiteReference(base / "ref.sqlite") as ref, engine(root, b) as e:
        load_physical(e, ref, plan)
        assert e.bp.pinned() == 0
    return root, b, plan


def workload(base):
    return json.loads((base / "workload.json").read_text())


def check_against_sqlite(e, ref, plan, b, drop=lambda name, r: False):
    """Cada variante = a tabela do SQLite (menos os registros que `drop(variante, registro)` diz que foram apagados)."""
    for item in plan:
        t, src = e.catalog.table(item["name"]), item["source"]
        want = sorted(tuple(r) for r in ref.records(src) if not drop(item["name"], r))
        assert sorted(r for _, r in t.data.scan()) == want, item["name"]
        st = t.stats()
        assert st.n_records == len(want)
        if not any(drop(item["name"], r) for r in ref.records(src)):
            for col, v in ref.stats(src, b)["distinct"].items():
                assert st.distinct[col] == v, (item["name"], col)
        assert set(t.indexes) == {i["column"] for i in item["indexes"]}
        if item["org"]["org"] == Org.HASH:                    # a seed do plano chegou ao arquivo e voltou do disco
            assert t.data.seed == item["org"]["params"]["seed"], item["name"]
        for col, idx in t.indexes.items():                       # cada registro achado pelo índice, mesmo Ptr
            k = t.schema.idx(col)
            for ptr, r in t.data.scan():
                if r[k] is not None:
                    assert ptr in set(idx.search(r[k])), (item["name"], col, r[k])
    assert e.bp.pinned() == 0


def bucket_rule(hf, key):
    """Arquitetura §5.2 (D-P2-14): coluna INT, (k + seed) % N; outros tipos, hash_value(k, seed) % N."""
    ftype = hf.schema.get(hf.spec.key).ftype
    return (key + hf.seed) % hf.n_buckets if ftype is FType.INT else hash_value(key, hf.seed) % hf.n_buckets


def test_e2e_every_variant_matches_sqlite_and_survives_reopen(base, loaded):
    root, b, plan = loaded
    with SQLiteReference(base / "ref.sqlite") as ref:
        with engine(root, b) as e:                                # reaberto: tudo veio do disco
            assert e.catalog.tables() == sorted(i["name"] for i in plan)
            check_against_sqlite(e, ref, plan, b)
            for item in plan:                                    # o bucket de cada registro segue o contrato
                hf = e.catalog.table(item["name"]).data
                if item["org"]["org"] == Org.HASH:
                    k = hf.schema.idx(hf.spec.key)
                    for i in range(hf.n_buckets):
                        assert all(bucket_rule(hf, r[k]) == i for _, r in hf.scan_bucket(i)), item["name"]


def test_e2e_selections_give_the_sqlite_signature(base, loaded):
    # Igualdade em aluno.dre (UNIQUE) pelos dois caminhos da P2 e faixa em aluno.cr por scan, para cada consulta.
    root, b, _ = loaded
    ops = {"=": lambda x, v: x == v, "<": lambda x, v: x < v, "<=": lambda x, v: x <= v,
           ">": lambda x, v: x > v, ">=": lambda x, v: x >= v}
    with engine(root, b, prefetch=8) as e:
        heap, hs_dre = e.catalog.table("aluno__heap"), e.catalog.table("aluno__hs_dre")
        cr = heap.schema.idx("cr")
        for q in [q for q in workload(base) if q["kind"] == "select"]:
            want = (q["expected"]["count"], q["expected"]["checksum"])
            if q["column"] == "dre":
                by_index = [heap.data.fetch(p) for p in heap.indexes["dre"].search(q["value"])]
                by_hash = [r for _, r in hs_dre.data.search(q["value"])]
                assert output_signature(q, by_index) == want, q["id"]
                assert output_signature(q, by_hash) == want, q["id"]
            else:
                for name in ("aluno__heap", "aluno__hs_cr"):
                    rows = [r for _, r in e.catalog.table(name).data.scan() if ops[q["operator"]](r[cr], q["value"])]
                    assert output_signature(q, rows) == want, (q["id"], name)
        assert e.bp.pinned() == 0


def chain_position(hf, key):
    """Quantos blocos a busca UNIQUE de `key` lê: a posição do bloco onde ela está na cadeia do bucket."""
    k, blocks = hf.schema.idx(hf.spec.key), []
    for rid, r in hf.scan_bucket(hf.bucket_of(key)):
        if rid.block not in blocks:
            blocks.append(rid.block)
        if r[k] == key:
            return len(blocks)
    raise AssertionError(f"{key} não está no arquivo")


def test_e2e_cold_counts_match_the_cost_formulas(base, loaded):
    # N4 (AGENTS §7) na pilha real, cache frio (Engine.cold):
    # - scan do heap: b leituras; misses = b sem prefetch e ⌈b / (1 + k)⌉ com prefetch k = 8 (D-P2-19);
    # - busca UNIQUE no hash primário: os blocos da cadeia até o que tem a chave (1 sem overflow);
    # - índice hash + fetch: os blocos da cadeia do índice (área INDEX) + 1 bloco do heap (área DATA).
    root, b, _ = loaded
    dres = [q["value"] for q in workload(base) if q["kind"] == "select" and q["column"] == "dre"]
    for k in (0, 8):
        with engine(root, b, prefetch=k) as e:
            t = e.catalog.table("aluno__heap")
            nb = t.stats().n_blocks
            e.cold()
            assert sum(1 for _ in t.data.scan()) == t.stats().n_records
            s = e.snapshot()
            assert (s["io.reads"], s["bp.DATA.misses"]) == (nb, math.ceil(nb / (1 + k))), (b, k)
            hs = e.catalog.table("aluno__hs_dre").data
            idx = t.indexes["dre"]
            for dre in dres:
                want = chain_position(hs, dre)
                e.cold()
                assert len(list(hs.search(dre))) == 1
                assert e.snapshot()["io.reads"] == want, dre
                want_idx = chain_position(idx._hf, dre)
                e.cold()
                assert len([t.data.fetch(p) for p in idx.search(dre)]) == 1
                s = e.snapshot()
                assert (s["bp.INDEX.misses"], s["bp.DATA.misses"], s["io.reads"]) == (want_idx, 1, want_idx + 1)
            assert e.bp.pinned() == 0


def eq_pair(e, q):
    r = e.catalog.table(f"{q['r_table']}__hs_{q['r_column']}_eq_{q['size_combo']}").data
    s = e.catalog.table(f"{q['s_table']}__hs_{q['s_column']}_eq_{q['size_combo']}").data
    return r, s


def test_e2e_bucket_join_with_the_p5_contract(base, loaded):
    # Bucket-join (guia P5 §6): os dois lados HASH com N e seed iguais (D-P6-01); bucket i de r × bucket i de s.
    # Só a interface do §5.2: n_buckets, seed, bucket_of e scan_bucket.
    root, b, _ = loaded
    with engine(root, b) as e:
        for q in [q for q in workload(base) if q["kind"] == "join"]:
            r, s = eq_pair(e, q)
            assert (r.n_buckets, r.seed) == (s.n_buckets, s.seed) and r.seed == 480 and r.n_buckets > 1, q["id"]
            rk, sk = r.schema.idx(q["r_column"]), s.schema.idx(q["s_column"])
            out = []
            for i in range(r.n_buckets):
                left = defaultdict(list)                          # dublê do P5: o bucket i de r em memória
                for _, x in r.scan_bucket(i):
                    assert bucket_rule(r, x[rk]) == i == bucket_rule(s, x[rk])
                    left[x[rk]].append(x)
                for _, y in s.scan_bucket(i):
                    assert bucket_rule(s, y[sk]) == i
                    out += [x + y for x in left.get(y[sk], ())]
            assert output_signature(q, out) == (q["expected"]["count"], q["expected"]["checksum"]), q["id"]
        assert e.bp.pinned() == 0


def partitioned_join(e, part_side, hf, part_col, hash_col, hash_is_r):
    """Dublê do hash join do P5 com um lado HASH (guia P5 §6): o outro lado é particionado em N_H TempHeaps (WORK)
    pela regra do P5, hf.bucket_of(k) % N_H; a partição j do lado hash é scan_bucket(i) para i ≡ j (mod N_H).
    Saída: registro de r + registro de s (arquitetura §7.3), seja o hash o r ou o s."""
    pk, hk = part_side.schema.idx(part_col), hf.schema.idx(hash_col)
    parts = [e.catalog.temp(part_side.schema) for _ in range(N_H)]
    for _, x in part_side.data.scan():
        parts[hf.bucket_of(x[pk]) % N_H].append(x)
    for p in parts:
        p.finish()
    assert sum(p.n_records() for p in parts) == part_side.stats().n_records
    assert e.bp.pinned() == 0
    out = []
    for j, p in enumerate(parts):
        mem = defaultdict(list)                                   # dublê do P5: a partição j em memória
        for x in p.scan():
            mem[x[pk]].append(x)
        for i in range(j, hf.n_buckets, N_H):
            for _, y in hf.scan_bucket(i):
                out += [y + x if hash_is_r else x + y for x in mem.get(y[hk], ())]
        name = p.name
        p.drop()
        assert not e.bp.fm.exists(name), "drop() deixou o arquivo temporário"
    return out


def test_e2e_partitioned_join_with_temp_files_both_orientations(base, loaded):
    # As duas orientações da configuração b da matriz: o detalhe (s) em hash e o mestre particionado, e o mestre
    # (r) em hash (variante __hs_<col>, com a seed da tabela) e o detalhe particionado.
    root, b, _ = loaded
    with engine(root, b) as e:
        for q in [q for q in workload(base) if q["kind"] == "join"]:
            want = (q["expected"]["count"], q["expected"]["checksum"])
            r_heap, s_heap = e.catalog.table(f"{q['r_table']}__heap"), e.catalog.table(f"{q['s_table']}__heap")
            s_hash = e.catalog.table(f"{q['s_table']}__hs_{q['s_column']}_eq_{q['size_combo']}").data
            r_hash = e.catalog.table(f"{q['r_table']}__hs_{q['r_column']}").data
            out = partitioned_join(e, r_heap, s_hash, q["r_column"], q["s_column"], hash_is_r=False)
            assert output_signature(q, out) == want, (q["id"], "s em hash")
            out = partitioned_join(e, s_heap, r_hash, q["s_column"], q["r_column"], hash_is_r=True)
            assert output_signature(q, out) == want, (q["id"], "r em hash")
        assert e.bp.pinned() == 0


def test_e2e_p1_contract_disk_only_through_the_buffer_and_one_area_per_file(base, tmp_path, monkeypatch):
    # Regra 1 (AGENTS §0): só cbd/storage/buffer_pool.py chama FileManager.read/write, na carga inteira, nas
    # consultas, num delete + reorganize, num TempHeap e numa reabertura. E cada arquivo da P2 (inclusive os que
    # somem: o heap antes do reorganize, a cópia do reorganize, os índices refeitos, o temporário) pede as
    # páginas numa área só. A chave é (FileManager, fid): o fid recomeça em 1 em cada FileManager.
    # LIMITAÇÃO: espia o BufferPool._pin (privado do P1), por onde passam pin, page e o cursor do seq_scan; se o
    # P1 mudar esse nome, o teste quebra com AttributeError (não passa calado).
    callers, areas, names = set(), defaultdict(set), {}
    real_read, real_write = FileManager.read, FileManager.write

    def read(self, *a, **k):
        callers.add(sys._getframe(1).f_globals["__name__"])
        return real_read(self, *a, **k)

    def write(self, *a, **k):
        callers.add(sys._getframe(1).f_globals["__name__"])
        return real_write(self, *a, **k)
    real_pin, real_new = BufferPool._pin, BufferPool.new_page

    def note(self, fid, ptype):
        areas[(id(self.fm), fid)].add(PageType(ptype))
        names.setdefault((id(self.fm), fid), self.fm._names.get(fid) if hasattr(self.fm, "_names") else fid)

    def pin(self, fid, bno, ptype, cursor):
        note(self, fid, ptype)
        return real_pin(self, fid, bno, ptype, cursor)

    def new_page(self, fid, ptype=PageType.DATA):
        note(self, fid, ptype)
        return real_new(self, fid, ptype)
    monkeypatch.setattr(FileManager, "read", read)
    monkeypatch.setattr(FileManager, "write", write)
    monkeypatch.setattr(BufferPool, "_pin", pin)
    monkeypatch.setattr(BufferPool, "new_page", new_page)

    b = 2048
    plan = p2_plan(base, b)
    gone = lambda name, r: name == "aluno__heap" and r[0] % 7 == 0      # dre múltiplo de 7: apagado
    with SQLiteReference(base / "ref.sqlite") as ref:
        with engine(tmp_path, b, prefetch=4) as e:
            load_physical(e, ref, plan)
            t = e.catalog.table("aluno__heap")
            for ptr in [p for p, r in t.data.scan() if gone("aluno__heap", r)]:
                t.delete(ptr)
            t.reorganize()
            tmp = e.catalog.temp(t.schema)
            for _, r in t.data.scan():
                tmp.append(r)
            tmp.finish()
            assert list(tmp.scan()) == [r for _, r in t.data.scan()]
            tmp.drop()
            fm1 = id(e.fm)
            want = {(fm1, h.fid): {PageType.CATALOG} for h in e.catalog._heaps.values()}
            for name in e.catalog.tables():
                tb = e.catalog.table(name)
                want[(fm1, tb.data.fid)] = {PageType.DATA}
                for idx in tb.indexes.values():
                    want[(fm1, idx._hf.fid)] = {PageType.INDEX}
            assert e.bp.pinned() == 0
        with engine(tmp_path, b) as e:
            check_against_sqlite(e, ref, plan, b, drop=gone)
    assert callers == {"cbd.storage.buffer_pool"}, callers
    for key, w in want.items():
        assert areas[key] == w, (names.get(key), areas[key], w)
    mixed = {names.get(k): a for k, a in areas.items() if len(a) != 1}
    assert not mixed, f"arquivos com mais de uma área: {mixed}"
