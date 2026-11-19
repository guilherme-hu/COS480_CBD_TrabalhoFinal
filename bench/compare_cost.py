"""Estimado × medido (P6 §7, enunciado §6.1.3 e §7.2.2.b). Camada 1, dono: P6.

Lê `results/summary.csv` e, para cada experimento medido, compara as leituras e as
gravações medidas com as estimadas pelas funções `*_cost` de P4/P5 (colunas `est.*`).
O P6 não escreve fórmula de custo nenhuma: só compara. Cada diferença relevante
recebe um motivo por regra (tabela do P6 §7); as que nenhuma regra explica vão para
a lista "diferenças não explicadas", que vira conversa com o dono do operador.

Uso: `python -m bench.compare_cost [--summary results/summary.csv] [--output results/compare_cost.csv]`.
"""
import argparse
import csv
import json
from pathlib import Path

# LIMITAÇÃO: limiares escolhidos pelo P6, não vêm dos livros. Uma diferença de até
# 10% ou de 1 bloco (o que for maior) é "dentro da tolerância": o P4 testa seleções
# a ±1 bloco e o P5 testa joins a ±10%.
REL_TOL, ABS_TOL = .10, 1
# "bp.DATA.hits alto" (P6 §7): pelo menos 10% dos pedidos de DATA atendidos sem ler disco.
HIGH_HIT_RATE = .10

COLUMNS = ('exp_id', 'kind', 'algorithm', 'size_combo', 'config', 'path', 'sel_type', 'variant', 'valid',
           'est.formula', 'est.reads', 'io.reads', 'ratio_reads', 'est.writes', 'io.writes',
           'ratio_writes', 'within_tolerance', 'reason', 'explained')


def _num(row, col):
    value = row.get(col, '')
    return None if value in ('', None) else float(value)


def ratio(measured, estimated):
    """medido / estimado; 1.0 se os dois são zero; None se não há estimativa."""
    if estimated is None or measured is None:
        return None
    if estimated == 0:
        return 1.0 if measured == 0 else float('inf')
    return measured / estimated


def close(measured, estimated):
    return abs(measured - estimated) <= max(ABS_TOL, REL_TOL * estimated)


def _info(row):
    try:
        return json.loads(row.get('info_json') or '{}')
    except json.JSONDecodeError:
        return {}


def _skew(row, info):
    """Recursão só conta como skew se o operador marcou o fallback (P6 §7: "inesperada").

    Recursão por falta de memória (ex.: WORK = 16) é prevista pela fórmula; se o
    medido diverge mesmo assim, a diferença tem de ir para "não explicadas".
    """
    return bool(info.get('skew_fallback')) or 'skew' in (row.get('variant') or '')


def reasons(row):
    """Motivos (P6 §7) por métrica: {'reads': [...], 'writes': [...]}.

    Cada regra diz qual métrica ela explica, para um motivo de gravação não esconder
    uma diferença de leitura (e vice-versa).
    """
    reads, est_reads = _num(row, 'io.reads'), _num(row, 'est.reads')
    writes, est_writes = _num(row, 'io.writes') or 0, _num(row, 'est.writes') or 0
    info = _info(row)
    hits, misses = _num(row, 'bp.DATA.hits') or 0, _num(row, 'bp.DATA.misses') or 0
    out = {'reads': [], 'writes': []}
    if (est_reads and reads < est_reads and not close(reads, est_reads)
            and hits + misses and hits / (hits + misses) >= HIGH_HIT_RATE):
        out['reads'].append('reaproveitamento do buffer (páginas de DATA reaproveitadas, ex.: interno do BNL em cache)')
    if est_writes > 0 and writes < est_writes and not close(writes, est_writes):
        out['writes'].append('páginas temporárias descartadas sem ir ao disco')
    # A estimativa por min/max só entra no custo de quem usa índice ou organização;
    # o SCAN lê b_r blocos qualquer que seja a distribuição.
    if row.get('sel_type') == 'range_nonunique' and row.get('path') not in ('SCAN', '', None):
        out['reads'].append('distribuição não uniforme (estimativa por min/max)')
    # LIMITAÇÃO: nenhum contrato de P4/P5 define `height`/`expected_height` em `info`
    # (revisão de 12/11); a regra só dispara quando isso for combinado na #36.
    if 'height' in info and 'expected_height' in info and info['height'] != info['expected_height']:
        out['reads'].append(f"altura da B+ {info['height']} ≠ esperada {info['expected_height']} (ocupação real ≠ fill)")
    if _skew(row, info):
        why = 'skew de chaves (fallback do hash join)'
        out['reads'].append(why)
        out['writes'].append(why)
    return out


def compare(rows):
    """Compara cada experimento 'ok' com estimativa; devolve (linhas, pendências).

    Pendências = diferenças que nenhuma regra explica, mais as linhas que não podem
    ser comparadas: resultado inválido (valid=False, bloqueia o relatório, P6 §5) e
    linha 'ok' sem medida.
    """
    out, unexplained = [], []
    for row in rows:
        if row.get('status') != 'ok' or _num(row, 'est.reads') is None:
            continue
        line = {c: row.get(c, '') for c in COLUMNS}
        reads, est_reads = _num(row, 'io.reads'), _num(row, 'est.reads')
        blocked = ('resultado inválido (valid=False): bloqueia o relatório'
                   if str(row.get('valid', '')).lower() == 'false' else
                   'linha ok sem medida de leituras' if reads is None else None)
        if blocked:
            line.update(ratio_reads=None, ratio_writes=None, within_tolerance=False,
                        reason=blocked, explained=False)
            unexplained.append(line)
            continue
        writes, est_writes = _num(row, 'io.writes') or 0, _num(row, 'est.writes') or 0
        off = {'reads': not close(reads, est_reads), 'writes': not close(writes, est_writes)}
        why = reasons(row) if any(off.values()) else {'reads': [], 'writes': []}
        # Explicada só se CADA métrica fora da tolerância tem o próprio motivo.
        explained = all(why[m] for m, is_off in off.items() if is_off)
        reason = [w for m in ('reads', 'writes') if off[m] for w in why[m]]
        line.update(ratio_reads=ratio(reads, est_reads), ratio_writes=ratio(writes, est_writes),
                    within_tolerance=not any(off.values()),
                    reason='; '.join(dict.fromkeys(reason)), explained=explained)
        out.append(line)
        if not explained:
            unexplained.append(line)
    return out, unexplained


def main():
    parser = argparse.ArgumentParser(description='Tabela estimado × medido a partir do summary.csv.')
    parser.add_argument('--summary', type=Path, default=Path('results/summary.csv'))
    parser.add_argument('--output', type=Path, default=Path('results/compare_cost.csv'))
    args = parser.parse_args()
    with args.summary.open(newline='') as handle:
        lines, unexplained = compare(csv.DictReader(handle))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(lines)
    print(f'{len(lines)} experimentos comparados em {args.output}; '
          f'{len(unexplained)} pendência(s) (não explicadas ou inválidas)')
    for line in unexplained:
        print(f"PENDENTE {line['exp_id']} ({line['reason'] or 'sem motivo'}): leituras {line['io.reads']} × {line['est.reads']} estimadas, "
              f"gravações {line['io.writes']} × {line['est.writes']} ({line['est.formula']})")


if __name__ == '__main__':
    main()
