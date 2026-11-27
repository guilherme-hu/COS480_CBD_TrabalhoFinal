"""Testes do heap e do arquivo temporário (P2, #43). Decisões: D-P2-07 a D-P2-10.

N1/N2: o heap sobre o buffer pool real (fixture `bp` ou um pool com outro tamanho de bloco).
N3: a base do gerador. N4: contagens de blocos com cache frio. N5: operações aleatórias contra um dict.
"""
import random

import pytest

from cbd.contracts import (BufferConfig, Field, FType, NotApplicable, Org, OrgSpec, PageType, RID,
                           RecordTooLarge, Schema, StorageConfig)
from cbd.files.heap import HeapFile, TempHeap
from cbd.records.codec import RecordCodec
from cbd.records.slotted_page import SlottedPage
from cbd.storage.buffer_pool import BufferPool
from cbd.storage.file_manager import FileManager

S = Schema("t", (Field("k", FType.INT), Field("v", FType.VARCHAR, 3000)))
SPEC = OrgSpec(Org.HEAP)


def rec(i, n=100):
    return (i, "x" * n)


def cold(bp):
    # O mesmo que Engine.cold(): grava os sujos, esvazia o buffer e zera os contadores.
    bp.clear()
    bp.fm.reset_stats()
    bp.reset_stats()


@pytest.fixture
def pool(tmp_path):
    """Buffer pool com outro tamanho de bloco: pool(2048)."""
    made = []

    def make(block_size):
        fm = FileManager(StorageConfig(root_dir=str(tmp_path / str(block_size)), block_size=block_size))
        p = BufferPool(fm, BufferConfig())
        made.append(p)
        return p
    yield make
    for p in made:
        assert p.pinned() == 0, "pin sem unpin"
        p.flush_all()
        p.fm.close_all()


def blocks_model(sizes, block_size):
    # Enchimento em ordem (guia P2 §5.3): cabeçalho de SlottedPage.HEADER e 4 B de slot por registro.
    blocks, free = 0, 0
    for s in sizes:
        if s + 4 > free:
            blocks, free = blocks + 1, block_size - SlottedPage.HEADER
        free -= s + 4
    return blocks


# ---------- básico ----------

def test_insert_fetch_scan(bp):
    h = HeapFile.create(bp, "h", S, SPEC)
    rids = [h.insert(rec(i)) for i in range(100)]
    assert all(isinstance(r, RID) for r in rids)
    assert [h.fetch(r) for r in rids] == [rec(i) for i in range(100)]
    assert sorted(r for _, r in h.scan()) == [rec(i) for i in range(100)]
    assert {p for p, _ in h.scan()} == set(rids)
    assert h.stats()["records"] == 100


def test_search_and_range_are_not_applicable(bp):
    h = HeapFile.create(bp, "h", S, SPEC)
    with pytest.raises(NotApplicable):
        h.search(1)
    with pytest.raises(NotApplicable):
        h.range(1, 2)


def test_ptr_is_six_bytes(bp):
    # D-P2-10: RID = bloco u32 + slot u16.
    h = HeapFile.create(bp, "h", S, SPEC)
    assert h.ptr_size == 6
    for r in (RID(1, 0), RID(2**32 - 1, 65535), RID(7, 1636)):
        b = h.encode_ptr(r)
        assert len(b) == 6 and h.decode_ptr(b) == r
    for bad in (RID(2**32, 0), RID(1, 65536), RID(-1, 0)):
        with pytest.raises(ValueError):
            h.encode_ptr(bad)


def test_delete_and_fetch_errors(bp):
    h = HeapFile.create(bp, "h", S, SPEC)
    r = h.insert(rec(1))
    h.delete(r)
    with pytest.raises(KeyError):
        h.fetch(r)
    with pytest.raises(KeyError):
        h.delete(r)
    for bad in (RID(r.block, 99), RID(999, 0), RID(1, 0)):     # slot inexistente, bloco fora, bloco de meta
        with pytest.raises(KeyError):
            h.fetch(bad)
    assert h.stats()["records"] == 0


def test_record_too_large_touches_nothing(bp):
    # D-P2-06: o heap levanta RecordTooLarge antes de tocar em qualquer página.
    big = Schema("b", (Field("v", FType.VARCHAR, 9000),))
    h = HeapFile.create(bp, "h", big, SPEC)
    limit = SlottedPage.max_record(bp.block_size)        # registro = 2 B de tamanho + texto
    h.insert(("y" * (limit - 2),))                          # cabe exatamente
    before = (bp.fm.n_blocks(h.fid), h.stats()["records"])
    with pytest.raises(RecordTooLarge):
        h.insert(("y" * (limit - 1),))
    assert (bp.fm.n_blocks(h.fid), h.stats()["records"]) == before
    with pytest.raises(RecordTooLarge):
        h.bulk_load([("y" * (limit - 1),)])


