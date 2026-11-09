"""Testes do orquestrador (P6 §6, issue #11) com Engine e operadores dublês.

Os dublês simulam contadores para provar o PROTOCOLO (o que entra na janela
medida); medições de verdade só com a pilha real (tests/integration).
"""
import csv
from pathlib import Path as FsPath
from types import SimpleNamespace

import pytest

from bench.generator import Dataset
from bench.matrix import Experiment, Side, join_experiments
import bench.run as runmod
from bench.run import (COLUMNS, CsvLog, EngineCache, PinLeak, measure, read_rows, report_invalid,
                       run, summarize)
from bench.sqlite_ref import output_signature
from cbd.contracts import Cost, NotApplicable, Path, Policy, TableStats

ALUNOS = list(Dataset(.002).records('aluno'))


class FakeEngine:
    def __init__(self):
        self.reads = self.writes = self.pins = 0
        self.cold_calls = 0
        self.catalog = SimpleNamespace(table=self.table)
        self.bp = SimpleNamespace(pinned=lambda: self.pins)

    def table(self, name):
        def stats():
            self.reads += 0  # stats() lê metadados; se lesse blocos, apareceria aqui
            return TableStats(len(ALUNOS), 7, 14.0, 150.0, {}, {}, {})
        return SimpleNamespace(name=name, stats=stats)

    def cold(self):
        self.cold_calls += 1
        self.reads = self.writes = 0

    def snapshot(self):
        return {'io.reads': self.reads, 'io.writes': self.writes, 'bp.requests': self.reads,
                'bp.WORK.requests': 0}


def select_exp(**kw):
    side = Side('aluno', 'aluno__heap', 'cr', Path.SCAN, 'HEAP')
    base = dict(exp_id='select.x.a_scan', kind='select', op='select_range', cost_fn='select_range_cost',
                args=dict(table=side, column='cr', lo=None, hi=5.0, path=Path.SCAN),
                config='a', algorithm='select_range', sel_type='range_nonunique', selectivity=.5,
                path='SCAN', query_id='range_cr_0.5')
    base.update(kw)
    return Experiment(**base)


def lazy_scan(eng, rows, reads_per_row=1):
    """Operador dublê lazy: cada registro devolvido custa 1 leitura."""
    def op(eng_, **kwargs):
        for row in rows:
            eng.reads += reads_per_row
            yield row
    return op


def test_row_counts_whole_output_inside_window():
    eng = FakeEngine()
    rows = [r for r in ALUNOS if r[8] <= 5.0]

    def cost(eng_, **kwargs):
        eng.reads += 100          # se o custo entrasse na janela, io.reads ficaria 100 + len(rows)
        return Cost(reads=42, formula='b_r')

    row = measure(eng, select_exp(), functions={'select_range': lazy_scan(eng, rows), 'select_range_cost': cost})
    assert row['status'] == 'ok'
    assert row['result_rows'] == len(rows) > 0          # iterador consumido até o fim
    assert row['io.reads'] == len(rows)                 # nem o custo nem stats() na contagem
    assert row['checksum'] == output_signature(dict(kind='select', r_table='aluno'), rows)[1]
    assert (row['est.reads'], row['est.formula']) == (42, 'b_r')
    assert 'cost_fn alterou contadores' in row['note']
    assert eng.cold_calls == 1
    assert (row['r_rows'], row['r_blocks']) == (len(ALUNOS), 7)


def test_not_applicable_in_cost_or_operator_becomes_na_row():
    eng = FakeEngine()

    def refuse(*a, **k):
        raise NotApplicable('faixa em hash primário')

    def lazy_refuse(eng_, **k):
        yield from ()
        raise NotApplicable('sem caminho')

    ok_cost = lambda *a, **k: Cost(reads=1)
    r1 = measure(eng, select_exp(), functions={'select_range': lazy_scan(eng, []), 'select_range_cost': refuse})
    r2 = measure(eng, select_exp(), functions={'select_range': lazy_refuse, 'select_range_cost': ok_cost})
    assert (r1['status'], r2['status']) == ('N/A', 'N/A')
    assert 'hash primário' in r1['note'] and 'sem caminho' in r2['note']


