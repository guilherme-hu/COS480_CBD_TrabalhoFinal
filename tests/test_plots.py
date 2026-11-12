"""Testes das figuras (P6 §9, issue #23) com um summary.csv SINTÉTICO.

Os números são aleatórios (seed fixa) só para exercitar a extração e o desenho;
não são medições e nunca vão para o relatório.
"""
import csv
import random

import pytest

pytest.importorskip('matplotlib')

from bench.matrix import factor_experiments, join_experiments, select_experiments  # noqa: E402
from bench.plots import factor_series, join_series, plot_all, select_series  # noqa: E402
from bench.report import _schematic_workload  # noqa: E402


def synthetic_rows(seed=480):
    rng = random.Random(seed)
    rows = []
    workload = _schematic_workload()
    for e in join_experiments() + select_experiments(workload) + factor_experiments(workload):
        na = e.exp_id.endswith('c_hash_eq.sort_merge_join')   # finge um N/A
        measured, est = rng.randint(10, 5000), rng.randint(10, 5000)
        # io.writes ≠ est.writes: uma troca de prefixo em `blocks` não passa despercebida.
        rows.append({'exp_id': e.exp_id, 'status': 'N/A' if na else 'ok', 'sel_type': e.sel_type,
                     'valid': '' if na else 'True',
                     'io.reads': '' if na else measured, 'io.writes': '' if na else 5,
                     'io.seeks': '' if na else measured // 3,
                     'est.reads': '' if na else est, 'est.writes': '' if na else 7})
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
    assert series['a']['hash_join'] == (float(row['io.reads']) + 5, float(row['est.reads']) + 7)


def test_select_series_averages_the_20_dres():
    rows = [{k: str(v) for k, v in r.items()} for r in synthetic_rows()]
    eq, rng = select_series(rows)
    scan = [float(r['io.reads']) + 5 for r in rows
            if r['exp_id'].startswith('select.eq_dre_') and r['exp_id'].endswith('.a_scan')]
    est = [float(r['est.reads']) + 7 for r in rows
           if r['exp_id'].startswith('select.eq_dre_') and r['exp_id'].endswith('.a_scan')]
    assert len(scan) == 20 and eq['a_scan'] == pytest.approx((sum(scan) / 20, sum(est) / 20))
    assert set(rng['c_secondary_on_btree_sorted']) == {'0.01', '0.5'}
    row = next(r for r in rows if r['exp_id'] == 'select.range_cr_0.5.a_scan')
    assert rng['a_scan']['0.5'] == (float(row['io.reads']) + 5, float(row['est.reads']) + 7)


def test_one_command_writes_every_figure(tmp_path):
    write_summary(tmp_path / 'summary.csv', synthetic_rows())
    made = plot_all(tmp_path / 'summary.csv', tmp_path / 'figs')
    assert sorted(p.name for p in made) == ['fator_block.png', 'fator_prefetch.png', 'fator_toss.png',
                                            'fator_work.png', 'joins_large_large.png',
                                            'joins_small_large.png', 'joins_small_small.png',
                                            'selecoes.png']
    assert all(p.read_bytes().startswith(b'\x89PNG') for p in made)


def test_bad_rows_are_labeled_not_hidden_or_plotted():
    # P6 §5 e §10: resultado inválido não vira barra; erro não se passa por N/A;
    # N/A da seleção aparece em vez de sumir.
    rows = [{k: str(v) for k, v in r.items()} for r in synthetic_rows()]
    by_id = {r['exp_id']: r for r in rows}
    by_id['join.small_large.a.hash_join']['valid'] = 'False'
    by_id['join.small_large.a.nl_join']['status'] = 'error'
    by_id['join.small_large.a.sort_merge_join']['status'] = 'nondeterministic'
    by_id['select.range_cr_0.5.b_hash']['status'] = 'N/A'
    by_id['select.eq_dre_03.a_scan']['status'] = 'error'
    series = join_series(rows, 'small_large')
    assert series['a'] == {'hash_join': 'INVÁLIDO', 'nl_join': 'ERRO', 'sort_merge_join': 'NÃO DET.'}
    eq, rng = select_series(rows)
    assert rng['b_hash']['0.5'] == 'N/A'
    assert eq['a_scan'] == 'ERRO'          # média de 19 DREs não é "média de 20"


def test_figure_draws_measured_bar_and_estimated_mark(tmp_path, monkeypatch):
    # Enunciado §7.2.2.b: medido ao lado do estimado. Confere o que é desenhado.
    from matplotlib.axes import Axes
    from bench.plots import plot_joins
    drawn = {'bar': [], 'hlines': [], 'text': []}
    monkeypatch.setattr(Axes, 'bar', lambda self, x, h, *a, **k: drawn['bar'].append((x, h)))
    monkeypatch.setattr(Axes, 'hlines', lambda self, y, *a, **k: drawn['hlines'].append(y))
    real_text = Axes.text
    monkeypatch.setattr(Axes, 'text', lambda self, x, y, s, *a, **k: (drawn['text'].append(s),
                                                                     real_text(self, x, y, s, *a, **k))[1])
    rows = [{k: str(v) for k, v in r.items()} for r in synthetic_rows()]
    series = join_series(rows, 'small_large')
    plot_joins(rows, 'small_large', tmp_path)
    cells = [series[l][alg] for alg in ('nl_join', 'sort_merge_join', 'hash_join') for l in series]
    good = [c for c in cells if not isinstance(c, str)]
    assert [h for _, h in drawn['bar']] == [m for m, _ in good]
    assert drawn['hlines'] == [e for _, e in good]
    assert 'N/A' in drawn['text']


def test_factor_series_by_level():
    rows = [{k: str(v) for k, v in r.items()} for r in synthetic_rows()]
    block = factor_series(rows, 'block')
    assert len(block) == 18 and all(set(v) == {'2048', '4096', '8192'} for v in block.values())
    row = next(r for r in rows if r['exp_id'] == 'factor.work_16.join.large_large.a.hash_join')
    assert factor_series(rows, 'work')['large_large.a.hash_join']['16'] == \
        (float(row['io.reads']) + 5, float(row['est.reads']) + 7)
    seeks = factor_series(rows, 'prefetch')
    row = next(r for r in rows if r['exp_id'] == 'factor.prefetch_8.select.range_cr_0.5.a_scan')
    assert seeks['range_cr_0.5.a_scan']['8'] == (float(row['io.seeks']), None)
