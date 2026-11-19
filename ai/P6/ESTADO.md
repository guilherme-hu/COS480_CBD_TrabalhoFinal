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
- Matriz: 117 joins + 4 de lado forçado (#9) e 98 seleções (#10: 80 igualdades + 18 faixas, com o B+ secundário clustering sobre B+ e hash): `tests/test_matrix.py`.
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

## Relatório técnico (#24)
- Esqueleto em `docs/relatorio/` (Markdown → PDF via pandoc + tectonic), partes
  numéricas geradas por `bench/report.py` e testadas em `tests/test_report.py`.
  Formato escolhido pelo agente com aval do integrante ("pode seguir"); trocar
  para LaTeX puro só muda a montagem.
- Pendentes no texto: resumo das conclusões (#26), decisões (#25), política e
  fatores (#16, #19), decisão de volumes (#6).

## Requisitos do enunciado cobertos
| Enunciado § | Onde está | Teste que prova |
| :--- | :--- | :--- |
| §6.4 base universitária | `bench/schema.py`, `bench/generator.py` | `test_schema_is_architecture_contract`, `test_generator_types_uniqueness_and_determinism` |
| §5.1 NATURAL JOIN, 3 combinações de tamanho | `bench/workload.py` (`JOINS`) | `test_all_workload_results_against_python` |
| §5.1 algoritmos × configurações a–e | `bench/matrix.py` (`join_experiments`) | `test_join_matrix_size`, `test_config_paths_match_architecture` |
| §5.1.3 interna × externa | linhas `outer_*`/`build_*` da matriz | `test_join_matrix_size` |
| §5.2 seleções (UNIQUE =, NOT UNIQUE faixa), caminhos a–c, 1% e 50%; §4.2 secundário clustering sobre B+ e hash | `bench/workload.py`, `bench/matrix.py` | `test_select_matrix_counts`, `test_select_values_come_from_workload` |
| §5 verificação contra referência (SQLite) | `bench/sqlite_ref.py`, `bench/run.py` (`valid`) | `test_validation_detects_extra_or_missing_tuple_without_touching_counters` |
| §6 repetições até < 5% no tempo, mínimo 3, contadores idênticos | `bench/run.py` (`stable`, `run`) | `test_stable_measure_stops_at_three`, `test_unstable_time_repeats_until_ten`, `test_io_divergence_is_flagged_nondeterministic` |
| §6 retomada da rodada sem misturar bases | `bench/run.py` (`dataset`, `load_previous`, `last_attempt`) | `test_rows_carry_dataset_and_resume_refuses_other_dataset`, `test_resume_retries_error_but_not_na`, `test_resume_drops_truncated_last_line_and_checks_header`, `test_summary_refuses_mixed_datasets_and_filters_by_dataset` |
| §6.1.2 contagem de blocos/buffer por operação | `bench/run.py` (`measure`) + `cbd/engine.py` | `test_row_counts_whole_output_inside_window`, `test_cold_flushes_before_resetting` |
| §7.3 folha de rosto com nome e DRE de todos | `bench/report.py` (`cover`, `members`) | `test_cover_has_every_member_with_dre` |
| §7.2.2 relatório: base e metodologia | `docs/relatorio/03_base.md`, `04_metodologia.md`, `gerado/` | `test_base_tables_come_from_base_stats`, `test_generated_parts_are_up_to_date` |
| §6.1.3 / §7.2.2.b estimado × medido com motivo | `bench/compare_cost.py` | `test_each_reason_rule`, `test_unexplained_listed_separately_and_rules_do_not_overreach` |
| §7.2.2.b gráficos com medido ao lado do estimado | `bench/plots.py` | `test_one_command_writes_every_figure`, `test_join_series_takes_the_13_configs_and_marks_na` |
| §6.1.1 ocupação antes/depois da manutenção | — (#17) | — |

## Desvios ou dúvidas sobre o contrato
- `Experiment.op`/`cost_fn` guardam o **nome** da função (o guia diz "função"),
  para a matriz não depender de P4/P5; `bench/run.py` resolve por `OPERATORS`.
- CLI: `python -m bench.run --data <pasta>` em vez de `--scale` (a escala vem da
  pasta preparada por `bench.build`, evitando medir uma base com a escala errada).
- O `Engine` ainda não foi testado com a pilha real (P1/P2 ausentes).

## Armadilhas conhecidas / bugs abertos
- Tempos de execução muito curtos (ms) tendem a nunca estabilizar em 5% e vão
  até 10 repetições; avaliar na #20 (P6 §6.4 opção 3).
- `POLICY_BY_ALGORITHM` está LRU em tudo até a #16. O P1 implementa só LRU e
  MRU (D-P1-08, PR #2): a #16 compara só as duas.
- Limitação a citar no relatório (revisão do PR #33): a validação compara
  `(count, soma dos hash_value da PK lógica)`. Um join com as PKs certas e outra
  coluna errada passa. Segue o P6 §5; não é bug, mas vai como limitação.

- `compare_cost`: limiares escolhidos pelo P6 (tolerância de 10% ou 1 bloco;
  "hits altos" = 10% dos pedidos de DATA). A regra da altura da B+ depende de
  P4/P5 porem `height`/`expected_height` no `info` (conferir na #36).

- `bench/plots.py` (#23, parcial): joins por combinação e seleções. Faltam as
  figuras de política (#16), fatores (#19) e manutenção/bulk (#17), que dependem
  dos `exp_id` desses experimentos. Cores: slots 1–3 da paleta de referência.

## Decisões provisórias a rever
- D-P6-01 — N comum dos pares bucket-join (menor N padrão, limitado por V(A)).
