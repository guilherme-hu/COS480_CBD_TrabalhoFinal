import json
from pathlib import Path
import random
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from bench.build import prepare, load_physical, physical_plan, size_checks
from bench.generator import Dataset, permutation, BASE_COUNTS
from bench.schema import SCHEMAS, PK
from bench.sqlite_ref import SQLiteReference, to_sql, query_sql, output_signature, from_sql
from cbd.contracts import Org, IndexKind
from cbd.records.hashing import hash_value


@pytest.fixture(scope='module')
def prepared(tmp_path_factory):
    folder = tmp_path_factory.mktemp('prepared')
    prepare(folder, Dataset(.002))
    return folder


@pytest.mark.parametrize('n', [1, 2, 3, 4, 17, 100, 1025])
def test_permutation(n):
    values = list(permutation(n, random.Random(480)))
    assert sorted(values) == list(range(n))
    assert values == list(permutation(n, random.Random(480)))
    if n > 10:
        assert values != sorted(values)


@pytest.mark.parametrize('scale', [0, -1, float('nan'), float('inf')])
def test_invalid_scale(scale):
    with pytest.raises(ValueError):
        Dataset(scale)


def test_schema_is_architecture_contract():
    source = Path('docs/implementacao/00_ARQUITETURA_GERAL.md').read_text()
    code = source.split('### 8.3 Base de dados')[1].split('```python\n')[1].split('```')[0]
    namespace = {}
    exec(code, namespace)
    for table in BASE_COUNTS:
        assert namespace[table.upper()] == SCHEMAS[table]


def test_contracts_are_exact_copy():
    source = Path('docs/implementacao/00_ARQUITETURA_GERAL.md').read_text()
    expected = source.split('## 4. Tipos compartilhados')[1].split('```python\n')[1].split('```')[0]
    assert Path('cbd/contracts.py').read_text() == expected


def test_generator_types_uniqueness_and_determinism():
    dataset = Dataset(.002)
    for table in BASE_COUNTS:
        records = list(dataset.records(table))
        assert len(records) == dataset.counts[table]
        assert records == list(dataset.records(table))
        schema = SCHEMAS[table]
        keys = [tuple(row[schema.idx(c)] for c in PK[table]) for row in records]
        assert len(set(keys)) == len(keys)
        for row in records:
            assert from_sql(schema, to_sql(schema, row)) == row
    for scale in (.000001, .00002):
        tiny = Dataset(scale)
        assert len(list(tiny.records('inscricao'))) == tiny.counts['inscricao']


def test_generator_and_hash_stable_across_processes():
    script = '''import hashlib, json
from bench.generator import Dataset
from cbd.records.hashing import hash_value
print(hash_value("abc"))
print(hashlib.sha256(repr(list(Dataset(.002).records("aluno"))).encode()).hexdigest())
'''
    import os
    results = [subprocess.check_output([sys.executable, '-c', script],
               env=dict(os.environ, PYTHONHASHSEED=seed)) for seed in ('1', '99')]
    assert results[0] == results[1]
    assert hash_value(-0.0) == hash_value(0.0)
    assert hash_value('abc', 0) != hash_value('abc', 1)


def test_sqlite_integrity_and_subsets(prepared):
    with SQLiteReference(prepared / 'ref.sqlite') as ref:
        assert list(ref.conn.execute('PRAGMA foreign_key_check')) == []
        assert ref.conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        for table in BASE_COUNTS:
            assert list(ref.records(table)) == list(Dataset(.002).records(table))
        assert {r[1] for r in ref.records('aluno_p')} == {r[0] for r in ref.records('detalhes_p')}
        for table in ('aluno_p', 'turma_p', 'detalhes_p'):
            assert ref.stats(table, 2048)['estimated_blocks'] <= 62
        assert ref.conn.execute('SELECT COUNT(*) FROM aluno WHERE orientador IS NULL').fetchone()[0] > 0
        with pytest.raises(sqlite3.IntegrityError):
            ref.conn.execute('INSERT INTO inscricao VALUES (999999,1,100,NULL)')


def test_all_workload_results_against_python(prepared):
    workload = json.loads((prepared / 'workload.json').read_text())
    assert len([q for q in workload if q['kind'] == 'join']) == 9
    assert len([q for q in workload if q.get('sel_type') == 'eq_unique']) == 20
    assert len([q for q in workload if q.get('sel_type', '').startswith('range')]) == 6
    with SQLiteReference(prepared / 'ref.sqlite') as ref:
        for query in workload:
            r = query['r_table']
            if query['kind'] == 'join':
                s = query['s_table']
                ri, si = SCHEMAS[r].idx(query['r_column']), SCHEMAS[s].idx(query['s_column'])
                # Referência independente só em escala minúscula, nunca durante a medição.
                left, right = list(ref.records(r)), list(ref.records(s))
                output = [a + b for a in left for b in right if a[ri] is not None and a[ri] == b[si]]
            else:
                import operator
                compare = {'=': operator.eq, '<=': operator.le, '>=': operator.ge}[query['operator']]
                index = SCHEMAS[r].idx(query['column'])
                output = [row for row in ref.records(r) if compare(row[index], query['value'])]
                assert query['selectivity'] == len(output) / Dataset(.002).counts['aluno']
            expected = query['expected']
            assert output_signature(query, reversed(output)) == (expected['count'], expected['checksum'])
            sql, params = query_sql(query)
            assert ref.conn.execute('SELECT COUNT(*) FROM (' + sql + ')', params).fetchone()[0] == len(output)


