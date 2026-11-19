# Estado da parte P6 — atualizado em 19/11

PRs #28–#35 (#4–#13) mesclados no `main` em 12/11. Pilha #76–#82 mesclada em 19/11
(fecha #24, #22, #23, #6, #19, #3; a #8 segue aberta à espera do professor).
PR #2 do P1 (camada 4: `FileManager`, `BufferPool`, LRU/MRU, toss, prefetch)
mesclado em 19/11, depois da revisão final do P6 (ver "Camada 4 no `main`" abaixo).
Suíte no `main`: 130 passaram e 2 pulados (o do Engine com a pilha real, que agora
espera só `cbd.files.catalog` do P2, e o do PDF, que roda com `REPORT_PDF=1`).
Revisão independente de 12/11 tratada (`ai/P6/review.md`); backups das branches
anteriores ao rebase em `backup/p6-*` e branches `p6/*` locais já mescladas podem
ser apagados.

## Pronto (com teste que prova)
- Carga de trabalho no escopo novo (#4): `test_all_workload_results_against_python`.
- Plano físico com as 23 variantes do escopo (#5): `test_plan_and_catalog_adapter`.
- Engine documentado e testado com dublês (#7): `tests/test_engine.py`.
- Matriz: 117 joins + 4 de lado forçado (#9) e 98 seleções (#10: 80 igualdades + 18 faixas, com o B+ secundário clustering sobre B+ e hash): `tests/test_matrix.py`.
- Experimentos de fator único (#19): `factor_experiments` (bloco, toss, prefetch, WORK; 72 linhas, D-P6-10), `--only factors`: `test_factor_matrix_counts`, `test_each_factor_row_changes_only_its_factor`.
- Cartões retroativos do gerador e da referência (#3): D-P6-03 a D-P6-08, `test_heap_order_is_not_key_order`, `test_signature_detects_swapped_duplicate_with_same_count`.
- Volumes (#6): D-P6-02, `test_size_checks_use_areas_of_each_block_size`.
- Protocolo de medição, CSV §6.3, validação SQLite, repetições, `--resume`,
  `summary.csv` (#11, #12, #13): `tests/test_run.py`.

## Em andamento
- Nada pela metade.

## Falta
- #8: enviar ao professor a pergunta pronta na D-P6-09 e registrar a resposta (o agente não envia).
- #14 carga física real, #15 integração ponta a ponta: dependem de P2–P5 no `main`
  (a camada 4 do P1 já entrou em 19/11).
- #25 (consolidar as decisões no relatório): capítulo 2 escrito por item do enunciado §7.2.2.a
  (organização escolhida pelo integrante em 19/11) e índice gerado `gerado/decisoes.md`.
  Faltam os cartões de P2 (#62), P3 (#44/#63), P5 (#71) e o PR do P4 (#67); lacunas
  comentadas nas issues em 19/11. Linhas "cartão pendente" trocam pelo id quando chegarem.
  Lacuna própria: cartão do plano físico (campos de ordenação, #5).
- #16, #17 experimentos de justificativa (#19 já tem a lista; falta medir com a pilha real); #18 revisores independentes;
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
| §6 um fator por vez (bloco, toss, prefetch, WORK) | `bench/matrix.py` (`factor_experiments`) | `test_each_factor_row_changes_only_its_factor`, `test_factor_rows_reuse_main_matrix_queries` |
| §7.2.2.a opções de projeto justificadas com as preteridas | `docs/relatorio/02_opcoes_de_projeto.md`, `bench/report.py` (`decision_cards`, `decisions_section`) | `test_every_decision_card_is_justified_in_the_design_chapter`, `test_malformed_cards_raise_instead_of_vanishing` |
| §6.1.1 ocupação antes/depois da manutenção | — (#17) | — |

## Desvios ou dúvidas sobre o contrato
- `Experiment.op`/`cost_fn` guardam o **nome** da função (o guia diz "função"),
  para a matriz não depender de P4/P5; `bench/run.py` resolve por `OPERATORS`.
- CLI: `python -m bench.run --data <pasta>` em vez de `--scale` (a escala vem da
  pasta preparada por `bench.build`, evitando medir uma base com a escala errada).
- O `Engine` ainda não foi testado com a pilha real no `main`: a camada 4 entrou,
  falta o catálogo do P2 (`cbd.files.catalog`). Em 17/11 ele foi sondado com a camada
  4 do PR #2 e um catálogo dublê, e passou.

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

- #19: "os scans" do guia §4.4 foram lidos como as faixas em `cr` pelo caminho a (1% e 50%); a igualdade por scan ficou de fora (20 DREs repetiriam o mesmo scan). O toss também roda no BNL maior ⋈ maior, config a.

## Pendências da revisão de 12/11
- Regra "altura da B+ ≠ esperada" do `compare_cost` nunca dispara: nenhum contrato
  põe `height`/`expected_height` em `info`. Combinar com P3/P4 na #36.
- Regra da faixa ("distribuição não uniforme") não tem limite de magnitude: debater
  com o integrante se 50× ainda deve contar como explicada.
- Nome do P6 na capa vem abreviado do README ("C. Costa"); o §7.3 pede nome
  completo. Confirmar também a grafia "Rizawinsk" (P3).
- Números de configuração digitados em `03_base.md`/`04_metodologia.md` (áreas,
  "~34%"): não são resultado, mas podem divergir de `BASE_AREAS` sem teste.
- Tabela estimado × medido ainda não vai para `docs/relatorio/gerado/` (#26).

## Achados da revisão do PR #2 (P1) que afetam o P6 (13/11)
- O fator prefetch (#19) usa os scans de faixa, mas num scan de um arquivo só o
  prefetch não muda `io.reads` nem `io.seeks` (medido: 20 leituras e 1 seek com
  prefetch 0 ou 8); só troca misses por hits (17 de 20 viram hits, o que infla
  `bp.hit_rate`). O efeito em seeks só aparece com acessos intercalados (ex.: BNL).
  Resolvido pela D-P6-10 (17/11, decidida em 19/11): scans como controle + INL.
- O prefetch só liga se o dono do arquivo chamar `bp.allow_prefetch(fid, DATA)`;
  o guia do P2 não cita essa chamada. Sem ela o fator prefetch mede zero efeito.

## Camada 4 no `main` (PR #2 mesclado em 19/11)
Revisão final antes do merge, no `f64e3e3` mesclado com o `main`, reproduzindo os
achados da revisão do P2 de 12/11 (script na sessão, não versionado):
- Prefetch entre cursores (LRU, 2 scans intercalados de 40 blocos): 80 leituras em
  DATA = 8/16/32 e k = 0/4/8 (antes até 584). Seeks caem de 80 para 6–16.
- Prefetch com MRU (DATA = 128, 2 × 300 blocos): 600 leituras com k = 0/4/8 (antes
  1068); seeks 600 → 120 → 68. **O fator prefetch da D-P6-10 tem efeito mensurável.**
- `allow_prefetch` só em arquivo de um tipo de página: a B+ primária (folhas DATA e
  nós INDEX no mesmo `fid`) não pode chamá-lo; o uso indevido dá `ValueError`.
- `toss_immediate=True` com MRU dá o mesmo que LRU (interno de 200 blocos 5×, DATA =
  128: 1000 leituras contra 488 com `False`). D-P1-09 manda rodar LRU × MRU com
  `toss_immediate=False`; a bancada usa `toss=False` fora do fator toss. **Na #16,
  manter `toss=False`**, senão a comparação de políticas sai empatada por construção.
- No fator toss (#19), o BNL com MRU e `toss=True` vai ler mais que com `False`:
  é o efeito acima, não defeito da bancada. Explicar no relatório.
- Commits do P6 na branch do P1 para destravar o merge (com o ok do integrante):
  merge do `main` (conflito só na linha da política do guia P6 §4.4; ficou a do
  `main`) e registro do marcador `stress` no `pyproject.toml`, que o `fb54146`
  dizia registrar mas não estava no diff.

## Decisões provisórias a rever
- D-P6-09 (#8, 12/11): "secundário clustering" = ponteiros agrupados por chave
  (arquitetura §5.3), à espera da resposta do professor. Nenhuma leitura muda a matriz.

## Decisões tomadas nesta parte
- Revisão das provisórias em 19/11 (branch `p6/revisao-provisorias`): D-P6-01
  confirmada pelo integrante (B); D-P6-10 (C) e D-P6-03 a D-P6-08 delegadas pelo
  integrante ao agente, que manteve a recomendação de cada cartão. A D-P6-01 tinha
  pedido do P2 (PR #29) para ele e o P5 participarem da revisão: avisar no PR.
  D-P6-10 continua dependendo de o P2 chamar `allow_prefetch` (#83).
- D-P6-02 — memória do buffer constante em bytes no experimento de bloco
  (`areas_for_block`); base com volumes padrão, sem `--rows turma=100000` (#6).