def test_pin_leak_aborts():
    eng = FakeEngine()

    def leaky(eng_, **k):
        eng.pins = 1
        yield ALUNOS[0]

    with pytest.raises(PinLeak):
        measure(eng, select_exp(), functions={'select_range': leaky,
                                             'select_range_cost': lambda *a, **k: Cost(reads=1)})


def test_join_resolves_sides_and_output_signature():
    eng = FakeEngine()
    exp = next(e for e in join_experiments() if e.exp_id == 'join.large_large.a.hash_join')
    inscricoes = list(Dataset(.002).records('inscricao'))
    by_dre = {r[0]: r for r in ALUNOS}
    output = [by_dre[i[0]] + i for i in inscricoes]
    seen = {}

    def op(eng_, r, s, info):
        seen.update(r=r, s=s)
        info['variant'] = 'grace'
        yield from output

    row = measure(eng, exp, functions={'hash_join': op, 'hash_join_cost': lambda *a, **k: Cost(reads=3)})
    assert (seen['r'].table.name, seen['r'].column, seen['r'].path) == ('aluno__heap', 'dre', Path.SCAN)
    assert seen['s'].table.name == 'inscricao__heap'
    assert row['result_rows'] == len(output) and row['variant'] == 'grace'
    assert row['checksum'] == output_signature(dict(kind='join', r_table='aluno', s_table='inscricao'), output)[1]
    assert (row['r_org'], row['s_org'], row['config']) == ('HEAP', 'HEAP', 'a')


def test_csv_header_matches_guide(tmp_path):
    text = FsPath('docs/implementacao/P6_bancada_relatorio.md').read_text()
    block = text.split('### 6.3 CSV')[1].split('```')[1]
    guide = [c.strip() for c in block.replace('\n', ',').split(',') if c.strip()]
    assert list(COLUMNS) == guide
    log = CsvLog(tmp_path / 'raw.csv')
    log.write({'exp_id': 'x', 'rep': 1})
    log.close()
    log = CsvLog(tmp_path / 'raw.csv')          # reabrir não repete o cabeçalho
    log.write({'exp_id': 'y', 'rep': 1})
    log.close()
    rows = list(csv.reader(open(tmp_path / 'raw.csv')))
    assert rows[0] == guide and [r[0] for r in rows[1:]] == ['x', 'y']


def test_engine_cache_reopens_only_when_config_changes():
    opened = []

    class Dummy:
        def __init__(self, b, c):
            opened.append((b, c.policy))

        def close(self):
            pass

    cache = EngineCache('data', open_engine=Dummy)
    lru, mru = select_exp(), select_exp(policy=Policy.MRU)
    cache.get(lru), cache.get(lru), cache.get(mru), cache.get(mru)
    assert opened == [(4096, Policy.LRU), (4096, Policy.MRU)]


def test_operator_bug_is_recorded_not_fatal_unless_pins_leak():
    eng = FakeEngine()
    ok_cost = lambda *a, **k: Cost(reads=1)

    def buggy(eng_, **k):
        yield ALUNOS[0]
        raise IndexError('offset fora do bloco')

    row = measure(eng, select_exp(), functions={'select_range': buggy, 'select_range_cost': ok_cost})
    assert row['status'] == 'error' and 'IndexError' in row['note']
    assert row.get('result_rows') is None          # resultado parcial nunca é gravado como medida

    def buggy_leak(eng_, **k):
        eng.pins = 2
        raise KeyError('x')
        yield

    with pytest.raises(PinLeak):
        measure(eng, select_exp(), functions={'select_range': buggy_leak, 'select_range_cost': ok_cost})


def test_cost_fn_bug_is_recorded_like_operator_bug():
    # Revisão do PR #33: KeyError no cost_fn derrubava a rodada inteira, enquanto o
    # mesmo erro no operador virava status=error. As duas falhas são do dono do
    # operador e devem ser tratadas igual; o operador nem chega a rodar.
    eng = FakeEngine()
    ran = []

    def broken_cost(*a, **k):
        raise KeyError('n_blocks')

    def op(eng_, **k):
        ran.append(1)
        yield from ()

    row = measure(eng, select_exp(), functions={'select_range': op, 'select_range_cost': broken_cost})
    assert row['status'] == 'error' and row['note'].startswith('cost_fn: KeyError')
    assert ran == [] and row.get('result_rows') is None


