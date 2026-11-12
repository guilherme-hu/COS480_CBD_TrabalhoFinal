"""Figuras do relatório a partir de `results/summary.csv` (P6 §9). Camada 1, dono: P6.

Nenhum número é digitado à mão: toda figura sai das medianas do summary.csv. Em
toda barra, o medido (blocos transferidos = io.reads + io.writes) aparece ao lado
do estimado pela função `*_cost` (traço escuro sobre a barra), como pede o
enunciado §7.2.2.b. Figuras estáticas (vão para o PDF): sem camada de interação.

Cores: três primeiros slots da paleta categórica de referência (validados em todos
os pares); algoritmo -> cor fixa, nunca pela posição. O aqua fica abaixo de 3:1 de
contraste: a legenda e a tabela de bench.compare_cost são a vista em texto.

Uso: `python -m bench.plots [--summary results/summary.csv] [--output results/figs]`.
LIMITAÇÃO: as figuras de política (#16), fatores (#19) e manutenção (#17) entram
quando esses experimentos tiverem os exp_id definidos.
"""
import argparse
import csv
from pathlib import Path
import statistics

import matplotlib
matplotlib.use('Agg')                    # sem janela: só grava arquivos
import matplotlib.pyplot as plt          # noqa: E402

from bench.matrix import JOIN_ALGORITHMS  # noqa: E402

SERIES = ('#2a78d6', '#eb6834', '#1baf7a')       # slots 1–3 da paleta de referência
ALG_COLOR = dict(zip(JOIN_ALGORITHMS, SERIES))   # cor segue o algoritmo, nunca o rank
ALG_LABEL = {'nl_join': 'nested-loop', 'sort_merge_join': 'sort-merge', 'hash_join': 'hash join'}
TEXT, MUTED, GRID, EST = '#0b0b0b', '#52514e', '#e4e3df', '#0b0b0b'
COMBO_TITLE = {'small_small': 'curso ⋈ disciplina (menor ⋈ menor)',
               'small_large': 'curso ⋈ aluno (menor ⋈ maior)',
               'large_large': 'aluno ⋈ inscricao (maior ⋈ maior)'}


def _num(row, col):
    value = row.get(col, '')
    return None if value in ('', None) else float(value)


def status_label(row):
    """None se a linha é uma medição boa; senão o rótulo escrito na figura.

    N/A legítimo, erro do operador, contagem não determinística e resultado inválido
    (valid=False) aparecem com nomes diferentes: nenhum some em silêncio (P6 §10).
    """
    if row.get('status') == 'N/A':
        return 'N/A'
    if row.get('status') == 'error':
        return 'ERRO'
    if row.get('status') != 'ok':
        return 'NÃO DET.'
    if str(row.get('valid', '')).lower() == 'false':
        return 'INVÁLIDO'
    if _num(row, 'io.reads') is None:
        return 'SEM MEDIDA'
    return None


def blocks(row, prefix='io'):
    """Blocos transferidos (leituras + gravações), medidos ('io') ou estimados ('est')."""
    reads, writes = _num(row, f'{prefix}.reads'), _num(row, f'{prefix}.writes')
    return None if reads is None else reads + (writes or 0)


def join_series(rows, combo):
    """{rótulo da config: {algoritmo: (medido, estimado) | rótulo de status}} na ordem da matriz.

    Só as 13 configurações da matriz principal; as linhas de lado forçado
    (outer_*/build_*) ficam fora desta figura.
    """
    out = {}
    for row in rows:
        parts = row['exp_id'].split('.')
        if parts[0] != 'join' or parts[1] != combo or len(parts) != 4:
            continue
        label, alg = parts[2], parts[3]
        cell = status_label(row) or (blocks(row), blocks(row, 'est'))
        out.setdefault(label, {})[alg] = cell
    return out


def select_series(rows):
    """(igualdade, faixa): igualdade = {caminho: (média medido, média estimado)} sobre
    os 20 DREs; faixa = {caminho: {seletividade: (medido, estimado)}}.

    Linha que não é medição boa vira o rótulo de status (`status_label`). Na
    igualdade, basta um DRE ruim para o caminho inteiro virar rótulo: a média de
    menos de 20 valores não seria a "média de 20 DREs" da legenda.
    """
    eq, bad_eq, rng = {}, {}, {}
    for row in rows:
        if not row['exp_id'].startswith('select.'):
            continue
        # exp_id = select.<query_id>.<caminho>; o query_id pode ter ponto (range_cr_0.01).
        query_id, path = row['exp_id'].removeprefix('select.').rsplit('.', 1)
        label = status_label(row)
        if row['sel_type'] == 'eq_unique':
            if label:
                bad_eq.setdefault(path, label)
            else:
                eq.setdefault(path, []).append((blocks(row), blocks(row, 'est')))
        else:
            target = query_id.removeprefix('range_cr_')
            rng.setdefault(path, {})[target] = label or (blocks(row), blocks(row, 'est'))
    mean = {p: (statistics.mean(m for m, _ in v),
                statistics.mean(e for _, e in v) if all(e is not None for _, e in v) else None)
            for p, v in eq.items()}
    for p, label in bad_eq.items():
        mean[p] = label
    return mean, rng


