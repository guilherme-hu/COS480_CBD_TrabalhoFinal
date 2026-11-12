"""Testes da matriz de experimentos (P6 §4, issues #9 e #10)."""
from collections import Counter

import pytest

from bench.build import physical_plan
from bench.matrix import JOIN_ALGORITHMS, join_configs, join_experiments
from bench.schema import ACCESS_COLUMNS, SCHEMAS
from bench.workload import JOINS
from cbd.contracts import PageType, Path


@pytest.fixture(scope='module')
def plan_by_name():
    # Estatísticas mínimas só para gerar os nomes e specs do plano (sem base real).
    stats = {t: dict(n=100, estimated_bfr=10,
                     distinct={c: 10 for c in ACCESS_COLUMNS.get(t, ())}) for t in SCHEMAS}
    return {item['name']: item for item in physical_plan(stats)}


def test_join_matrix_size():
    exps = join_experiments()
    regular = [e for e in exps if 'outer' not in e.args and 'build' not in e.args]
    # 3 combinações × 13 configurações físicas × 3 algoritmos (P6 §4.2).
    assert len(regular) == 3 * 13 * 3 == 117
    forced = [e for e in exps if e not in regular]
    # menor ⋈ maior, config a: nl_join com outer r|s e hash_join com build r|s.
    assert sorted((e.op, e.args.get('outer') or e.args.get('build')) for e in forced) == [
        ('hash_join', 'r'), ('hash_join', 's'), ('nl_join', 'r'), ('nl_join', 's')]
    assert {e.size_combo for e in forced} == {'small_large'} and {e.config for e in forced} == {'a'}


def test_exp_ids_unique_and_stable():
    ids = [e.exp_id for e in join_experiments()]
    assert len(ids) == len(set(ids))
    assert ids == [e.exp_id for e in join_experiments()]


def test_config_counts_per_join():
    for combo, *rest in JOINS:
        counts = Counter(config for _, config, _, _ in join_configs(combo, *rest))
        assert counts == {'a': 1, 'b': 4, 'c': 3, 'd': 4, 'e': 1}


@pytest.mark.parametrize('combo,r,rc,s,sc', JOINS)
def test_config_paths_match_architecture(combo, r, rc, s, sc):
    """Arquitetura §7.4: a=SCAN/SCAN, b=PRIMARY/SCAN, c=PRIMARY/PRIMARY,
    d=PRIMARY/SECONDARY, e=SECONDARY/SECONDARY — b e d nas duas orientações."""
    expected = {'a': {(Path.SCAN, Path.SCAN)},
                'b': {(Path.PRIMARY, Path.SCAN), (Path.SCAN, Path.PRIMARY)},
                'c': {(Path.PRIMARY, Path.PRIMARY)},
                'd': {(Path.PRIMARY, Path.SECONDARY), (Path.SECONDARY, Path.PRIMARY)},
                'e': {(Path.SECONDARY, Path.SECONDARY)}}
    seen = {}
    for label, config, rs, ss in join_configs(combo, r, rc, s, sc):
        assert (rs.table, rs.column, ss.table, ss.column) == (r, rc, s, sc)  # r = mestre sempre
        seen.setdefault(config, set()).add((rs.path, ss.path))
        if config in ('b', 'd'):
            organized = rs if rs.path == Path.PRIMARY else ss
            assert label.split('_')[1] == ('r' if organized is rs else 's')
    assert seen == expected
    c_orgs = sorted((rs.org, ss.org) for _, cfg, rs, ss in join_configs(combo, r, rc, s, sc) if cfg == 'c')
    assert c_orgs == [('BTREE', 'BTREE'), ('HASH', 'HASH'), ('HASH_EQ', 'HASH_EQ')]


def test_every_side_exists_in_physical_plan(plan_by_name):
    for exp in join_experiments():
        for side in (exp.args['r'], exp.args['s']):
            item = plan_by_name[side.variant]           # KeyError = variante inexistente
            assert item['source'] == side.table
            org = item['org']
            if side.path == Path.PRIMARY:
                assert org['key'] == side.column
                assert org['org'] == ('HASH' if side.org == 'HASH_EQ' else side.org)
            elif side.path == Path.SECONDARY:
                assert org['org'] == 'HEAP'
                assert side.column in {i['column'] for i in item['indexes']}
            else:
                assert org['org'] == 'HEAP'


