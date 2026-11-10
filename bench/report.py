"""Relatório técnico: partes geradas e montagem do PDF (P6 §9). Camada 1, dono: P6.

O texto fica em `docs/relatorio/*.md`, escrito à mão. Tudo o que é número ou
lista que já existe em outro lugar é gerado aqui, para nunca ser redigitado:
- capa: nomes e DREs da tabela "Integrantes" do README (enunciado §7.3);
- base: volumes, tamanho de registro, V(A), blocos e bfr estimados por tamanho
  de bloco e a conferência dos tamanhos dos joins, lidos de `base_stats.csv`;
- metodologia: composição da matriz contada a partir de `bench.matrix`.

Uso: `python -m bench.report [--stats results/base_stats.csv] [--pdf] [--capa]`.
Grava `docs/relatorio/gerado/*.md`; com `--pdf`, monta `docs/relatorio/relatorio.pdf`
com pandoc (motor tectonic ou xelatex). Não lê nem mede nada do SGBD.
"""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import re
import shutil
import subprocess

from bench.build import size_checks_for_block
from bench.matrix import areas_for_block, factor_experiments, join_experiments, select_experiments
from bench.workload import CR_RANGES, EQ_SAMPLES
from cbd.contracts import PageType

REPORT_DIR = Path('docs/relatorio')
# Ordem dos capítulos; `gerado/` é escrito por este módulo.
CHAPTERS = ('gerado/capa.md', '01_introducao.md', '02_opcoes_de_projeto.md', '03_base.md',
            'gerado/base.md', '04_metodologia.md', 'gerado/matriz.md', '05_resultados.md',
            '06_conclusao.md')


def members(readme):
    """(nome, DRE, parte) da tabela "Integrantes" do README, ordenados pela parte."""
    rows = re.findall(r'^\|\s*\*\*(.+?)\*\*\s*\|\s*`(\d+)`\s*\|\s*\*?(P\d)\*?\s*\|', Path(readme).read_text(), re.M)
    if not rows:
        raise ValueError(f'{readme}: tabela de integrantes não encontrada')
    return sorted(rows, key=lambda r: r[2])


def cover(readme):
    """Folha de rosto (enunciado §7.3). Os integrantes vão no `author` do pandoc para
    sair na página de título, ANTES do sumário (com `--toc` o corpo vem depois dele)."""
    lines = ['---',
             'title: "Avaliação 2 — Indexação e Processamento de Consultas"',
             'subtitle: "COS480 Construção de Banco de Dados — Grupo 3 — UFRJ, 2026.2"',
             'author:']
    lines += [f'  - "{name} — DRE {dre} ({part})"' for name, dre, part in members(readme)]
    lines += ['lang: pt-BR', '---', '']
    return '\n'.join(lines) + '\n'


def read_stats(path):
    """base_stats.csv -> {block_size: {tabela: linha}} com números convertidos."""
    out = {}
    with Path(path).open(newline='') as handle:
        for row in csv.DictReader(handle):
            row['n'], row['block_size'] = int(row['n']), int(row['block_size'])
            row['estimated_blocks'] = int(row['estimated_blocks'])
            row['estimated_bfr'] = float(row['estimated_bfr'])
            row['record_size'] = float(row['record_size'])
            row['distinct'] = json.loads(row['distinct'] or '{}')
            out.setdefault(row['block_size'], {})[row['table']] = row
    return out


def _table(header, rows, numeric=()):
    """Tabela Markdown; colunas em `numeric` (índices) alinhadas à direita."""
    align = ['---:' if i in numeric else ':---' for i in range(len(header))]
    out = ['| ' + ' | '.join(header) + ' |', '| ' + ' | '.join(align) + ' |']
    out += ['| ' + ' | '.join(str(c) for c in row) + ' |' for row in rows]
    return '\n'.join(out)


def _int(n):
    return f'{n:,}'.replace(',', '.')        # milhar com ponto (pt-BR)


def _dec(x, digits=1):
    return f'{x:.{digits}f}'.replace('.', ',')  # decimal com vírgula (pt-BR)


def base_section(stats_path):
    by_block = read_stats(stats_path)
    sizes = sorted(by_block)
    any_block = by_block[sizes[0]]
    tables = list(any_block)
    parts = ['## Volumes e cardinalidades\n',
             'Gerado de `results/base_stats.csv`. Tamanho médio do registro em bytes, no formato do P2. '
             'V(A) só nas colunas usadas em junções e seleções, sem contar nulos.\n']
    parts.append(_table(
        ['Tabela', 'n', 'Registro (B)', 'V(A)'],
        [(t, _int(any_block[t]['n']), _dec(any_block[t]['record_size']),
          ', '.join(f'{c} = {_int(v)}' for c, v in sorted(any_block[t]['distinct'].items())) or '—')
         for t in tables], numeric=(1, 2)))
    parts.append('\n## Blocos e fator de blocagem estimados\n')
    parts.append('Empacotamento heap estimado com o layout do P2 (cabeçalho de 28 bytes e slot de 4 bytes). '
                 '**Não são medidas do SGBD**: as medidas físicas entram quando a carga pela pilha real existir (#14).\n')
    parts.append(_table(
        ['Tabela'] + [f'b (B = {b})' for b in sizes] + [f'bfr (B = {b})' for b in sizes],
        [(t, *(_int(by_block[b][t]['estimated_blocks']) for b in sizes),
          *(_dec(by_block[b][t]['estimated_bfr']) for b in sizes)) for t in tables],
        numeric=range(1, 1 + 2 * len(sizes))))
    parts.append('\n## Adequação das combinações de tamanho\n\n'
                 'Critério (P6 §2.3): a tabela menor cabe em M − 2 blocos e a maior tem pelo menos '
                 '10 × DATA blocos. As áreas do buffer têm a mesma memória em bytes em todos os '
                 'tamanhos de bloco (D-P6-02), então M e DATA, em páginas, dependem de B:\n')
    parts.append(_table(['B', 'WORK = M', 'DATA', 'menor ≤', 'maior ≥'],
                        [(b, a[PageType.WORK], a[PageType.DATA], a[PageType.WORK] - 2,
                          _int(10 * a[PageType.DATA])) for b, a in ((b, areas_for_block(b)) for b in sizes)],
                        numeric=range(5)))
    rows = []
    for b in sizes:
        for check in size_checks_for_block(by_block[b], b):
            rows.append((check['size_combo'], b, 'ok' if not check['problems'] else '; '.join(check['problems'])))
    parts.append('\n' + _table(['Combinação', 'B', 'Situação'], rows, numeric=(1,)))
    return '\n'.join(parts) + '\n'