def test_validation_detects_extra_or_missing_tuple_without_touching_counters():
    rows = [r for r in ALUNOS if r[8] <= 5.0]
    expected = output_signature(dict(kind='select', r_table='aluno'), rows)
    ok_cost = lambda *a, **k: Cost(reads=1)
    results = {}
    for label, out, exp_sig in (('ok', rows, expected), ('menos', rows[:-1], expected),
                                ('mais', rows + [rows[0]], expected), ('sem_validar', rows, None)):
        eng = FakeEngine()
        row = measure(eng, select_exp(), functions={'select_range': lazy_scan(eng, out),
                                                   'select_range_cost': ok_cost}, expected=exp_sig)
        results[label] = row
    assert results['ok']['valid'] is True
    assert results['menos']['valid'] is False and results['mais']['valid'] is False
    assert results['sem_validar']['valid'] == ''
    # Validar não muda nenhum contador medido.
    keys = [c for c in COLUMNS if c.startswith(('io.', 'bp.'))]
    assert [results['ok'].get(k) for k in keys] == [results['sem_validar'].get(k) for k in keys]


def test_run_collects_invalid_rows(tmp_path, capsys):
    eng = FakeEngine()
    rows = ALUNOS[:5]
    exp = select_exp()
    log = CsvLog(tmp_path / 'raw.csv')
    cache = SimpleNamespace(get=lambda e: eng)
    invalid = run([exp], cache, log, {exp.query_id: (999, 0)},
                  functions={'select_range': lazy_scan(eng, rows),
                             'select_range_cost': lambda *a, **k: Cost(reads=1)})
    log.close()
    assert [r['exp_id'] for r in invalid] == [exp.exp_id]
    assert report_invalid(invalid) == 1
    assert 'INVÁLIDO select.x.a_scan' in capsys.readouterr().out


def test_unknown_query_id_is_an_error_row_and_run_continues(tmp_path):
    # Revisão do PR #33: expected[exp.query_id] com id fora do workload.json levantava
    # KeyError e derrubava a rodada. Matriz e workload fora de sincronia viram linha
    # error (sem medir: não haveria como validar) e a rodada segue.
    eng = FakeEngine()
    lost, good = select_exp(exp_id='select.lost', query_id='nao_existe'), select_exp()
    log = CsvLog(tmp_path / 'raw.csv')
    run([lost, good], SimpleNamespace(get=lambda e: eng), log, {good.query_id: (5, 0)},
        functions={'select_range': lazy_scan(eng, ALUNOS[:5]),
                   'select_range_cost': lambda *a, **k: Cost(reads=1)})
    log.close()
    with (tmp_path / 'raw.csv').open(newline='') as handle:
        rows = {r['exp_id']: r for r in csv.DictReader(handle)}
    assert rows['select.lost']['status'] == 'error' and 'nao_existe' in rows['select.lost']['note']
    assert rows['select.lost']['io.reads'] == ''
    assert rows[good.exp_id]['status'] == 'ok'


def _run_with_times(tmp_path, monkeypatch, times, exps, previous=(), stop_after=None):
    """Roda `run` com tempos de relógio controlados; `stop_after` simula a queda."""
    ticks = iter([t for dt in times for t in (0.0, dt)])
    monkeypatch.setattr(runmod, 'clock', lambda: next(ticks))
    eng, calls = FakeEngine(), []

    def op(eng_, **k):
        if stop_after is not None and len(calls) == stop_after:
            raise KeyboardInterrupt
        calls.append(1)
        yield from lazy_scan(eng, ALUNOS[:3])(eng_)

    log = CsvLog(tmp_path / 'raw.csv')
    sig = output_signature(dict(kind='select', r_table='aluno'), ALUNOS[:3])
    try:
        run(exps, SimpleNamespace(get=lambda e: eng), log, {e.query_id: sig for e in exps},
            functions={'select_range': op, 'select_range_cost': lambda *a, **k: Cost(reads=1)},
            previous=previous)
    except KeyboardInterrupt:
        pass
    finally:
        log.close()
    return read_rows(tmp_path / 'raw.csv')


