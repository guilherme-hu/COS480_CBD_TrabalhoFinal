"""Testes do heap e do arquivo temporário (P2, #43). Decisões: D-P2-07 a D-P2-10.

N1/N2: o heap sobre o buffer pool real (fixture `bp` ou um pool com outro tamanho de bloco).
N3: a base do gerador. N4: contagens de blocos com cache frio. N5: operações aleatórias contra um dict.
"""
import math
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


def read_temp_after_crash(root, name, schema):
    # "Queda": outro FileManager e outro BufferPool leem só o que está no disco (pelo buffer, nunca fm.read).
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
    requests = bp.stats.requests
    with pytest.raises(RecordTooLarge):
        h.insert(("y" * (limit - 1),))
    assert bp.stats.requests == requests                    # nem pinou a cauda
    h2 = HeapFile.create(bp, "h2", big, SPEC)
    with pytest.raises(RecordTooLarge):
        h2.bulk_load([("y" * (limit - 1),)])


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
    h.delete(rids[90])                                      # o buraco de verdade fica num bloco do fim
    full = rids[10].block                                   # um bloco cheio antes dele, com entrada mentirosa
    h._set_free(full, 4000)
    for i in range(30):
        h.insert(rec(1000 + i, 300))
    assert h.stats()["records"] == 129
    assert sorted(x for _, x in h.scan()) == sorted([rec(i, 300) for i in range(100) if i != 90]
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
    assert not bp.fm.exists("h__reorg0")
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


# ---------- achados da revisão independente (ai/P2/review.md) ----------

def test_open_without_close_sees_current_counters(bp):
    # A meta acompanha cada mudança: abrir o mesmo arquivo sem close() vê os contadores atuais.
    h = HeapFile.create(bp, "h", S, SPEC)
    rids = [h.insert(rec(i, 10)) for i in range(10)]
    for r in rids[:3]:
        h.delete(r)
    h2 = HeapFile.open(bp, "h", S, SPEC)
    assert h2.stats()["records"] == 7 and h2.stats()["deleted"] == 3
    h2.close()


def test_deleted_counts_dead_slots_not_deletes_ever(bp):
    # n_deleted = slots apagados que ainda existem: o insert que reaproveita um slot apagado desconta.
    h = HeapFile.create(bp, "h", S, SPEC)
    rids = [h.insert(rec(i, 10)) for i in range(10)]
    h.delete(rids[3])
    r = h.insert(rec(99, 10))
    assert r == rids[3]                                     # o slot 3 da cauda foi reaproveitado
    assert h.stats()["deleted"] == 0


@pytest.mark.parametrize("bad", [0.0, "0", None, True])
def test_bad_rid_types_raise_keyerror_without_leaking_pins(bp, bad):
    h = HeapFile.create(bp, "h", S, SPEC)
    r = h.insert(rec(1))
    for call in (h.delete, h.fetch):
        with pytest.raises(KeyError):
            call(RID(r.block, bad))
        with pytest.raises(KeyError):
            call(RID(bad, 0))
    assert bp.pinned() == 0


def test_failure_in_new_page_keeps_the_original_error(bp, monkeypatch):
    # Se alocar a página falhar no meio da carga, o erro original sobe (nada de unpin duplo).
    real, calls = bp.new_page, []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) == 3:
            raise OSError("disco cheio")
        return real(*a, **k)
    h = HeapFile.create(bp, "h", S, SPEC)
    t = TempHeap(bp, "__tmp_f", S)
    monkeypatch.setattr(bp, "new_page", flaky)
    with pytest.raises(OSError):
        h.bulk_load(rec(i, 2000) for i in range(10))
    calls.clear()
    with pytest.raises(OSError):
        for i in range(10):
            t.append(rec(i, 2000))
    monkeypatch.setattr(bp, "new_page", real)
    t.drop()
    assert bp.pinned() == 0 and not bp.fm.exists("__tmp_f")


