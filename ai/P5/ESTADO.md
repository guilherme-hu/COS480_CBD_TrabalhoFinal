# Estado da parte P5 · 27/11/2026

## Pronto

- Worktree p5/entrega, base baae948 de origin/main. P1, P2 e P4 reais disponíveis. Worktrees intermediárias preservadas.
- #61: GRACE, build automático/r/s, recursão, skew, nulos, vazio, teto em registros/bytes e cleanup. N1, N2 e base universitária N3 executados.
- #65: prepartitioned, bucket-join e GRACE para HASH incompatíveis, após os atalhos definidos em D-P5-01/05/07. Bucket-join real: 22 leituras, 0 escritas. Prepartitioned real: 96 leituras, 38 escritas, temporários apenas no lado externo.
- #66: sort-merge PK→FK, sort real, reutilização de ordem pelo contrato P4/P3, custo e WORK dividido. Dois heaps usam M≥6. Um lado ordenado permite M=3. B+ exercitada por contrato.
- #68: [variantes](variantes.md) a–e e comparação controlada. GRACE 1.620 medidos contra 1.668 estimados. Sort-merge 2.700 nos dois.
- #69: estresse real em SQLite, seed 480, relações ≥50×M, M=3 para hash e M=6 para sort sobre dois heaps. Comparação com versão ingênua executável em raw/.
- #71: cartões D-P5-01–14, estudo e [Enzo_decisoes.md](../../Enzo_decisoes.md), registro de IA completo, post-mortem, revisão independente em quatro focos. Índice do relatório regenerado.
- Suíte completa em UTF-8 na branch local: **868 passaram, 3 ignorados** em 657,37 s. Os ignorados são gráficos sem matplotlib, B+ ausente e renderização PDF opcional. Após ajustes documentais, testes do relatório **16 passaram, 1 ignorado**.
- Bancada `bench.run.measure`, base tiny e B=2048/M=6: **38 medições válidas** em configurações a, b, c e d suportadas por heap, hash e índice HASH secundário. Por variante: 18 sort-both, 13 in-memory, 3 GRACE, 3 prepartitioned e 1 bucket-join. Verificadas contra assinaturas SQLite sem alterar contadores no estimador.
- Os 103 casos P5 em `tests/test_hash_join.py`, `tests/test_sort_merge.py` e `tests/integration/test_p5_joins.py` passaram. Verificações estáticas de camada e I/O do AGENTS.md §2 e `git diff --check` limpas.

## Requisitos do enunciado cobertos

| Enunciado § | Onde está | Teste que prova |
| :--- | :--- | :--- |
| §5.1, GRACE, recursão e PK→FK | cbd/exec/hash_join.py | test_hj_vs_bruteforce, test_orientation, test_hj_recursion, test_hj_skew |
| §5.1, HASH primário nas configurações b/c/d | cbd/exec/hash_join.py | test_hj_bucket_join, test_hj_bucket_overflow_without_writes, test_hj_prepartitioned, test_hj_prepartitioned_hash_on_s, test_incompatible_real_hash_indexes |
| §5.1, sort-merge e caminhos ordenados | cbd/exec/sort_merge.py | test_smj_vs_bruteforce, test_smj_btree_no_sort, test_smj_one_ordered_side_with_minimum_work |
| §6.1, resultado verificado e contadores | tests/integration/test_p5_joins.py | test_p5_tiny_base_signatures, test_costs, test_abandon_real_temp_files |
| §7.1, memória de junções | hash join/sort-merge | test_build_overflows_page_bytes_before_bfr_limit, test_block_join_releases_previous_chunk_records, test_skew_50_times_work_against_sqlite |
| §7.2.2.a, justificativas | docs/decisoes/P5.md, Enzo_decisoes.md e capítulo 2 do relatório | test_every_decision_card_is_justified_in_the_design_chapter |
| §7.2.3, IA e estresse | ai/P5/prompts.md, raw/, review.md, stress.md, post_mortem.md | comparação ingênuo × limitado, regressões e revisores independentes |

## Pendências de integração

- A B+ real da #49 está ausente no checkout. Repetir a matriz física a–e e as contagens B+ com P3. Os casos já ordenados e mistos têm testes de contrato com dublê de B+ e adaptador P4 real.
- M=3 com duas entradas não ordenadas não é aplicável sob a interface P4, que demanda três páginas por sort. A bancada registra N/A. Medição do estresse com dois heaps usa M=6, e o caso misto em M=3 tem teste de lógica.
- Verificar a execução após eventual integração no main e fechar issues pelo fluxo do grupo. A entrega atual está no worktree local.
- Nenhum cartão provisório. D-P5-03 registra o primeiro limite lógico, revisto por D-P5-06 e completado fisicamente por D-P5-10.

## Armadilhas e verificações

- Use Python em modo UTF-8 no Windows. O valor de bfr representa média e não substitui bytes codificados. A saída só começa depois que o construtor cabe.
- Prefira leituras frias para comparar custo. Trabalho já cacheado, partições sem correspondência, nulos e término antecipado podem evitar transferências.
- Use a tabela de [review.md](review.md) para as quatro revisões. As versões anteriores e os resultados intermediários foram preservados nos logs e nas worktrees #61/#65.
