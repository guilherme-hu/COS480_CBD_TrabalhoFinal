# Resultados

> **A preencher (#21, #22, #23, #26):** todas as tabelas e figuras saem de
> `results/summary.csv` por `bench/plots.py` e `bench/compare_cost.py`; nenhum
> número é digitado à mão.

Figuras previstas (P6 §9), sempre com o medido ao lado do estimado:

- junções: algoritmo × configuração a–e, uma figura por combinação de tamanho;
- escolha da política de substituição, incluindo LRU × MRU no nested-loop;
- seleções: caminho × seletividade;
- memória de trabalho (WORK) × passadas da ordenação e recursão do hash join;
- tamanho de bloco;
- ocupação antes e depois da carga de manutenção, carga em massa × incremental.

## Estimado × medido

Tabela com a razão medido/estimado de leituras e gravações por experimento e o
motivo de cada diferença relevante.