def test_failure_after_new_block_keeps_counters_right(bp, monkeypatch):
    h = HeapFile.create(bp, "h", S, SPEC)
    for i in range(3):
        h.insert(rec(i, 1500))                              # 2 por bloco: o 3º abre bloco novo
    monkeypatch.setattr(h, "_fsm_page", lambda k: (_ for _ in ()).throw(OSError("falha")))
    with pytest.raises(OSError):
        for i in range(3, 6):
            h.insert(rec(i, 1500))
    monkeypatch.undo()
    assert h.stats()["records"] == sum(1 for _ in h.scan())


def test_close_twice_is_harmless(bp):
    h = HeapFile.create(bp, "h", S, SPEC)
    h.insert(rec(1))
    h.close()
    h.close()


def test_edge_cases_on_empty_heap(pool):
    bp = pool(8192)
    h = HeapFile.create(bp, "h", S, SPEC)
    assert list(h.scan()) == []
    h.reorganize()
    h.bulk_load(iter(()))
    assert h.insert(rec(1)).block >= 2
    it = h.scan()
    next(it)
    it.close()                                              # scan abandonado solta o pin
    assert bp.pinned() == 0
    h.close()



# ---------- testes da revisão de qualidade (mutantes que sobreviviam) ----------

def check_map(h):
    # D-P2-09: fora da cauda, a entrada arredonda o livre real para baixo e esconde menos de B/256 bytes.
    step = h._b // 256
    for a, z in h._ranges:
        for bno in range(a, z + 1):
            if bno == h._tail:
                continue
            with h.bp.page(h.fid, bno, h.ptype) as p:
                real = SlottedPage(p.data).free_space()
            got = h._get_free(bno)
            assert got <= real < got + step, (bno, got, real)


def test_map_entries_round_down(bp):
    r = random.Random(7)
    h = HeapFile.create(bp, "h", S, SPEC)
    h.bulk_load([rec(i, r.randint(300, 1500)) for i in range(300)])
    check_map(h)
    rids = [h.insert(rec(1000 + i, r.randint(300, 1500))) for i in range(300)]
    check_map(h)
    for rid in r.sample(rids, 100):
        h.delete(rid)
    check_map(h)
    for i in range(100):
        h.insert(rec(5000 + i, r.randint(50, 1500)))
    check_map(h)


def test_map_survives_eviction(bp):
    h = HeapFile.create(bp, "h", S, SPEC)
    rids = [h.insert(rec(i, 300)) for i in range(400)]
    cold(bp)
    for rid in rids[:200:2]:
        h.delete(rid)
    cold(bp)
    check_map(h)


def test_candidates_only_blocks_that_fit(bp):
    h = HeapFile.create(bp, "h", S, SPEC)
    rids = [h.insert(rec(i, 300)) for i in range(200)]
    for rid in rids[:150:3]:
        h.delete(rid)
    for n in range(1, 2000, 7):
        for bno in h._candidates(n):
            assert h._get_free(bno) >= n, (n, bno)


def test_map_not_consulted_without_deletes(bp, monkeypatch):
    h = HeapFile.create(bp, "h", S, SPEC)
    monkeypatch.setattr(h, "_candidates", lambda n: pytest.fail("mapa consultado sem remoções"))
    for i in range(300):
        h.insert(rec(i, 200))


def test_insert_into_tail_costs_two_pins(bp):
    # Cauda e meta, as duas no buffer: 2 requisições, nenhuma leitura.
    h = HeapFile.create(bp, "h", S, SPEC)
    h.insert(rec(0))
    bp.reset_stats()
    bp.fm.reset_stats()
    h.insert(rec(1))
    assert bp.stats.requests == 2 and bp.fm.io.reads == 0


def test_reuse_after_reopen(bp):
    h = HeapFile.create(bp, "h", S, SPEC)
    rids = [h.insert(rec(i, 200)) for i in range(1000)]
    for rid in rids[::3]:
        h.delete(rid)
    h.close()
    h = HeapFile.open(bp, "h", S, SPEC)
    blocks = h.stats()["blocks"]
    for i in range(330):
        h.insert(rec(5000 + i, 200))
    assert h.stats()["blocks"] == blocks
    h.close()