def _schematic_workload():
    """Consultas de seleção só com a estrutura (ids e tipos): a contagem de linhas da
    matriz não depende dos valores sorteados, então não precisa de base preparada."""
    out = [dict(id=f'eq_dre_{i:02d}', kind='select', sel_type='eq_unique', column='dre',
                operator='=', value=0, selectivity=0) for i in range(EQ_SAMPLES)]
    out += [dict(id=f'range_cr_{sel:g}', kind='select', sel_type='range_nonunique', column='cr',
                 operator=op, value=0, selectivity=sel) for sel, op in CR_RANGES]
    return out


def matrix_section():
    joins = join_experiments()
    regular = [e for e in joins if 'outer' not in e.args and 'build' not in e.args]
    by_config = Counter(e.config for e in regular)
    parts = ['## Composição da matriz\n', 'Contada a partir de `bench/matrix.py`.\n',
             _table(['Configuração', 'Linhas de junção'], sorted(by_config.items()), numeric=(1,)),
             f'\nTotal: {len(regular)} linhas de junção (3 combinações de tamanho × 3 algoritmos × '
             f'{len(regular) // 9} configurações físicas), mais {len(joins) - len(regular)} com o lado '
             f'externo ou construtor forçado.']
    selects = select_experiments(_schematic_workload())
    by_type = Counter((e.sel_type, e.config) for e in selects)
    parts += ['\n', _table(['Seleção', 'Configuração', 'Linhas'],
                           [(t, c, n) for (t, c), n in sorted(by_type.items())], numeric=(2,)),
              f'\nTotal: {len(selects)} linhas de seleção ({EQ_SAMPLES} igualdades em `dre` e '
              f'{len(CR_RANGES)} faixas em `cr`).']
    factors = Counter(e.exp_id.split('.')[1].rsplit('_', 1)[0] for e in factor_experiments(_schematic_workload()))
    names = {'block': 'tamanho de bloco', 'toss': 'toss-immediate', 'prefetch': 'prefetch', 'work': 'memória WORK'}
    parts += ['\n', _table(['Fator único', 'Linhas'], [(names[f], n) for f, n in factors.items()], numeric=(1,)),
              f'\nTotal: {sum(factors.values())} linhas de fator único, cada uma igual a uma linha da '
              'matriz principal com um só fator mudado.']
    return '\n'.join(parts) + '\n'


def generate(stats, readme='README.md', out_dir=REPORT_DIR):
    target = Path(out_dir) / 'gerado'
    target.mkdir(parents=True, exist_ok=True)
    (target / 'capa.md').write_text(cover(readme))
    (target / 'base.md').write_text(base_section(stats))
    (target / 'matriz.md').write_text(matrix_section())
    return target


def build_pdf(out_dir=REPORT_DIR, output='relatorio.pdf', cover_only=False):
    """pandoc + motor LaTeX. LIMITAÇÃO: precisa de pandoc e tectonic (ou xelatex) instalados.

    `cover_only`: só a folha de rosto (`capa.pdf`), que os demais integrantes postam
    no lugar do relatório (enunciado §7.3).
    """
    engine = next((e for e in ('tectonic', 'xelatex') if shutil.which(e)), None)
    if not shutil.which('pandoc') or engine is None:
        raise RuntimeError('instale pandoc e tectonic (ou xelatex) para gerar o PDF')
    out_dir = Path(out_dir)
    sources = [CHAPTERS[0]] if cover_only else [*CHAPTERS, '--toc']
    subprocess.run(['pandoc', *sources, '-o', output, f'--pdf-engine={engine}',
                    '-V', 'geometry:margin=2.5cm'],
                   cwd=out_dir, check=True)
    return out_dir / output


def main():
    parser = argparse.ArgumentParser(description='Gera as partes automáticas do relatório e, opcionalmente, o PDF.')
    parser.add_argument('--stats', type=Path, default=Path('results/base_stats.csv'))
    parser.add_argument('--pdf', action='store_true', help='monta docs/relatorio/relatorio.pdf com pandoc')
    parser.add_argument('--capa', action='store_true', help='monta só a folha de rosto em docs/relatorio/capa.pdf')
    args = parser.parse_args()
    print('partes geradas em', generate(args.stats))
    if args.pdf:
        print('PDF em', build_pdf())
    if args.capa:
        print('folha de rosto em', build_pdf(output='capa.pdf', cover_only=True))


if __name__ == '__main__':
    main()
