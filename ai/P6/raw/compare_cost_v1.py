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

COLUMNS = ('exp_id', 'kind', 'algorithm', 'size_combo', 'config', 'path', 'sel_type', 'variant',
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


def reasons(row):
    """Motivos (P6 §7) que explicam a diferença desta linha; [] se nenhum se aplica."""
    out = []
    reads, est_reads = _num(row, 'io.reads'), _num(row, 'est.reads')
    writes, est_writes = _num(row, 'io.writes'), _num(row, 'est.writes') or 0
    info = _info(row)
    hits, misses = _num(row, 'bp.DATA.hits') or 0, _num(row, 'bp.DATA.misses') or 0
    if (est_reads and reads < est_reads and not close(reads, est_reads)
            and hits + misses and hits / (hits + misses) >= HIGH_HIT_RATE):
        out.append('reaproveitamento do buffer (páginas de DATA reaproveitadas, ex.: interno do BNL em cache)')
    if est_writes > 0 and writes < est_writes and not close(writes, est_writes):
        out.append('páginas temporárias descartadas sem ir ao disco')
    if row.get('sel_type') == 'range_nonunique' and not close(reads, est_reads):
        out.append('distribuição não uniforme (estimativa por min/max)')
    if 'height' in info and 'expected_height' in info and info['height'] != info['expected_height']:
        out.append(f"altura da B+ {info['height']} ≠ esperada {info['expected_height']} (ocupação real ≠ fill)")
    if info.get('skew_fallback') or 'skew' in (row.get('variant') or '') or (info.get('levels') or 0) > 0:
        out.append('skew de chaves (fallback ou recursão do hash join)')
    return out


def compare(rows):
    """Uma linha por experimento 'ok' com estimativa; devolve (linhas, não explicadas)."""
    out, unexplained = [], []
    for row in rows:
        if row.get('status') != 'ok' or _num(row, 'est.reads') is None:
            continue
        reads, est_reads = _num(row, 'io.reads'), _num(row, 'est.reads')
        writes, est_writes = _num(row, 'io.writes') or 0, _num(row, 'est.writes') or 0
        within = close(reads, est_reads) and close(writes, est_writes)
        why = [] if within else reasons(row)
        line = {c: row.get(c, '') for c in COLUMNS}
        line.update(ratio_reads=ratio(reads, est_reads), ratio_writes=ratio(writes, est_writes),
                    within_tolerance=within, reason='; '.join(why), explained=within or bool(why))
        out.append(line)
        if not line['explained']:
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
          f'{len(unexplained)} diferença(s) não explicada(s)')
    for line in unexplained:
        print(f"NÃO EXPLICADA {line['exp_id']}: leituras {line['io.reads']} × {line['est.reads']} estimadas, "
              f"gravações {line['io.writes']} × {line['est.writes']} ({line['est.formula']})")


if __name__ == '__main__':
    main()
