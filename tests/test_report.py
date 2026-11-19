"""Testes das partes geradas do relatório técnico (P6 §9, issue #24)."""
import csv
import os
from pathlib import Path
import shutil

import pytest

from bench.matrix import join_experiments, select_experiments
from bench.report import (CHAPTERS, REPORT_DIR, _schematic_workload, base_section, build_pdf,
                          cover, generate, matrix_section, members)


def test_cover_has_every_member_with_dre():
    # Enunciado §7.3: nome completo e DRE de todos, lidos do README (sem redigitar).
    rows = members('README.md')
    assert [part for _, _, part in rows] == ['P1', 'P2', 'P3', 'P4', 'P5', 'P6']
    assert all(len(dre) == 9 and dre.isdigit() for _, dre, _ in rows)
    assert ('Bernardo Brandão Pozzato C. Costa', '123289593', 'P6') in rows
    # Os nomes vão no `author` do YAML: saem na página de título, antes do sumário.
    header = cover('README.md').split('---')[1]
    assert 'author:' in header and all(f'DRE {dre}' in header for _, dre, _ in rows)


def test_base_tables_come_from_base_stats(tmp_path):
    stats = tmp_path / 'base_stats.csv'
    shutil.copy('results/base_stats.csv', stats)
    with stats.open(newline='') as handle:
        rows = list(csv.DictReader(handle))
    aluno = next(r for r in rows if r['table'] == 'aluno' and r['block_size'] == '4096')
    text = base_section(stats)
    assert f"| aluno | {int(aluno['n']):,} |".replace(',', '.') in text
    # Com as áreas proporcionais ao bloco (D-P6-02), a base atual é adequada nos 3 B.
    assert 'esperado' not in text
    # Muda um número no CSV: a tabela gerada acompanha (nada digitado à mão).
    aluno_8k = next(r for r in rows if r['table'] == 'aluno' and r['block_size'] == '8192')
    aluno_8k['estimated_blocks'] = '600'
    with stats.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    changed = base_section(stats)
    # B = 8192: DATA = 64 páginas, então "grande" exige 640 blocos.
    assert '| aluno | 50.000 |' in changed and 'aluno: 600 blocos, esperado >= 640' in changed


def test_matrix_counts_match_matrix_module():
    text = matrix_section()
    regular = [e for e in join_experiments() if 'outer' not in e.args and 'build' not in e.args]
    assert f'Total: {len(regular)} linhas de junção' in text
    assert f'Total: {len(select_experiments(_schematic_workload()))} linhas de seleção' in text


def test_generated_parts_are_up_to_date(tmp_path):
    # As partes geradas versionadas têm de bater com o que o gerador produz hoje
    # (base_stats.csv, README ou matriz mudaram -> rode `python -m bench.report`).
    out = generate('results/base_stats.csv', out_dir=tmp_path)
    for path in out.iterdir():
        assert path.read_text() == (REPORT_DIR / 'gerado' / path.name).read_text(), path.name
    for chapter in CHAPTERS:
        assert (REPORT_DIR / chapter).exists(), chapter


@pytest.mark.skipif(not os.environ.get('REPORT_PDF'), reason='lento; rode com REPORT_PDF=1')
def test_pdf_builds(tmp_path):
    shutil.copytree(REPORT_DIR, tmp_path / 'relatorio', ignore=shutil.ignore_patterns('*.pdf'))
    pdf = build_pdf(tmp_path / 'relatorio')
    assert pdf.read_bytes().startswith(b'%PDF')
