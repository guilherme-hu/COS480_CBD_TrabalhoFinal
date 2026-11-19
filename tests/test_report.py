"""Testes das partes geradas do relatório técnico (P6 §9, issue #24)."""
import csv
import os
from pathlib import Path
import shutil

import pytest

from bench.matrix import join_experiments, select_experiments
from bench.report import (CHAPTERS, REPORT_DIR, _schematic_workload, base_section, build_pdf,
                          cover, decision_cards, decisions_section, generate, matrix_section,
                          members)


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


def test_every_decision_card_is_justified_in_the_design_chapter():
    # Enunciado §7.2.2.a / issue #25: todo cartão registrado aparece no capítulo 2.
    # Um colega que acrescentar um cartão sem citá-lo no relatório derruba este teste.
    # Conta só citação fora de nota (linhas que começam com ">" não justificam nada).
    cards = decision_cards()
    assert {'D-P1-01', 'D-P1-12', 'D-P6-01', 'D-P6-10'} <= {c for c, _, _ in cards}
    index = decisions_section().splitlines()
    assert sum(line.startswith('| D-P') for line in index) == len(cards)
    missing = [line.split(' | ')[0][2:] for line in index if line.startswith('| D-P') and line.endswith('| — |')]
    assert not missing, f'cartões sem justificativa no capítulo 2: {missing}'


def test_decision_cards_read_status_and_reject_malformed(tmp_path):
    folder = tmp_path / 'decisoes'
    folder.mkdir()
    (folder / 'P9.md').write_text(
        '### D-P9-01 — Primeira\n- **Status:** decidida\n\n'
        '### D-P9-02 — Segunda | com barra\n\n- **Status:** provisória (12/11), a rever\n\n'
        '### D-P9-03 — Terceira\n- **Status:** decidida (revista em 12/11)\n')
    chapter = tmp_path / 'cap.md'
    chapter.write_text('## Blocos\n| x | D-P9-01, D-P9-03 |\n## Hash\nD-P9-01\n')
    assert decision_cards(folder) == [('D-P9-01', 'Primeira', 'decidida'),
                                      ('D-P9-02', 'Segunda | com barra', 'provisória'),
                                      ('D-P9-03', 'Terceira', 'decidida (revista)')]
    text = decisions_section(folder, chapter)
    assert '3 cartões, 1 provisórios' in text
    assert '| D-P9-01 | Primeira | decidida | Blocos; Hash |' in text
    assert '| D-P9-02 | Segunda \\| com barra | provisória | — |' in text   # barra escapada, não citado
    # Cartão sem a linha de status logo abaixo: erro, em vez de sumir do índice.
    (folder / 'P8.md').write_text('### D-P8-01 — Sem status\n\nTexto solto.\n')
    with pytest.raises(ValueError, match='P8.md'):
        decision_cards(folder)


def test_decision_cards_normalize_status_and_keep_file_order(tmp_path):
    # Revisão da #25: status em negrito, itálico ou maiúscula é normalizado; a ordem
    # segue o nome do arquivo; "revista" só marca quando o campo diz "revista em".
    (tmp_path / 'P2.md').write_text(
        '### D-P2-01 — A\n- **Status:** **Decidida** em 18/11\n\n'
        '### D-P2-02 — B\n- **Status:** _provisória_ (12/11)\n\n'
        '### D-P2-03 — C\n- **Status:** decidida; nada foi revisto\n')
    (tmp_path / 'P1.md').write_text('### D-P1-01 — Z\n- **Status:** `revista` (13/11)\n')
    assert decision_cards(tmp_path) == [('D-P1-01', 'Z', 'revista'), ('D-P2-01', 'A', 'decidida'),
                                        ('D-P2-02', 'B', 'provisória'), ('D-P2-03', 'C', 'decidida')]


@pytest.mark.parametrize('text', [
    '#### D-P3-01 — T\n- **Status:** decidida\n',          # outro nível de título
    '### D-P3-01 - T\n- **Status:** decidida\n',           # hífen no lugar do travessão
    '### D-P3-1 — T\n- **Status:** decidida\n',            # numeração com 1 dígito
    '### D-P3-01 — T\n- **Status:** provisoria\n',         # status fora do vocabulário
    '### D-P3-01 — T\n- **Status:** (12/11) decidida\n',   # status sem palavra inicial
    '### D-P5-01 — T\n- **Status:** decidida\n',           # cartão de outra parte no arquivo
    '### D-P3-01 — T\n- **Status:** decidida\n\n### D-P3-01 — U\n- **Status:** decidida\n',  # duplicado
])
def test_malformed_cards_raise_instead_of_vanishing(tmp_path, text):
    (tmp_path / 'P3.md').write_text(text)
    with pytest.raises(ValueError, match='P3.md'):
        decision_cards(tmp_path)


def test_lowercase_decision_file_is_not_ignored(tmp_path):
    (tmp_path / 'p3.md').write_text('### D-P3-01 — T\n- **Status:** decidida\n')
    assert [c for c, _, _ in decision_cards(tmp_path)] == ['D-P3-01']


def test_citation_in_a_note_does_not_count_as_justification(tmp_path):
    folder = tmp_path / 'decisoes'
    folder.mkdir()
    (folder / 'P3.md').write_text('### D-P3-01 — T\n- **Status:** decidida\n\n'
                                  '### D-P3-02 — U\n- **Status:** decidida\n')
    chapter = tmp_path / 'cap.md'
    chapter.write_text('## Árvores\n| x | D-P3-02 | D-P3-02 |\n'
                       '> Os cartões D-P3-01 e D-P3-02 entram depois.\n')
    text = decisions_section(folder, chapter)
    assert '| D-P3-01 | T | decidida | — |' in text            # só citado na nota
    assert '| D-P3-02 | U | decidida | Árvores |' in text      # sem repetir a seção


@pytest.mark.skipif(not os.environ.get('REPORT_PDF'), reason='lento; rode com REPORT_PDF=1')
def test_pdf_builds(tmp_path):
    shutil.copytree(REPORT_DIR, tmp_path / 'relatorio', ignore=shutil.ignore_patterns('*.pdf'))
    pdf = build_pdf(tmp_path / 'relatorio')
    assert pdf.read_bytes().startswith(b'%PDF')