def test_null_join_and_duplicate_multiplicity():
    with SQLiteReference(':memory:') as ref:
        ref.create_schema()
        course = next(Dataset(.002).records('curso'))
        ref.load('curso', [course])
        student = list(next(Dataset(.002).records('aluno')))
        student[0], student[1], student[7], student[9] = 1, '00000000001', course[0], 42
        ref.load('aluno', [tuple(student)])
        ref.load('disciplina', [(1, course[0], 'd', None, 60, ())])
        ref.load('turma', [(1, 1, '2026.2', 'SEG', 'S01', 42), (2, 1, '2026.2', 'SEG', 'S02', 42)])
        query = dict(kind='join', r_table='aluno', s_table='turma', r_column='orientador', s_column='professor')
        assert ref.result(query)[0] == 2
        ref.conn.execute('UPDATE aluno SET orientador=NULL')
        assert ref.result(query) == (0, 0)


def test_repeated_preparation_and_overwrite_guard(prepared, tmp_path):
    prepare(tmp_path, Dataset(.002))
    for name in ('workload.json', 'base_stats.csv', 'manifest.json', 'physical_plan_4096.json'):
        assert (tmp_path / name).read_bytes() == (prepared / name).read_bytes()
    with pytest.raises(FileExistsError):
        prepare(tmp_path, Dataset(.002))
    before = (tmp_path / 'ref.sqlite').read_bytes()
    # Recriação inválida preserva a base anterior.
    with pytest.raises(ValueError):
        prepare(tmp_path, Dataset(.002), work_pages=1, force=True)
    assert (tmp_path / 'ref.sqlite').read_bytes() == before


def test_plan_and_catalog_adapter(prepared):
    class FakeCatalog:
        def __init__(self):
            self.loaded, self.orgs, self.indexes = {}, {}, {}

        def tables(self):
            return list(self.loaded)

        def create_table(self, schema, org):
            self.orgs[schema.name] = org
            self.indexes[schema.name] = {}

        def load_table(self, name, records, bulk=True):
            self.loaded[name] = list(records)

        def create_index(self, spec):
            self.indexes[spec.table][spec.column] = SimpleNamespace(stats=lambda: {'blocks': 1})

        def table(self, name):
            return SimpleNamespace(stats=lambda: SimpleNamespace(n_records=len(self.loaded[name]),
                                   n_blocks=1, bfr=len(self.loaded[name]), distinct={}),
                                   data=SimpleNamespace(stats=lambda: {'blocks': 1}), indexes=self.indexes[name])

    with SQLiteReference(prepared / 'ref.sqlite') as ref:
        stats = {table: ref.stats(table) for table in SCHEMAS}
        plan = physical_plan(stats)
        assert len({v['name'] for v in plan}) == len(plan)
        assert any(not item['bulk'] for item in plan)
        for item in plan:
            for index in item['indexes']:
                if item['org']['org'] == Org.HEAP:
                    assert index['kind'] == (IndexKind.HASH if index['unique'] else IndexKind.BTREE)
                else:
                    assert index['kind'] == IndexKind.BTREE and not index['unique']
                    assert index['column'] != item['org']['key']
        paired = [item for item in plan if '_eq_' in item['name']]
        for r, s in zip(paired[::2], paired[1::2]):
            assert r['org']['params']['seed'] == s['org']['params']['seed']
            assert r['org']['params']['buckets'] == s['org']['params']['buckets']
        eng = SimpleNamespace(catalog=FakeCatalog())
        rows = load_physical(eng, ref, plan)
        for item in plan:
            assert eng.catalog.loaded[item['name']] == list(ref.records(item['source']))
        assert len(rows) == len(plan)
        with pytest.raises(ValueError, match='existentes'):
            load_physical(eng, ref, plan)
        assert any(c['status'] == 'inadequate' for c in size_checks(stats))


def test_row_override():
    dataset = Dataset(1, (('turma', 100000), ('aluno', 100)))
    assert dataset.counts['turma'] == 100000
    assert dataset.counts['detalhes'] == dataset.counts['aluno'] == 100
    with pytest.raises(ValueError):
        Dataset(1, (('detalhes', 2),))