def _style(ax, ylabel):
    ax.set_ylabel(ylabel, color=MUTED)
    ax.grid(axis='y', color=GRID, linewidth=.8)
    ax.set_axisbelow(True)
    for side in ('top', 'right', 'left'):
        ax.spines[side].set_visible(False)
    ax.spines['bottom'].set_color(MUTED)
    ax.tick_params(colors=MUTED, length=0)


def _bars(ax, x, width, cells, color, label):
    """Barras do medido com o estimado como traço; rótulo de status escrito na base."""
    shown = False
    for xi, cell in zip(x, cells):
        if cell is None:
            continue
        if isinstance(cell, str):
            ax.text(xi, 0, cell, ha='center', va='bottom', fontsize=6, color=MUTED, rotation=90)
            continue
        measured, estimated = cell
        ax.bar(xi, measured, width * .92, color=color, label=None if shown else label,
               edgecolor='white', linewidth=1)
        shown = True
        if estimated is not None:
            ax.hlines(estimated, xi - width * .46, xi + width * .46, colors=EST, linewidth=2)


def _legend(ax, handles_from):
    from matplotlib.lines import Line2D
    handles, labels = handles_from.get_legend_handles_labels()
    handles.append(Line2D([], [], color=EST, linewidth=2))
    labels.append('estimado (*_cost)')
    ax.legend(handles, labels, frameon=False, fontsize=8, labelcolor=TEXT, ncols=4,
              loc='upper left', bbox_to_anchor=(0, 1.12))


def plot_joins(rows, combo, output):
    series = join_series(rows, combo)
    if not series:
        return None
    labels = list(series)
    fig, ax = plt.subplots(figsize=(10, 4))
    width = .8 / len(JOIN_ALGORITHMS)
    for i, alg in enumerate(JOIN_ALGORITHMS):
        x = [k + (i - 1) * width for k in range(len(labels))]
        _bars(ax, x, width, [series[l].get(alg) for l in labels], ALG_COLOR[alg], ALG_LABEL[alg])
    ax.set_xticks(range(len(labels)), labels, rotation=45, ha='right', fontsize=8)
    _style(ax, 'blocos transferidos (leituras + gravações)')
    ax.set_title(COMBO_TITLE.get(combo, combo), color=TEXT, loc='left', pad=28, fontsize=11)
    _legend(ax, ax)
    fig.tight_layout()
    path = Path(output) / f'joins_{combo}.png'
    fig.savefig(path, dpi=200)
    plt.close(fig)
    return path


def plot_selections(rows, output):
    eq, rng = select_series(rows)
    if not eq and not rng:
        return None
    fig, (left, right) = plt.subplots(1, 2, figsize=(11, 4), gridspec_kw={'width_ratios': [1, 2]})
    paths = list(eq)
    _bars(left, range(len(paths)), .6, [eq[p] for p in paths], SERIES[0], 'medido (média de 20 DREs)')
    left.set_xticks(range(len(paths)), paths, rotation=45, ha='right', fontsize=8)
    _style(left, 'blocos transferidos')
    left.set_title('igualdade em aluno.dre (UNIQUE)', color=TEXT, loc='left', pad=28, fontsize=10)
    _legend(left, left)
    paths = list(rng)
    targets = sorted({t for v in rng.values() for t in v}, key=float)
    width = .8 / max(1, len(targets))
    for i, target in enumerate(targets):
        x = [k + (i - (len(targets) - 1) / 2) * width for k in range(len(paths))]
        _bars(right, x, width, [rng[p].get(target) for p in paths], SERIES[i],
              f'seletividade {float(target):.0%}')
    right.set_xticks(range(len(paths)), paths, rotation=45, ha='right', fontsize=8)
    _style(right, 'blocos transferidos')
    right.set_title('faixa em aluno.cr (NOT UNIQUE)', color=TEXT, loc='left', pad=28, fontsize=10)
    _legend(right, right)
    fig.tight_layout()
    path = Path(output) / 'selecoes.png'
    fig.savefig(path, dpi=200)
    plt.close(fig)
    return path


def plot_all(summary, output):
    with Path(summary).open(newline='') as handle:
        rows = list(csv.DictReader(handle))
    Path(output).mkdir(parents=True, exist_ok=True)
    made = [plot_joins(rows, combo, output) for combo in COMBO_TITLE]
    made.append(plot_selections(rows, output))
    return [p for p in made if p is not None]


def main():
    parser = argparse.ArgumentParser(description='Gera as figuras do relatório a partir do summary.csv.')
    parser.add_argument('--summary', type=Path, default=Path('results/summary.csv'))
    parser.add_argument('--output', type=Path, default=Path('results/figs'))
    args = parser.parse_args()
    for path in plot_all(args.summary, args.output):
        print(path)


if __name__ == '__main__':
    main()
