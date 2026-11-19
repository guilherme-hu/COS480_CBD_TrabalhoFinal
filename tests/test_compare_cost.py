"""Testes do estimado × medido (P6 §7, issue #22) com linhas sintéticas do summary.csv."""
import json

import pytest

from bench.compare_cost import compare, ratio


def row(exp_id, reads, est_reads, writes=0, est_writes=0, **kw):
    base = {'exp_id': exp_id, 'status': 'ok', 'kind': 'join', 'algorithm': 'hash_join',
            'io.reads': reads, 'est.reads': est_reads, 'io.writes': writes, 'est.writes': est_writes,
            'est.formula': 'f', 'bp.DATA.hits': 0, 'bp.DATA.misses': reads, 'info_json': '{}'}
    base.update({k.replace('__', '.'): v for k, v in kw.items()})
    return {k: str(v) for k, v in base.items()}


def by_id(rows):
    lines, unexplained = compare(rows)
    return {l['exp_id']: l for l in lines}, [l['exp_id'] for l in unexplained]


def test_ratio_edges():
    assert ratio(0, 0) == 1.0 and ratio(5, 0) == float('inf') and ratio(None, 3) is None
    assert ratio(90, 100) == pytest.approx(.9)


def test_tolerance_is_ten_percent_or_one_block():
    lines, unexplained = by_id([row('dez', 110, 100), row('um', 3, 2), row('fora', 112, 100)])
    assert lines['dez']['within_tolerance'] and lines['um']['within_tolerance']
    assert not lines['fora']['within_tolerance'] and unexplained == ['fora']


def test_each_reason_rule():
    rows = [
        # BNL: interno ficou em DATA -> leu bem menos que a fórmula e com muitos hits.
        row('buffer', 500, 1000, bp__DATA__hits=400, bp__DATA__misses=500, algorithm='nl_join'),
        # Temporários descartados: gravou menos que o estimado.
        row('temp', 1000, 1000, writes=100, est_writes=400),
        # Faixa com estimativa uniforme por min/max errando.
        row('faixa', 300, 150, kind='select', sel_type='range_nonunique', algorithm='select_range',
            path='SECONDARY'),
        # B+ com altura diferente da esperada.
        row('altura', 9, 6, info_json=json.dumps({'height': 4, 'expected_height': 3})),
        # Skew: fallback para nested-loop no hash join.
        row('skew', 5000, 1000, writes=900, est_writes=900, info_json=json.dumps({'skew_fallback': 2})),
    ]
    lines, unexplained = by_id(rows)
    assert 'reaproveitamento do buffer' in lines['buffer']['reason']
    assert 'temporárias' in lines['temp']['reason']
    assert 'não uniforme' in lines['faixa']['reason']
    assert 'altura da B+ 4' in lines['altura']['reason']
    assert 'skew' in lines['skew']['reason']
    assert unexplained == []


def test_unexplained_listed_separately_and_rules_do_not_overreach():
    rows = [
        # Leu MAIS que a fórmula com hits altos: reaproveitamento não explica.
        row('mais', 1500, 1000, bp__DATA__hits=400, bp__DATA__misses=1100),
        # Gravou MAIS que o estimado: temporários descartados não explicam.
        row('gravou_mais', 1000, 1000, writes=700, est_writes=400),
        # Seleção por igualdade fora da tolerância: a regra de distribuição é só para faixa.
        row('igualdade', 9, 3, kind='select', sel_type='eq_unique'),
    ]
    _, unexplained = by_id(rows)
    assert unexplained == ['mais', 'gravou_mais', 'igualdade']


def test_skips_rows_without_measure_or_estimate():
    na = {'exp_id': 'na', 'status': 'N/A', 'est.reads': '', 'io.reads': ''}
    err = {'exp_id': 'err', 'status': 'error', 'est.reads': '', 'io.reads': ''}
    lines, unexplained = compare([na, err, row('ok', 10, 10)])
    assert [l['exp_id'] for l in lines] == ['ok'] and unexplained == []


def test_tolerance_is_relative_to_the_estimate_on_both_sides():
    # 89 × 100 está 11% abaixo: fora (uma tolerância relativa ao medido aceitaria).
    lines, _ = by_id([row('abaixo', 89, 100), row('acima', 110, 100)])
    assert not lines['abaixo']['within_tolerance'] and lines['acima']['within_tolerance']


def test_output_ratios_are_measured_over_estimated():
    lines, _ = by_id([row('r', 150, 100, writes=30, est_writes=60)])
    assert lines['r']['ratio_reads'] == pytest.approx(1.5)
    assert lines['r']['ratio_writes'] == pytest.approx(.5)


def test_reason_of_one_metric_does_not_explain_the_other():
    rows = [
        # Gravações abaixo (temporários) não explicam leituras 3× acima.
        row('le_mais', 3000, 1000, writes=100, est_writes=400),
        # Leituras abaixo com hits altos não explicam gravações 9× acima.
        row('grava_mais', 500, 1000, writes=900, est_writes=100,
            bp__DATA__hits=400, bp__DATA__misses=500),
    ]
    lines, unexplained = by_id(rows)
    assert unexplained == ['le_mais', 'grava_mais']
    assert 'temporárias' in lines['le_mais']['reason']       # o motivo parcial continua visível


def test_rules_need_their_condition():
    rows = [
        # Leu menos sem hits: não é reaproveitamento do buffer.
        row('sem_hits', 500, 1000, bp__DATA__hits=0, bp__DATA__misses=500),
        # Altura igual à esperada não explica nada.
        row('altura_ok', 9, 6, info_json=json.dumps({'height': 3, 'expected_height': 3})),
        # Faixa pelo SCAN: custo b_r, a distribuição não importa.
        row('faixa_scan', 4000, 1962, kind='select', sel_type='range_nonunique', path='SCAN'),
        # Recursão sem fallback de skew (ex.: WORK = 16) é prevista pela fórmula.
        row('recursao', 3000, 2000, info_json=json.dumps({'levels': 1})),
    ]
    _, unexplained = by_id(rows)
    assert unexplained == ['sem_hits', 'altura_ok', 'faixa_scan', 'recursao']


def test_invalid_or_unmeasured_rows_are_pending_not_compared():
    # P6 §5: valid=False bloqueia o relatório; nunca sai como "dentro da tolerância".
    bad = row('invalida', 10, 10, valid='False')
    empty = row('sem_medida', 10, 10)
    empty['io.reads'] = ''
    lines, unexplained = compare([bad, empty, row('boa', 10, 10, valid='True')])
    assert [l['exp_id'] for l in lines] == ['boa']
    assert [(l['exp_id'], l['reason'].split(' ')[0]) for l in unexplained] == [
        ('invalida', 'resultado'), ('sem_medida', 'linha')]


def test_error_rows_with_numbers_are_skipped():
    err = row('err', 50, 10)
    err['status'] = 'error'
    lines, unexplained = compare([err])
    assert lines == [] and unexplained == []
