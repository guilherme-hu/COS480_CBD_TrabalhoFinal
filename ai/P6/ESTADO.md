# Estado da parte P6 — atualizado em 08/11

Branches locais empilhadas (nenhuma enviada ao GitHub ainda):
`p6/4-workload-escopo` → `p6/5-variantes-escopo` → `p6/7-engine-testes` →
`p6/9-matriz-joins` → `p6/10-matriz-selecoes` → `p6/11-protocolo-medicao` →
`p6/12-validacao-sqlite` → `p6/13-repeticoes-resume`.
Suíte: 52 passaram, 1 pulado (teste do Engine com a pilha real).

## Pronto (com teste que prova)
- Carga de trabalho no escopo novo (#4): `test_all_workload_results_against_python`.
- Plano físico com as 23 variantes do escopo (#5): `test_plan_and_catalog_adapter`.
- Engine documentado e testado com dublês (#7): `tests/test_engine.py`.
- Matriz: 117 joins + 4 de lado forçado (#9) e 90 seleções (#10): `tests/test_matrix.py`.
- Protocolo de medição, CSV §6.3, validação SQLite, repetições, `--resume`,
  `summary.csv` (#11, #12, #13): `tests/test_run.py`.

## Em andamento
- Nada pela metade.

## Falta
- #3 cartões retroativos das decisões do gerador (debate com o integrante).
- #6 decisão sobre volumes (aluno em B = 8192 abaixo de 1 280 blocos).
- #8 pergunta ao professor sobre "índice secundário clustering".
- #14 carga física real, #15 integração ponta a ponta: dependem de P1–P5 no `main`.
- #16, #17, #19 experimentos de justificativa; #18 revisores independentes;
  #20–#23 rodada final e análise; #24–#27 relatório e fechamento.

## Requisitos do enunciado cobertos
| Enunciado § | Onde está | Teste que prova |
| :--- | :--- | :--- |
| §6.4 base universitária | `bench/schema.py`, `bench/generator.py` | `test_schema_is_architecture_contract`, `test_generator_types_uniqueness_and_determinism` |
| §5.1 NATURAL JOIN, 3 combinações de tamanho | `bench/workload.py` (`JOINS`) | `test_all_workload_results_against_python` |
| §5.1 algoritmos × configurações a–e | `bench/matrix.py` (`join_experiments`) | `test_join_matrix_size`, `test_config_paths_match_architecture` |
| §5.1.3 interna × externa | linhas `outer_*`/`build_*` da matriz | `test_join_matrix_size` |
| §5.2 seleções (UNIQUE =, NOT UNIQUE faixa), caminhos a–c, 1% e 50% | `bench/workload.py`, `bench/matrix.py` | `test_select_matrix_counts`, `test_select_values_come_from_workload` |
| §5 verificação contra referência (SQLite) | `bench/sqlite_ref.py`, `bench/run.py` (`valid`) | `test_validation_detects_extra_or_missing_tuple_without_touching_counters` |
| §6 repetições até < 5%, mínimo 3 | `bench/run.py` (`stable`, `run`) | `test_stable_measure_stops_at_three`, `test_unstable_time_repeats_until_ten` |
| §6.1.2 contagem de blocos/buffer por operação | `bench/run.py` (`measure`) + `cbd/engine.py` | `test_row_counts_whole_output_inside_window`, `test_cold_flushes_before_resetting` |
| §6.1.1 ocupação antes/depois da manutenção | — (#17) | — |
| §6.1.3 estimado × medido | colunas `est.*` no CSV; análise em #22 | `test_row_counts_whole_output_inside_window` (só grava) |

## Desvios ou dúvidas sobre o contrato
- `Experiment.op`/`cost_fn` guardam o **nome** da função (o guia diz "função"),
  para a matriz não depender de P4/P5; `bench/run.py` resolve por `OPERATORS`.
- CLI: `python -m bench.run --data <pasta>` em vez de `--scale` (a escala vem da
  pasta preparada por `bench.build`, evitando medir uma base com a escala errada).
- O `Engine` ainda não foi testado com a pilha real (P1/P2 ausentes).

## Armadilhas conhecidas / bugs abertos
- Tempos de execução muito curtos (ms) tendem a nunca estabilizar em 5% e vão
  até 10 repetições; avaliar na #20 (P6 §6.4 opção 3).
- `POLICY_BY_ALGORITHM` está LRU em tudo até a #16.

## Decisões provisórias a rever
- D-P6-01 — N comum dos pares bucket-join (menor N padrão, limitado por V(A)).