def test_meta_has_structure_without_close(bp):
    h = HeapFile.create(bp, "h", S, SPEC)
    for i in range(500):
        h.insert(rec(i, 200))
    bp.flush_all()
    bp.drop_file(h.fid)
    bp.fm.close(h.fid)                                      # "queda" sem close()
    h = HeapFile.open(bp, "h", S, SPEC)
    assert sum(1 for _ in h.scan()) == 500
    h.close()


def test_insert_after_bulk_load_uses_last_block(bp):
    h = HeapFile.create(bp, "h", S, SPEC)
    h.bulk_load([rec(i) for i in range(10)])
    blocks = h.stats()["blocks"]
    h.insert(rec(99))
    assert h.stats()["blocks"] == blocks


def test_scans_use_seq_and_toss(bp, monkeypatch):
    h = HeapFile.create(bp, "h", S, SPEC)
    h.bulk_load([rec(i, 200) for i in range(200)])
    t = TempHeap(bp, "__tmp_spy", S)
    for i in range(200):
        t.append(rec(i, 200))
    t.finish()
    calls, pin, unpin = [], bp.pin, bp.unpin

    def spin(fid, bno, ptype=PageType.DATA, **kw):
        calls.append(kw.get("seq", False))
        return pin(fid, bno, ptype, **kw)

    def sunpin(page, dirty=False, toss=False):
        calls.append(toss)
        return unpin(page, dirty=dirty, toss=toss)
    monkeypatch.setattr(bp, "pin", spin)
    monkeypatch.setattr(bp, "unpin", sunpin)
    list(h.scan())
    list(t.scan())
    assert calls and all(calls)
    monkeypatch.undo()
    t.drop()


def test_temp_larger_than_work_area(bp):
    t = TempHeap(bp, "__tmp_big", S)
    recs = [rec(i, 1000) for i in range(1000)]               # ≈ 250 blocos, contra 64 frames de WORK
    for r in recs:
        t.append(r)
    t.finish()
    assert t.n_blocks() > bp.capacity(PageType.WORK)
    assert list(t.scan()) == recs
    t.drop()


def test_delete_is_written_to_disk(bp):
    h = HeapFile.create(bp, "h", S, SPEC)
    rids = [h.insert(rec(i)) for i in range(50)]
    cold(bp)
    h.delete(rids[7])
    cold(bp)
    with pytest.raises(KeyError):
        h.fetch(rids[7])


def test_scan_of_heap_loaded_by_insert_has_one_seek(bp):
    # A 1ª página do mapa nasce com a meta (bloco 2), então inserts um a um deixam os dados contíguos.
    h = HeapFile.create(bp, "h", S, SPEC)
    for i in range(3000):
        h.insert(rec(i, 200))
    b = h.stats()["blocks"] - h.stats()["meta_blocks"]
    cold(bp)
    assert sum(1 for _ in h.scan()) == 3000
    assert bp.fm.io.reads == b and bp.fm.io.seeks == 1


def test_read_only_session_writes_nothing(bp):
    h = HeapFile.create(bp, "h", S, SPEC)
    h.bulk_load([rec(i) for i in range(100)])
    h.close()
    h = HeapFile.open(bp, "h", S, SPEC)
    bp.fm.reset_stats()
    list(h.scan())
    h.close()
    assert bp.fm.io.writes == 0                             # a meta igual não vira gravação


def test_open_of_non_heap_closes_the_file(bp):
    fid = bp.fm.create("nada2")
    bp.unpin(bp.new_page(fid), dirty=True)
    bp.close_file(fid)
    before = {f for f in range(1, 100) if bp.fm.is_open(f)}
    with pytest.raises(ValueError):
        HeapFile.open(bp, "nada2", S, SPEC)
    assert {f for f in range(1, 100) if bp.fm.is_open(f)} == before


