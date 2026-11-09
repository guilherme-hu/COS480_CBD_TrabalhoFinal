"""Testes das figuras (P6 §9, issue #23) com um summary.csv SINTÉTICO.

Os números são aleatórios (seed fixa) só para exercitar a extração e o desenho;
não são medições e nunca vão para o relatório.
"""
import csv
import random

import pytest

pytest.importorskip('matplotlib')

from bench.matrix import join_experiments, select_experiments  # noqa: E402
from bench.plots import join_series, plot_all, select_series  # noqa: E402
from bench.report import _schematic_workload  # noqa: E402


def synthetic_rows(seed=480):
    rng = random.Random(seed)
    rows = []
    for e in join_experiments() + select_experiments(_schematic_workload()):
        na = e.exp_id.endswith('c_hash_eq.sort_merge_join')   # finge um N/A
        measured, est = rng.randint(10, 5000), rng.randint(10, 5000)
        rows.append({'exp_id': e.exp_id, 'status': 'N/A' if na else 'ok', 'sel_type': e.sel_type,
                     'io.reads': '' if na else measured, 'io.writes': '' if na else 5,
                     'est.reads': '' if na else est, 'est.writes': '' if na else 5})
    return rows


def write_summary(path, rows):
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows({k: str(v) for k, v in r.items()} for r in rows)


def test_join_series_takes_the_13_configs_and_marks_na():
    rows = [{k: str(v) for k, v in r.items()} for r in synthetic_rows()]
    series = join_series(rows, 'small_large')
    assert len(series) == 13                      # linhas outer_*/build_* ficam fora
    assert series['c_hash_eq']['sort_merge_join'] == 'N/A'
    row = next(r for r in rows if r['exp_id'] == 'join.small_large.a.hash_join')
    assert series['a']['hash_join'] == (float(row['io.reads']) + 5, float(row['est.reads']) + 5)


def test_select_series_averages_the_20_dres():
    rows = [{k: str(v) for k, v in r.items()} for r in synthetic_rows()]
    eq, rng = select_series(rows)
    scan = [float(r['io.reads']) + 5 for r in rows
            if r['exp_id'].startswith('select.eq_dre_') and r['exp_id'].endswith('.a_scan')]
    assert len(scan) == 20 and eq['a_scan'][0] == pytest.approx(sum(scan) / 20)
    assert set(rng['c_secondary_on_btree_sorted']) == {'0.01', '0.5'}


def test_one_command_writes_every_figure(tmp_path):
    write_summary(tmp_path / 'summary.csv', synthetic_rows())
    made = plot_all(tmp_path / 'summary.csv', tmp_path / 'figs')
    assert sorted(p.name for p in made) == ['joins_large_large.png', 'joins_small_large.png',
                                            'joins_small_small.png', 'selecoes.png']
    assert all(p.read_bytes().startswith(b'\x89PNG') for p in made)
