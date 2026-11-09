# Relatório técnico (enunciado §7.2.2)

Fonte em Markdown, montada em PDF pelo pandoc (motor `tectonic` ou `xelatex`).

```sh
python -m bench.report          # regenera gerado/ (capa, tabelas da base, matriz)
python -m bench.report --pdf    # e monta relatorio.pdf nesta pasta
```

- `0N_*.md`: capítulos escritos à mão, na ordem de `CHAPTERS` em `bench/report.py`.
- `gerado/`: **não edite**. Capa (do `README.md` da raiz), tabelas da base (de
  `results/base_stats.csv`) e composição da matriz (de `bench/matrix.py`). O teste
  `test_generated_parts_are_up_to_date` falha se estiverem desatualizadas.
- Nenhum número de resultado é digitado à mão: tabelas e figuras de resultados
  virão de `results/summary.csv` (#22, #23).
- Use `$\bowtie$` para o símbolo de junção (a fonte padrão não tem o caractere ⋈).
- O PDF não é versionado; gere-o na entrega.