def test_reorganize_failure_after_delete_keeps_the_temp(bp, tmp_path, monkeypatch):
    h = HeapFile.create(bp, "h", S, SPEC)
    for i in range(300):
        h.insert(rec(i, 300))
    real, calls = bp.new_page, []

    def flaky(fid, ptype=PageType.DATA):
        calls.append(1)
        if ptype is PageType.DATA and len(calls) > 30:
            raise OSError("disco cheio")
        return real(fid, ptype)
    monkeypatch.setattr(bp, "new_page", flaky)
    with pytest.raises(OSError) as err:
        h.reorganize()
    monkeypatch.undo()
    # Revisto no /code-review do PR #102 (26/11): a falha vem na recarga, depois de apagar o original, e o
    # temporário é a única cópia dos 300 registros. Antes ele era apagado aqui (perda de dados); agora fica no
    # disco (lido por outro FileManager, como depois de uma queda) e a exceção diz o nome. Antes este teste se
    # chamava test_reorganize_failure_cleans_up; o que ele pegava (página WORK fixada, review.md) continua coberto.
    assert bp.pinned() == 0
    assert any("h__reorg0" in note for note in err.value.__notes__)
    assert read_temp_after_crash(tmp_path, "h__reorg0", S) == sorted(rec(i, 300) for i in range(300))


def test_reorganize_failure_during_the_copy_cleans_up(bp, monkeypatch):
    # Falha antes de apagar o original (na cópia para o temporário): o original fica inteiro, o temporário some.
    h = HeapFile.create(bp, "h", S, SPEC)
    for i in range(300):
        h.insert(rec(i, 300))
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
    assert sorted(r for _, r in h.scan()) == [rec(i, 300) for i in range(300)]


# ---------- 2ª revisão independente ----------

def test_insert_returns_the_right_rid_when_the_frame_is_recycled(tmp_path):
    # Com MRU, o frame do bloco novo, recém-solto, é a próxima vítima: o RID não pode ser lido dele depois.
    from cbd.contracts import Policy
    fm = FileManager(StorageConfig(root_dir=str(tmp_path)))
    bp = BufferPool(fm, BufferConfig(policy=Policy.MRU))
    g = HeapFile.create(bp, "g", S, SPEC)
    g.bulk_load(rec(i, 200) for i in range(3000))           # ≈ 150 blocos > 128 frames
    h = HeapFile.create(bp, "h", S, SPEC)
    h.insert(rec(0, 1500))
    h.insert(rec(1, 1500))                                  # 2 por bloco: o 3º abre bloco novo
    cold(bp)
    sum(1 for _ in g.scan())                                # área DATA cheia de páginas de g
    r = h.insert(rec(2, 1500))
    assert r.block == h._tail
    assert h.fetch(r) == rec(2, 1500)
    assert bp.pinned() == 0
    bp.flush_all()
    fm.close_all()


def test_empty_record_is_rejected_before_any_page(bp):
    h = HeapFile.create(bp, "z", Schema("z", ()), SPEC)
    blocks = bp.fm.n_blocks(h.fid)
    with pytest.raises(ValueError):
        h.insert(())
    t = TempHeap(bp, "__tmp_z", Schema("z", ()))
    with pytest.raises(ValueError):
        t.append(())
    t.drop()
    assert bp.pinned() == 0 and bp.fm.n_blocks(h.fid) == blocks


def test_open_of_empty_file_closes_it(bp):
    bp.fm.close(bp.fm.create("vazio"))
    before = {f for f in range(1, 100) if bp.fm.is_open(f)}
    with pytest.raises(ValueError):
        HeapFile.open(bp, "vazio", S, SPEC)
    assert {f for f in range(1, 100) if bp.fm.is_open(f)} == before


def test_interrupted_bulk_load_keeps_a_tail(bp, monkeypatch):
    h = HeapFile.create(bp, "h", S, SPEC)
    real, calls = bp.new_page, []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) == 3:
            raise OSError("disco cheio")
        return real(*a, **k)
    monkeypatch.setattr(bp, "new_page", flaky)
    with pytest.raises(OSError):
        h.bulk_load(rec(i, 1500) for i in range(10))
    monkeypatch.undo()
    blocks = h.stats()["blocks"]
    h.insert(rec(99, 10))                                   # cabe na sobra do último bloco
    assert h.stats()["blocks"] == blocks


# ---------- gravação forçada (D-P2-13, usada pelo catálogo) ----------
def test_force_writes_only_dirty_pages_now(bp):
    h = HeapFile.create(bp, "h", S, SPEC)
    bp.flush_all()
    w0, f0 = bp.fm.io.writes, bp.stats.forced_writes
    rid = h.insert(rec(1))
    h.force([rid.block])
    # o bloco de dados e a meta ficaram sujos com o insert; o mapa não mudou (insert na cauda)
    assert bp.stats.forced_writes - f0 == bp.fm.io.writes - w0 == 2
    h.force([rid.block])                                 # tudo limpo: nada a gravar
    assert bp.stats.forced_writes - f0 == 2
    h.close()