def test_close_and_open_keep_everything(bp):
    h = HeapFile.create(bp, "h", S, SPEC)
    rids = [h.insert(rec(i)) for i in range(200)]
    h.delete(rids[5])
    h.close()
    h = HeapFile.open(bp, "h", S, SPEC)
    assert h.stats()["records"] == 199
    assert sorted(r for _, r in h.scan()) == sorted(rec(i) for i in range(200) if i != 5)
    assert h.fetch(rids[7]) == rec(7)
    h.close()


def test_open_rejects_a_file_that_is_not_a_heap(bp):
    fid = bp.fm.create("nada")
    bp.unpin(bp.new_page(fid), dirty=True)                 # bloco 1 zerado: sem a marca do heap
    bp.close_file(fid)
    with pytest.raises(ValueError):
        HeapFile.open(bp, "nada", S, SPEC)


# ---------- carga em massa ----------

def test_bulk_load_fills_in_order(bp):
    # Enche cada bloco até o próximo não caber, na ordem de chegada (guia P2 §5.3).
    h = HeapFile.create(bp, "h", S, SPEC)
    recs = [rec(i, 50 + i % 70) for i in range(2000)]
    h.bulk_load(recs)
    sizes = [len(RecordCodec(S).encode(r)) for r in recs]
    data_blocks = blocks_model(sizes, bp.block_size)
    st = h.stats()
    assert st["records"] == 2000
    assert st["blocks"] == data_blocks + st["meta_blocks"]
    assert st["meta_blocks"] == 2                          # meta + 1 página de mapa
    assert [r for _, r in h.scan()] == recs                # a ordem física é a de chegada


def test_bulk_load_needs_an_empty_heap(bp):
    h = HeapFile.create(bp, "h", S, SPEC)
    h.insert(rec(1))
    with pytest.raises(ValueError):
        h.bulk_load([rec(2)])


# ---------- custos com cache frio (N4) ----------

def test_heap_scan_cost(bp):
    # Critério da #43: heap de b blocos de dados, cache frio, scan = b leituras e 1 seek
    # (o bulk_load grava os dados contíguos depois da meta e o mapa depois dos dados).
    h = HeapFile.create(bp, "h", S, SPEC)
    h.bulk_load([rec(i, 200) for i in range(3000)])
    b = h.stats()["blocks"] - h.stats()["meta_blocks"]
    assert b > bp.capacity(PageType.DATA)                  # maior que o buffer: nada fica em cache
    cold(bp)
    assert sum(1 for _ in h.scan()) == 3000
    assert bp.fm.io.reads == b and bp.fm.io.seeks == 1 and bp.fm.io.writes == 0


def test_fetch_cost_is_one_block(bp):
    h = HeapFile.create(bp, "h", S, SPEC)
    rids = [h.insert(rec(i)) for i in range(500)]
    cold(bp)
    assert h.fetch(rids[321]) == rec(321)
    assert bp.fm.io.reads == 1


def test_insert_into_tail_costs_no_read(bp):
    # Sem remoções, o insert usa a cauda, que já está no buffer: nenhuma leitura de mapa.
    h = HeapFile.create(bp, "h", S, SPEC)
    for i in range(50):
        h.insert(rec(i))
    bp.fm.reset_stats()
    for i in range(50, 60):
        h.insert(rec(i))
    assert bp.fm.io.reads == 0


# ---------- espaço livre (D-P2-09) ----------

def test_heap_delete_reuse(bp):
    # Critério da #43: apagar 30% e inserir de novo não faz o arquivo crescer.
    r = random.Random(1)
    h = HeapFile.create(bp, "h", S, SPEC)
    rids = [h.insert(rec(i, r.randint(50, 150))) for i in range(3000)]
    blocks = h.stats()["blocks"]
    for rid in r.sample(rids, 900):
        h.delete(rid)
    for i in range(900):
        h.insert(rec(10_000 + i, r.randint(50, 150)))
    assert h.stats()["blocks"] == blocks
    assert h.stats()["records"] == 3000


def test_free_space_map_sends_insert_to_the_hole(bp):
    h = HeapFile.create(bp, "h", S, SPEC)
    rids = [h.insert(rec(i, 200)) for i in range(200)]
    first = rids[0].block
    for rid in rids[:3]:                                    # abre um buraco no primeiro bloco
        h.delete(rid)
    tail = max(r.block for r in rids)
    while True:                                             # enche a cauda até o próximo não caber nela
        r = h.insert(rec(-1, 200))
        if r.block != tail:
            break
    assert r.block == first                                 # o mapa mandou para o buraco, não para um bloco novo


def test_stale_map_entry_is_corrected(bp):
    # O mapa é só uma dica (D-P2-09): se a entrada prometer mais do que o bloco tem, o heap corrige e segue.
    h = HeapFile.create(bp, "h", S, SPEC)
    rids = [h.insert(rec(i, 300)) for i in range(100)]
    h.delete(rids[0])
    full = rids[50].block
    h._set_free(full, 4000)                                 # entrada mentirosa para um bloco cheio
    for i in range(30):
        h.insert(rec(1000 + i, 300))
    assert h.stats()["records"] == 129
    assert sorted(x for _, x in h.scan()) == sorted([rec(i, 300) for i in range(1, 100)]
                                                     + [rec(1000 + i, 300) for i in range(30)])
    assert h._get_free(full) < 300                          # a entrada foi corrigida ao pinar o bloco