def test_bucket_join_pairs_share_n_and_seed(plan_by_name):
    for exp in join_experiments():
        r, s = exp.args['r'], exp.args['s']
        if r.org == 'HASH_EQ':
            pr, ps = plan_by_name[r.variant]['org']['params'], plan_by_name[s.variant]['org']['params']
            assert (pr['buckets'], pr['seed']) == (ps['buckets'], ps['seed'])
        elif (r.org, s.org) == ('HASH', 'HASH'):
            # c_hash = caso "sem bucket-join": seeds diferentes entre as tabelas.
            assert plan_by_name[r.variant]['org']['params']['seed'] != plan_by_name[s.variant]['org']['params']['seed']


def test_operators_referenced_by_name_only():
    # A matriz não importa cbd.exec: gerar a lista não depende de P4/P5 existirem.
    import bench.matrix as matrix
    assert 'cbd.exec' not in open(matrix.__file__).read()
    for exp in join_experiments():
        assert exp.cost_fn == JOIN_ALGORITHMS[exp.op]
        assert exp.query_id == f'join_{exp.size_combo}'


@pytest.fixture(scope='module')
def workload(tmp_path_factory):
    import json
    from bench.build import prepare
    from bench.generator import Dataset
    folder = tmp_path_factory.mktemp('wl')
    prepare(folder, Dataset(.002))
    return json.loads((folder / 'workload.json').read_text())


def test_select_matrix_counts(workload):
    from bench.matrix import select_experiments
    exps = select_experiments(workload)
    eq = [e for e in exps if e.sel_type == 'eq_unique']
    rng = [e for e in exps if e.sel_type == 'range_nonunique']
    # 20 DREs × (SCAN, B+, hash, SECONDARY).
    assert len(eq) == 20 * 4
    # 2 seletividades × (SCAN, B+, hash e SECONDARY sobre heap, B+ e hash, cada
    # SECONDARY sem e com sort_ptrs). Enunciado §4.2: o B+ secundário "clustering"
    # em campo NOT UNIQUE também é método de acesso de B+ e hash primários.
    assert len(rng) == 2 * 9
    assert Counter((e.config, e.args['table'].org) for e in rng) == {
        ('a', 'HEAP'): 2, ('b', 'BTREE'): 2, ('b', 'HASH'): 2,
        ('c', 'HEAP'): 4, ('c', 'BTREE'): 4, ('c', 'HASH'): 4}
    assert sorted(e.sort_ptrs for e in rng if e.config == 'c') == [False] * 6 + [True] * 6
    assert len({e.exp_id for e in exps}) == len(exps)


def test_select_values_come_from_workload(workload, plan_by_name):
    from bench.matrix import select_experiments
    queries = {q['id']: q for q in workload}
    for e in select_experiments(workload):
        q = queries[e.query_id]
        assert e.selectivity == q['selectivity']
        if e.op == 'select_eq':
            assert e.args['value'] == q['value']
        else:
            bound = e.args['lo'] if q['operator'] == '>=' else e.args['hi']
            other = e.args['hi'] if q['operator'] == '>=' else e.args['lo']
            assert (bound, other) == (q['value'], None)
        side = e.args['table']
        item = plan_by_name[side.variant]
        assert e.args['path'] == side.path
        if side.path == Path.PRIMARY:
            assert item['org']['key'] == side.column
        if side.path == Path.SECONDARY:
            index = next(i for i in item['indexes'] if i['column'] == side.column)
            if side.org != 'HEAP':
                # Sobre B+/hash primário, só B+ NOT UNIQUE (arquitetura §5.3).
                assert (index['kind'], index['unique']) == ('BTREE', False)


def test_block_size_keeps_buffer_bytes_constant():
    # D-P6-02: no experimento de bloco a memória em BYTES é a mesma; só o número
    # de páginas de cada área muda (DATA = 512 KiB: 256/128/64 páginas).
    from bench.matrix import BASE_AREAS, areas_for_block
    assert areas_for_block(4096) == BASE_AREAS
    for b in (2048, 4096, 8192):
        areas = areas_for_block(b)
        assert {k: v * b for k, v in areas.items()} == {k: v * 4096 for k, v in BASE_AREAS.items()}
    assert areas_for_block(8192)[PageType.WORK] == 32 and areas_for_block(2048)[PageType.DATA] == 256
    # WORK passado em páginas de 4096: 16 em B = 4096, 128 em B = 8192 (256 × 4096 B).
    assert areas_for_block(4096, work=16)[PageType.WORK] == 16
    assert areas_for_block(8192, work=256)[PageType.WORK] == 128
    # Bloco que zeraria uma área ou não divide a memória: erro, não arredondamento.
    for bad in (65536, 3000):
        with pytest.raises(ValueError):
            areas_for_block(bad)