def test_force_includes_the_free_space_map(bp):
    h = HeapFile.create(bp, "h", S, SPEC)
    rids = [h.insert(rec(i, 1500)) for i in range(6)]       # 2 por bloco: 3 blocos de dados
    bp.flush_all()
    f0 = bp.stats.forced_writes
    h.delete(rids[0])                                        # bloco que não é a cauda: o mapa muda
    h.force([rids[0].block])
    assert bp.stats.forced_writes - f0 == 3                  # o bloco, a meta e o mapa
    h.close()


def test_reorganize_with_an_open_iterator_changes_nothing(bp):
    # Revisor de corretude da revisão do PR #102 (26/11): com uma página do heap fixada, o drop_file recusa
    # antes de descartar nada. O original está inteiro, então o temporário não pode ficar no disco, e a nota
    # "falhou depois de apagar o original" seria falsa.
    h = HeapFile.create(bp, "h", S, SPEC)
    for i in range(50):
        h.insert(rec(i, 300))
    it = h.scan()
    next(it)                                               # página de dados fixada
    with pytest.raises(RuntimeError) as err:
        h.reorganize()
    it.close()
    assert not bp.fm.exists("h__reorg0")
    assert not getattr(err.value, "__notes__", None)
    assert sorted(r for _, r in h.scan()) == [rec(i, 300) for i in range(50)]


def test_heap_fetch_and_delete_at_the_slot_boundaries(bp):
    # Revisor de testes (26/11): slots -1 e 1 com n_slots = 1, vizinhos da faixa válida do _view: KeyError.
    h = HeapFile.create(bp, "h", S, SPEC)
    p = h.insert(rec(1, 10))
    for bad in (RID(p.block, -1), RID(p.block, 1)):
        with pytest.raises(KeyError):
            h.fetch(bad)
        with pytest.raises(KeyError):
            h.delete(bad)
    assert h.fetch(p) == rec(1, 10)


# ---------- issue #105 (revisão do P6 dos PRs #99 a #102) ----------

def test_force_writes_the_data_before_the_map_and_the_meta(bp, monkeypatch):
    # #105, item 2 (D-P2-13: detalhes primeiro, ponteiro por último): os blocos de dados vão para o disco antes
    # do mapa (bloco 2) e da meta (bloco 1). A versão anterior gravava em ordem crescente: 1, 2, 3.
    h = HeapFile.create(bp, "h", S, SPEC)
    rid = h.insert(rec(1))
    order = []
    real = bp.force
    monkeypatch.setattr(bp, "force", lambda p: (order.append(p.bno), real(p))[1])
    h.force([rid.block])
    assert order == [3, 2, 1]


def test_temp_keeps_no_list_that_grows_with_the_blocks(bp):
    # #105, item 3: o temporário guardava um int por bloco (sempre 1, 2, ..., n), memória Python fora das
    # M páginas (AGENTS §0, regra 6). Agora é um contador: nenhum atributo é uma coleção.
    t = TempHeap(bp, "tmp", S)
    for i in range(200):
        t.append(rec(i, 900))                                  # 4 por bloco: 50 blocos
    t.finish()
    assert t.n_blocks() == 50
    assert not any(isinstance(v, (list, set, dict, tuple)) for v in vars(t).values())
    assert [r[0] for r in t.scan()] == list(range(200))
    t.drop()


# ---------- #83, D-P2-19: prefetch só nos scans completos ----------

def prefetch_pool(root, k=8):
    fm = FileManager(StorageConfig(root_dir=str(root)))
    return BufferPool(fm, BufferConfig(prefetch=k))


def close_pool(p):
    assert p.pinned() == 0, "pin sem unpin"
    p.flush_all()
    p.fm.close_all()


