"""Testes da revisão final da P2 (#62): cada um mata um mutante que sobrevivia à suíte da P2.

Escritos pelo revisor independente de qualidade dos testes (123 mutantes plantados num por vez, numa cópia do
repositório; 104 mortos, 7 equivalentes, 12 lacunas reais) e conferidos nos dois lados: passam no código e falham
com o mutante. Cada comentário diz o mutante e a conta esperada. Ver ai/P2/review.md.
"""
import random

from cbd.contracts import BufferConfig, Field, FType, Org, OrgSpec, Schema, StorageConfig
from cbd.files.hash_file import HashFile
from cbd.records.slotted_page import SlottedPage
from cbd.storage.buffer_pool import BufferPool
from cbd.storage.file_manager import FileManager


def test_next_survives_insert_at_and_remove_at():
    # P3: a folha já encadeada recebe insert_at/remove_at; o next não pode mudar.
    sp = SlottedPage(bytearray(4096))
    sp.init()
    sp.next = 0xA1B2C3D4
    r = random.Random(1)
    n = 0
    for _ in range(500):
        if r.random() < .6 and sp.insert_at(r.randint(0, n), b"x" * r.randint(1, 200)):
            n += 1
        elif n:
            sp.remove_at(r.randrange(n))
            n -= 1
        assert sp.next == 0xA1B2C3D4


def test_scan_bucket_unpins_with_toss(tmp_path, monkeypatch):
    p = BufferPool(FileManager(StorageConfig(root_dir=str(tmp_path))), BufferConfig())
    S = Schema("t", (Field("k", FType.INT), Field("v", FType.VARCHAR, 3000)))
    h = HashFile.create(p, "h", S, OrgSpec(Org.HASH, "k", False, {"buckets": 2}))
    h.bulk_load([(i % 2, "x" * 1000) for i in range(40)])
    tosses, unpin = [], p.unpin
    monkeypatch.setattr(p, "unpin", lambda page, dirty=False, toss=False: (tosses.append(toss),
                                                                          unpin(page, dirty=dirty, toss=toss))[1])
    assert len(list(h.scan_bucket(0))) == 20
    monkeypatch.undo()
    assert tosses and all(tosses)
    p.flush_all()
    p.fm.close_all()


def test_bulk_load_refuses_heap_with_only_deleted_records(bp):
    import pytest
    from cbd.files.heap import HeapFile
    S = Schema("t", (Field("k", FType.INT), Field("v", FType.VARCHAR, 3000)))
    h = HeapFile.create(bp, "h", S, OrgSpec(Org.HEAP))
    h.delete(h.insert((1, "a")))
    with pytest.raises(ValueError):
        h.bulk_load([(2, "b")])


def test_float_column_found_by_int_key_several_keys(bp):
    # Com uma chave só (5) o hash_value(5) e o hash_value(5.0) caem no mesmo bucket mod 7 por acaso.
    f = HashFile.create(bp, "f", Schema("f", (Field("k", FType.FLOAT), Field("v", FType.VARCHAR, 10))),
                        OrgSpec(Org.HASH, "k", True, {"buckets": 7, "seed": 3}))
    for k in range(1, 9):
        f.insert((float(k), "a"))
    assert all([r for _, r in f.search(k)] == [(float(k), "a")] for k in range(1, 9))


def test_hash_bulk_load_refuses_file_with_chains_but_no_records(bp):
    import pytest
    S = Schema("t", (Field("k", FType.INT), Field("v", FType.VARCHAR, 3000)))
    h = HashFile.create(bp, "h", S, OrgSpec(Org.HASH, "k", True, {"buckets": 1}))
    ptrs = [h.insert((i, "x" * 1000)) for i in range(5)]      # 4 por bloco: 1 overflow
    for p in ptrs:
        h.delete(p)
    with pytest.raises(ValueError):
        h.bulk_load([(9, "y")])


def test_insert_writes_only_the_block_it_changes(bp):
    # N = 1, 10 registros de 1 006 B (4 por bloco): cadeia 2 (cheio), 3 (cheio), 4 (2 registros).
    # Cache frio, insert NOT UNIQUE: lê 2, 3, 4 e a meta; grava só o 4 e a meta.
    S = Schema("t", (Field("k", FType.INT), Field("v", FType.VARCHAR, 3000)))
    h = HashFile.create(bp, "h", S, OrgSpec(Org.HASH, "k", False, {"buckets": 1}))
    for i in range(10):
        h.insert((i, "x" * 1000))
    bp.clear()
    bp.fm.reset_stats()
    bp.reset_stats()
    assert h.insert((99, "x" * 1000)).block == 4
    bp.flush_all()
    assert (bp.fm.io.reads, bp.fm.io.writes) == (4, 2)


def test_heap_insert_that_misses_the_tail_does_not_write_it(bp):
    # Cauda com 2 registros de 1 506 B (cabe só 2 por bloco). Cache frio; o 3º não cabe na cauda e abre bloco
    # novo. Grava: o bloco novo, a página do mapa (entrada da cauda antiga) e a meta = 3; a cauda não muda.
    from cbd.files.heap import HeapFile
    S = Schema("t", (Field("k", FType.INT), Field("v", FType.VARCHAR, 3000)))
    h = HeapFile.create(bp, "h", S, OrgSpec(Org.HEAP))
    tail = h.insert((0, "x" * 1500)).block
    h.insert((1, "x" * 1500))
    bp.clear()
    bp.fm.reset_stats()
    bp.reset_stats()
    assert h.insert((2, "x" * 1500)).block != tail
    bp.flush_all()
    assert bp.fm.io.writes == 3


def test_analyze_skips_min_max_of_char_longer_than_255(tmp_path):
    from cbd.engine import Engine
    e = Engine(StorageConfig(root_dir=str(tmp_path)), BufferConfig())
    S = Schema("w", (Field("k", FType.INT), Field("c", FType.CHAR, 300)))
    e.catalog.create_table(S, OrgSpec(Org.HEAP))
    e.catalog.load_table("w", [(1, "a" * 300), (2, "b" * 300)])
    st = e.catalog.table("w").stats()
    assert st.min == {"k": 1} and st.distinct["c"] == 2
    e.close()


def test_char_with_leading_nul_is_corrupt():
    import pytest
    from cbd.records.codec import decode_key
    with pytest.raises(ValueError, match="registro corrompido"):
        decode_key(Field("k", FType.CHAR, 3), b"\x00ab")


def test_bfr_is_not_rounded(tmp_path):
    from cbd.engine import Engine
    e = Engine(StorageConfig(root_dir=str(tmp_path)), BufferConfig())
    S = Schema("w", (Field("k", FType.INT), Field("v", FType.VARCHAR, 300)))
    e.catalog.create_table(S, OrgSpec(Org.HEAP))
    e.catalog.load_table("w", [(i, "x" * 150) for i in range(1001)])
    st = e.catalog.table("w").stats()
    assert st.n_records % st.n_blocks != 0 and st.bfr == st.n_records / st.n_blocks
    e.close()