def test_map_spans_several_pages(pool, monkeypatch):
    # Com poucas entradas por página de mapa, o arquivo cresce com páginas de mapa no meio dos
    # dados; o scan pula as páginas de mapa e o reuso continua funcionando.
    bp = pool(2048)
    monkeypatch.setattr(HeapFile, "_FSM_PER_PAGE", 8)
    h = HeapFile.create(bp, "h", S, SPEC)
    rids = [h.insert(rec(i, 300)) for i in range(300)]
    assert h.stats()["meta_blocks"] > 3
    assert sorted(x for _, x in h.scan()) == [rec(i, 300) for i in range(300)]
    for rid in rids[::3]:
        h.delete(rid)
    blocks = h.stats()["blocks"]
    for i in range(100):
        h.insert(rec(1000 + i, 300))
    assert h.stats()["blocks"] == blocks
    h.close()
    h = HeapFile.open(bp, "h", S, SPEC)
    assert sum(1 for _ in h.scan()) == 300
    h.close()


# ---------- reorganize ----------

def test_heap_reorganize(bp):
    # Critério da #43: blocks diminui depois de reorganize; o conteúdo continua o mesmo.
    h = HeapFile.create(bp, "h", S, SPEC)
    rids = [h.insert(rec(i, 150)) for i in range(2000)]
    for rid in rids[::2]:
        h.delete(rid)
    before = h.stats()["blocks"]
    h.reorganize()
    st = h.stats()
    assert st["blocks"] < before
    assert st["records"] == 1000 and st["deleted"] == 0
    assert sorted(x for _, x in h.scan()) == [rec(i, 150) for i in range(1, 2000, 2)]
    assert not bp.fm.exists("h__reorg")
    h.close()
    h = HeapFile.open(bp, "h", S, SPEC)
    assert h.stats()["records"] == 1000
    h.close()


# ---------- arquivo temporário ----------

def test_temp(bp):
    # Critério da #43: append de 1000, finish, scan devolve os mesmos; drop remove o arquivo.
    t = TempHeap(bp, "__tmp_teste", S)
    recs = [rec(i, i % 200) for i in range(1000)]
    for r in recs:
        t.append(r)
    assert bp.pinned(PageType.WORK) == 1                   # só a página de saída
    t.finish()
    assert bp.pinned() == 0
    assert list(t.scan()) == recs
    assert t.n_records() == 1000 and t.n_blocks() == bp.fm.n_blocks(t.fid)
    t.drop()
    assert not bp.fm.exists("__tmp_teste")
    t.drop()                                                # idempotente


def test_temp_uses_work_pages_and_drop_without_finish(bp):
    t = TempHeap(bp, "__tmp_2", S)
    t.append(rec(1))
    t.drop()                                                # drop sem finish solta a página de saída
    assert bp.pinned() == 0 and not bp.fm.exists("__tmp_2")


def test_temp_record_too_large(bp):
    big = Schema("b", (Field("v", FType.VARCHAR, 9000),))
    t = TempHeap(bp, "__tmp_3", big)
    with pytest.raises(RecordTooLarge):
        t.append(("y" * 5000,))
    t.drop()


# ---------- base real (N3) ----------

@pytest.mark.parametrize("table", ["curso", "disciplina", "aluno", "inscricao"])
def test_generated_base_in_heap(bp, table):
    from bench.generator import Dataset
    from bench.schema import SCHEMAS
    recs = list(Dataset(scale=0.01).records(table))
    h = HeapFile.create(bp, table, SCHEMAS[table], SPEC)
    h.bulk_load(recs)
    codec = RecordCodec(SCHEMAS[table])
    data = h.stats()["blocks"] - h.stats()["meta_blocks"]
    assert data == blocks_model([len(codec.encode(r)) for r in recs], bp.block_size)
    assert [r for _, r in h.scan()] == [tuple(r) for r in recs]


# ---------- estresse pequeno (N5) ----------

@pytest.mark.parametrize("seed", range(3))
def test_random_ops_match_dict_oracle(pool, seed):
    r = random.Random(seed)
    bp = pool(2048)
    h = HeapFile.create(bp, "h", S, SPEC)
    oracle = {}
    for step in range(3000):
        op = r.random()
        if op < 0.6 or not oracle:
            x = rec(step, r.randint(1, 700))
            oracle[h.insert(x)] = x
        elif op < 0.95:
            rid = r.choice(list(oracle))
            h.delete(rid)
            del oracle[rid]
        elif op < 0.98:
            h.close()
            h = HeapFile.open(bp, "h", S, SPEC)
        else:
            values = sorted(oracle.values())
            h.reorganize()                                  # os RIDs mudam; o conteúdo não
            oracle = dict(h.scan())
            assert sorted(oracle.values()) == values
        if step % 250 == 0:
            assert dict(h.scan()) == oracle
    assert dict(h.scan()) == oracle
    assert h.stats()["records"] == len(oracle)
    h.close()
