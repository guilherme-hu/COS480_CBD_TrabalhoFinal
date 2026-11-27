"""Integração da P4 com as organizações físicas disponíveis e SQLite."""

from contextlib import closing

import pytest

from bench.build import load_physical, physical_plan
from bench.generator import Dataset
from bench.matrix import join_experiments, select_experiments
from bench.run import measure
from bench.schema import ALUNO, PHYSICAL_TABLES, SCHEMAS
from bench.sqlite_ref import SQLiteReference, from_sql, query_sql
from bench.workload import prepare_workload
from cbd.contracts import IndexKind, IndexSpec, Org, OrgSpec, Path, Schema
from cbd.exec.selection import select_eq, select_eq_cost


@pytest.fixture
def aluno_physical(eng):
    dataset = Dataset(.002)
    with SQLiteReference(":memory:") as reference:
        reference.create_schema()
        for name in ("curso", "aluno"):
            reference.load(name, dataset.records(name))

        variants = {
            "aluno__heap": OrgSpec(Org.HEAP),
            "aluno__hs_dre": OrgSpec(Org.HASH, "dre", True, {"buckets": 8}),
            "aluno__hs_cr": OrgSpec(Org.HASH, "cr", False, {"buckets": 8}),
        }
        for name, spec in variants.items():
            eng.catalog.create_table(Schema(name, ALUNO.fields), spec)
            eng.catalog.load_table(name, reference.records("aluno"))
        eng.catalog.create_index(IndexSpec("aluno__heap", "dre", IndexKind.HASH, True, {"buckets": 1}))
        yield reference, prepare_workload(reference), variants


def test_p4_heap_and_primary_hash_selections_through_the_bench(eng, aluno_physical):
    reference, workload, variants = aluno_physical
    queries = {query["id"]: query for query in workload}
    results = []
    for experiment in select_experiments(workload):
        side = experiment.args["table"]
        if side.variant not in variants or side.path is Path.SECONDARY:
            continue
        expected = reference.result(queries[experiment.query_id])
        row = measure(eng, experiment, expected=expected)
        results.append(row)
        if side.path is Path.PRIMARY and experiment.op == "select_range":
            assert row["status"] == "N/A"
        else:
            assert row["status"] == "ok", row["note"]
            assert row["valid"] is True
            assert row["result_rows"] == expected[0]
            assert row["io.writes"] == 0
            if side.path is Path.PRIMARY:
                assert row["io.reads"] == row["est.reads"] == 1  # Um bucket sem overflow.
            elif experiment.op == "select_range":
                assert row["io.reads"] == row["est.reads"] == row["r_blocks"]
            else:
                # O modelo é a média, e a versão real para ao achar o DRE.
                assert row["est.reads"] == row["r_blocks"] / 2
                assert 1 <= row["io.reads"] <= row["r_blocks"]
        assert eng.bp.pinned() == 0

    # 20 DREs em heap e hash, duas faixas em heap e duas N/A em hash.
    assert len(results) == 44
    assert sum(row["status"] == "ok" for row in results) == 42


def test_p4_secondary_hash_selection_against_sqlite(eng, aluno_physical):
    reference, workload, _ = aluno_physical
    heap = eng.catalog.table("aluno__heap")
    for query in (item for item in workload if item.get("sel_type") == "eq_unique"):
        sql, params = query_sql(query)
        with closing(reference.conn.execute(sql, params)) as cursor:
            expected = [from_sql(ALUNO, row) for row in cursor]
        estimated = select_eq_cost(eng, heap, "dre", query["value"], Path.SECONDARY)
        for sort_ptrs in (False, True):
            eng.cold()
            result = list(select_eq(eng, heap, "dre", query["value"], Path.SECONDARY, sort_ptrs))
            metrics = eng.snapshot()
            assert result == expected
            # 100 entradas de tamanho fixo cabem em um bucket de índice.
            # A folha do heap é buscada pelo RID retornado pelo índice.
            assert metrics["io.reads"] == estimated.reads == 2
            assert metrics["io.writes"] == 0
            assert eng.bp.pinned() == 0


def test_p4_nested_loop_through_the_bench_with_available_storage(eng):
    dataset = Dataset(.002)
    with SQLiteReference(":memory:") as reference:
        reference.create_schema()
        for name in SCHEMAS:
            reference.load(name, dataset.records(name))
        workload = {query["id"]: query for query in prepare_workload(reference)}
        table_stats = {name: reference.stats(name) for name in PHYSICAL_TABLES}
        plan = [dict(item, indexes=[ix for ix in item["indexes"] if ix["kind"] is IndexKind.HASH])
                for item in physical_plan(table_stats) if item["org"]["org"] is not Org.BTREE]
        load_physical(eng, reference, plan)
        tables = {item["name"] for item in plan}

        covered = set()
        runs = 0
        for experiment in join_experiments():
            if experiment.op != "nl_join":
                continue
            r, s = (experiment.args[name] for name in ("r", "s"))
            if any(side.variant not in tables for side in (r, s)):
                continue
            if any(side.path is Path.SECONDARY and
                   side.column not in eng.catalog.table(side.variant).indexes for side in (r, s)):
                continue
            expected = reference.result(workload[experiment.query_id])
            row = measure(eng, experiment, expected=expected)
            assert row["status"] == "ok", row["note"]
            assert row["valid"] is True
            assert row["result_rows"] == expected[0]
            assert row["variant"] == ("block" if experiment.config == "a" else "indexed")
            assert row["io.reads"] > 0 and row["io.writes"] == 0
            if experiment.config == "a":
                # Na base pequena o externo cabe no lote, com um scan por lado.
                assert row["io.reads"] == row["est.reads"] == row["r_blocks"] + row["s_blocks"]
            assert eng.bp.pinned() == 0
            covered.add((experiment.query_id, experiment.config))
            runs += 1

    assert runs == 20  # Três consultas e duas orientações forçadas na linha a.
    assert covered == {(query_id, config) for query_id in workload if query_id.startswith("join_")
                       for config in "abcd"}
