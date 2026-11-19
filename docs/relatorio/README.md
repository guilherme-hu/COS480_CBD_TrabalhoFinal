# Relatório técnico (enunciado §7.2.2)

Fonte em Markdown, montada em PDF pelo pandoc (motor `tectonic` ou `xelatex`).

```sh
python -m bench.report          # regenera gerado/ (capa, tabelas da base, matriz)
python -m bench.report --pdf    # e monta relatorio.pdf nesta pasta
python -m bench.report --capa   # só a folha de rosto (capa.pdf), para os demais integrantes postarem (§7.3)
```

- `0N_*.md`: capítulos escritos à mão, na ordem de `CHAPTERS` em `bench/report.py`.
- `gerado/`: **não edite**. Capa (do `README.md` da raiz), índice dos cartões de
  decisão (de `docs/decisoes/`; todo cartão novo precisa ser citado no capítulo 2,
  senão `test_every_decision_card_is_justified_in_the_design_chapter` falha), tabelas da base (de
  `results/base_stats.csv`) e composição da matriz (de `bench/matrix.py`). O teste
  `test_generated_parts_are_up_to_date` falha se estiverem desatualizadas.
- Nenhum número de resultado é digitado à mão: tabelas e figuras de resultados
  virão de `results/summary.csv` (#22, #23).
- Use `$\bowtie$` para o símbolo de junção (a fonte padrão não tem o caractere ⋈).
- O PDF não é versionado; gere-o na entrega.