def test_scan_prefetch_keeps_reads_and_cuts_misses(tmp_path):
    # Critério da #83: com prefetch = 8, o scan lê os mesmos b blocos (1 seek) que com prefetch = 0, mas
    # cada miss traz o bloco pedido e os 8 seguintes: misses = ceil(b / 9) e prefetched = b − misses.
    # Os dados vão do bloco 3 ao último bloco do arquivo (b < 4096: o mapa é só o bloco 2), então o
    # prefetch nunca lê além do que o scan pede.
    out = {}
    for k in (0, 8):
        p = prefetch_pool(tmp_path / str(k), k)
        h = HeapFile.create(p, "h", S, SPEC)
        h.bulk_load([rec(i, 200) for i in range(3000)])
        b = h.stats()["blocks"] - h.stats()["meta_blocks"]
        cold(p)
        assert sum(1 for _ in h.scan()) == 3000
        out[k] = (p.fm.io.reads, p.fm.io.seeks, p.stats.misses, p.stats.prefetched)
        close_pool(p)
    assert b > 128
    assert out[0] == (b, 1, b, 0)
    m = math.ceil(b / 9)
    assert out[8] == (b, 1, m, b - m)


def test_reopened_heap_also_prefetches(tmp_path):
    # O allow_prefetch fica no construtor: vale para create e para open.
    p = prefetch_pool(tmp_path)
    h = HeapFile.create(p, "h", S, SPEC)
    h.bulk_load([rec(i, 200) for i in range(300)])
    h.close()
    h = HeapFile.open(p, "h", S, SPEC)
    cold(p)
    assert sum(1 for _ in h.scan()) == 300
    b = h.stats()["blocks"] - h.stats()["meta_blocks"]
    m = math.ceil(b / 9)
    assert b > 9 and (p.fm.io.reads, p.stats.misses, p.stats.prefetched) == (b, m, b - m)
    h.close()
    close_pool(p)


def test_prefetch_cuts_seeks_when_another_file_reads_in_between(tmp_path):
    # O efeito que o P6 mede no INL (D-P6-10): entre dois blocos do scan, 1 leitura de outro arquivo.
    # O outro arquivo é lido de trás para frente, para nenhuma das leituras dele ser sequencial.
    # k = 0: cada leitura muda de arquivo = 1 seek: 2b seeks. k = 8: o scan só faz seek nos seus
    # ceil(b / 9) misses (os 8 blocos pré-lidos vêm em sequência), mais os b do outro arquivo.
    seeks = {}
    for k in (0, 8):
        p = prefetch_pool(tmp_path / str(k), k)
        h = HeapFile.create(p, "h", S, SPEC)
        h.bulk_load([rec(i, 200) for i in range(3000)])
        o = HeapFile.create(p, "o", S, SPEC)
        o.bulk_load([rec(i, 200) for i in range(3000)])
        first = {}
        for rid, _ in o.scan():
            first.setdefault(rid.block, rid)
        far = [first[bno] for bno in sorted(first, reverse=True)]
        b = h.stats()["blocks"] - h.stats()["meta_blocks"]
        cold(p)
        last = None
        for rid, _ in h.scan():
            if rid.block != last:
                last = rid.block
                o.fetch(far.pop(0))
        seeks[k] = p.fm.io.seeks
        assert p.fm.io.reads == 2 * b
        close_pool(p)
    assert seeks == {0: 2 * b, 8: math.ceil(b / 9) + b}


def test_scan_stopped_halfway_releases_the_prefetched_pages(tmp_path):
    # O cursor segura até 1 + k páginas; um scan largado no meio (break ou close) solta todas.
    p = prefetch_pool(tmp_path)
    h = HeapFile.create(p, "h", S, SPEC)
    h.bulk_load([rec(i, 200) for i in range(300)])
    cold(p)
    g = h.scan()
    next(g)
    assert p.pinned() == 9                                  # a página atual + as 8 pré-lidas
    g.close()
    assert p.pinned() == 0
    for _ in h.scan():
        break
    close_pool(p)