def test_stable_measure_stops_at_three(tmp_path, monkeypatch):
    rows = _run_with_times(tmp_path, monkeypatch, [1.0, 1.01, 1.02, 9, 9], [select_exp()])
    assert [r['rep'] for r in rows] == ['1', '2', '3']
    assert all(r['valid'] == 'True' for r in rows)


def test_unstable_time_repeats_until_ten(tmp_path, monkeypatch):
    rows = _run_with_times(tmp_path, monkeypatch, [1.0, 2.0] * 6, [select_exp()])
    assert len(rows) == runmod.MAX_REPS == 10


def test_resume_after_crash_does_not_duplicate(tmp_path, monkeypatch):
    exps = [select_exp(exp_id=f'select.x.{i}') for i in range(3)]
    times = [1.0] * 30
    first = _run_with_times(tmp_path, monkeypatch, times, exps, stop_after=4)   # cai no meio do 2º
    assert [(r['exp_id'], r['rep']) for r in first] == [
        ('select.x.0', '1'), ('select.x.0', '2'), ('select.x.0', '3'), ('select.x.1', '1')]
    rows = _run_with_times(tmp_path, monkeypatch, times, exps, previous=first)
    pairs = [(r['exp_id'], r['rep']) for r in rows]
    assert len(pairs) == len(set(pairs)) == 9          # 3 experimentos × 3 repetições, sem repetir
    assert sorted(pairs) == sorted((f'select.x.{i}', str(k)) for i in range(3) for k in (1, 2, 3))


def test_na_row_written_once(tmp_path, monkeypatch):
    monkeypatch.setattr(runmod, 'clock', lambda: 0.0)
    eng, log = FakeEngine(), CsvLog(tmp_path / 'raw.csv')

    def refuse(*a, **k):
        raise NotApplicable('faixa em hash')

    exp = select_exp()
    run([exp], SimpleNamespace(get=lambda e: eng), log, {exp.query_id: (0, 0)},
        functions={'select_range': lazy_scan(eng, []), 'select_range_cost': refuse})
    log.close()
    rows = read_rows(tmp_path / 'raw.csv')
    assert [(r['status'], r['rep']) for r in rows] == [('N/A', '1')]
    # e o --resume não tenta de novo
    log = CsvLog(tmp_path / 'raw.csv')
    run([exp], SimpleNamespace(get=lambda e: eng), log, {exp.query_id: (0, 0)},
        functions={'select_range': lazy_scan(eng, []), 'select_range_cost': refuse}, previous=rows)
    log.close()
    assert len(read_rows(tmp_path / 'raw.csv')) == 1


def test_summary_uses_medians_and_latest_file(tmp_path):
    raw = tmp_path / 'raw'
    old, new = CsvLog(raw / '2026-11-10_all.csv'), CsvLog(raw / '2026-11-18_all.csv')
    old.write({'exp_id': 'e1', 'rep': 1, 'status': 'ok', 'valid': True, 'io.reads': 1, 'io.writes': 0, 'time_s': 9})
    for rep, (reads, t) in enumerate([(10, 3.0), (10, 1.0), (10, 2.0)], 1):
        new.write({'exp_id': 'e1', 'rep': rep, 'status': 'ok', 'valid': True,
                   'io.reads': reads, 'io.writes': 0, 'time_s': t})
    new.write({'exp_id': 'e2', 'rep': 1, 'status': 'N/A', 'note': 'faixa em hash'})
    old.close(), new.close()
    assert summarize(sorted(raw.glob('*.csv')), tmp_path / 'summary.csv') == 2
    rows = {r['exp_id']: r for r in read_rows(tmp_path / 'summary.csv')}
    assert (rows['e1']['n_reps'], float(rows['e1']['io.reads']), float(rows['e1']['time_s'])) == ('3', 10, 2.0)
    assert rows['e1']['valid'] == 'True'
    assert (rows['e2']['status'], rows['e2']['n_reps'], rows['e2']['note']) == ('N/A', '0', 'faixa em hash')