def test_fetch_and_temp_do_not_prefetch(tmp_path):
    # Regressão: fetch é 1 bloco (pin fora de cursor nunca pré-lê); o TempHeap mora na área WORK, onde o P1
    # proíbe prefetch (um allow_prefetch em WORK levantaria ValueError antes da asserção).
    p = prefetch_pool(tmp_path)
    h = HeapFile.create(p, "h", S, SPEC)
    rids = [h.insert(rec(i, 200)) for i in range(300)]
    t = TempHeap(p, "__tmp_pf", S)
    for i in range(300):
        t.append(rec(i, 200))
    t.finish()
    cold(p)
    assert h.fetch(rids[150]) == rec(150, 200)
    assert [r[0] for r in t.scan()] == list(range(300))
    assert p.stats.prefetched == 0 and p.fm.io.reads == 1 + t.n_blocks()
    t.drop()
    close_pool(p)


def test_reorganized_heap_still_prefetches(tmp_path):
    # O reorganize recria o arquivo com outro fid (D-P2-11): o allow_prefetch precisa ser refeito nele.
    p = prefetch_pool(tmp_path)
    h = HeapFile.create(p, "h", S, SPEC)
    rids = [h.insert(rec(i, 200)) for i in range(1200)]
    for r in rids[::2]:
        h.delete(r)
    old = h.fid
    h.reorganize()
    assert h.fid != old
    cold(p)
    assert sum(1 for _ in h.scan()) == 600
    b = h.stats()["blocks"] - h.stats()["meta_blocks"]
    m = math.ceil(b / 9)
    assert b > 9 and (p.fm.io.reads, p.stats.misses, p.stats.prefetched) == (b, m, b - m)
    close_pool(p)


# ---------- revisão independente da #83 (26/11) ----------

class SmallMapHeap(HeapFile):
    _FSM_PER_PAGE = 8                                       # 1 página do mapa a cada 8 blocos: mapa entre os dados


def test_prefetch_does_not_read_the_map_pages(tmp_path):
    # Defeito: o P1 pré-lê os k blocos seguintes do arquivo, sejam de dados ou do mapa. Com o mapa entre as
    # faixas (carga por insert) ou depois dos dados (bulk_load acima de P blocos), o scan lia páginas do mapa
    # que ninguém pediu (32 blocos de dados: 36 leituras). Correção: os últimos k blocos de uma faixa seguida
    # por outro bloco do arquivo são lidos sem prefetch, então nenhuma pré-leitura passa do fim da faixa.
    for how in ("bulk", "insert"):
        out = {}
        for k in (0, 8):
            p = prefetch_pool(tmp_path / f"{how}{k}", k)
            h = SmallMapHeap.create(p, "h", S, SPEC)
            recs = [rec(i, 200) for i in range(600)]
            if how == "bulk":
                h.bulk_load(recs)
            else:
                for r in recs:
                    h.insert(r)
            b = h.stats()["blocks"] - h.stats()["meta_blocks"]
            assert len(h._fsm) > 1
            cold(p)
            assert sum(1 for _ in h.scan()) == 600
            out[k] = (p.fm.io.reads, p.stats.misses + p.stats.prefetched)
            close_pool(p)
        assert out[0] == (b, b) and out[8] == (b, b), (how, out)


def test_failed_reorganize_releases_the_scan_cursor(tmp_path, monkeypatch):
    # Guarda (achado descartado da revisão da #83): uma falha ao copiar para o temporário poderia deixar o
    # iterador do scan aberto enquanto o traceback existisse, com até 1 + k = 9 páginas fixadas. Não acontece:
    # ao sair do for pela exceção, o CPython tira o iterador da pilha do frame e o gerador fecha na hora. Se
    # alguém mudar o _rebuild para guardar o iterador numa variável, este teste pega.
    p = prefetch_pool(tmp_path)
    h = HeapFile.create(p, "h", S, SPEC)
    h.bulk_load([rec(i, 200) for i in range(300)])
    cold(p)
    calls = []

    def boom(self, r):
        calls.append(r)
        if len(calls) == 50:
            raise OSError("disco cheio")
    monkeypatch.setattr(TempHeap, "append", boom)
    with pytest.raises(OSError) as info:
        h.reorganize()
    assert p.pinned() == 0, info
    monkeypatch.undo()
    assert sum(1 for _ in h.scan()) == 300
    close_pool(p)
