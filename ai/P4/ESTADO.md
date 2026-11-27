# Estado da parte P4 — atualizado em 26/11/2026

## Pronto (com teste que prova)

- `p4/67-fechamento` incorpora `origin/main` `3d0eb70`. P1 e os arquivos
  heap, temporários, catálogo e hash de P2 já estão disponíveis. O único
  conflito no merge foi no `pyproject.toml`, preservado da `main`, que já
  registra o marcador `stress`.
- #54. `test_access_with_real_heap_and_hash` confirma scan heap em um
  bloco, lookup hash primário em um e hash secundário em dois, com zero
  pins após fechar os cursores. `test_access_with_real_btree` aguarda P3.
- #58. `test_external_sort_with_real_storage` confirma a ordenação em
  `M = 3`: 12 leituras e 6 gravações em seis blocos, e 300 leituras e 250
  gravações em cinquenta blocos. Dois sorts dividiram seis frames WORK
  na pilha real, com fechamento antecipado e zero pins finais. A P5 ainda
  precisa exercitar o sort-merge como consumidor.
- #59. `test_p4_heap_and_primary_hash_selections_through_the_bench`
  valida 42 seleções contra SQLite em heap e hash primário na escala
  `0,002`, além de duas faixas hash `N/A`. A igualdade em hash secundário
  passa pela API da P4 com e sem `sort_ptrs`, com dois blocos por busca.
- #60. `test_bnl_exact_cost` mede 15 leituras. `test_bnl_flooding` mede 21
  com LRU e 17 com MRU, ante 21 estimadas sem reuso. O INL com índice hash
  mede 3 leituras em vez das 5 previstas sem cache. A bancada valida 20
  cenários de junção em escala `0,002` com SQLite, cobrindo as três
  consultas e os casos suportados de a–d.
- #64. Sete casos de estresse da P4 passaram, incluindo BNL e sort físicos
  com 150 blocos por lado. O BNL revisado mediu 22.650 leituras. O sort
  mediu 1.050 leituras e 900 gravações. As saídas coincidiram com SQLite.
  [Relatório de estresse](stress.md).
- #67. Cartões, [post-mortem](post_mortem.md), guia de estudo, prompts,
  bruto e histórico de revisão estão consolidados. O índice gerado do
  relatório inclui os três cartões P4.
- A suíte da P4 teve 119 aprovados e um pulado pela B+ ausente. A suíte
  completa teve 672 aprovados e dois pulados, pela B+ ausente e pelo teste
  opcional de geração de PDF.

## Em andamento

- As revisões independentes confirmaram as contagens e orientaram as
  asserções de I/O dos testes de ordenação e bancada.

## Falta

- P3. Rodar os casos com B+ primária e secundária, incluindo a orientação
  restante da configuração d e a configuração e das junções.
- P5. Rodar o sort-merge usando os fluxos da P4.
- P6. Alinhar `sort_ptrs` nos estimadores, executar a cobertura BNL na
  matriz a–e e rodar a bateria física nas combinações reais de tamanho.
- Grupo. Aceite da #67 na `main` e consolidação do post-mortem (#72).
  Donos, issues e verificações estão em [Pendências externas](PENDENCIAS_EXTERNAS.md).

## Requisitos do enunciado cobertos

| Enunciado § | Onde está | Teste que prova |
| :--- | :--- | :--- |
| §5.1 Leitura das entradas e buscas diretas | `cbd/exec/access.py` | `test_access_paths` e `test_access_with_real_heap_and_hash`. B+ físico aguarda P3. |
| §5.1 Nested-loop em blocos e indexado | `cbd/exec/nested_loop.py` | `test_bnl_exact_cost`, `test_bnl_flooding`, `test_inl_real_index_counts_and_agrees_with_block` e `test_p4_nested_loop_through_the_bench_with_available_storage`. |
| §5.1 Orientação e consultas mestre-detalhe | `cbd/exec/nested_loop.py`, `bench/workload.py`, `bench/matrix.py` | `test_outer_choice_and_cost_use_the_same_plan` prova a escolha, e a integração com a bancada cobre as três consultas lógicas e orientações forçadas. A base pequena serve para integração, e a campanha de tamanhos físicos aguarda P6. |
| §5.1 Ordenação externa para sort-merge | `cbd/exec/external_sort.py` | `test_external_sort_with_real_storage` mede I/O e `test_external_sort_interleaves_two_real_work_streams` prova que dois fluxos cabem na área WORK real. Consumidor P5 pendente. |
| §5.2 Igualdade em `dre` e faixa em `cr`, caminhos a–c | `cbd/exec/selection.py` e `bench/matrix.py` | `test_p4_heap_and_primary_hash_selections_through_the_bench`, `test_p4_secondary_hash_selection_against_sqlite` e a matriz unitária. Faixas B+ e argumentos da bancada pendentes. |
| §5 Comparação com referência | `bench/sqlite_ref.py`, `bench/run.py` e testes P4 de integração e estresse | `test_p4_bnl_stress_physical` compara as tuplas inteiras e a assinatura, e `test_p4_sort_stress_physical` compara a ordenação. A bancada valida contagem e assinatura de chaves. |
| §7.2.3 Bruto, revisão e estresse | `ai/P4/raw/`, `ai/P4/stress.md` e `ai/P4/post_mortem.md` | `test_p4_historical_bnl_extra_pass` comprova uma varredura a menos. `test_p4_bnl_stress_physical` mede os três BNLs e `test_p4_sort_stress_physical` mede o sort. |

## Desvios ou dúvidas sobre o contrato

- `bench.run.measure` passa `sort_ptrs` aos estimadores nas linhas
  secundárias, mas o contrato reserva o parâmetro aos operadores. A
  igualdade secundária real reproduz `TypeError` pela bancada. Tratar em #36.
- A interpretação de índice secundário clustering ficou registrada em
  D-P6-09. Os testes físicos da interpretação ainda exigem a B+ secundária.

## Armadilhas conhecidas / bugs abertos

- O custo da ordenação assume blocagem comparável entre passadas. Os
  números físicos de estresse valem sob LRU, prefetch desligado e páginas
  densas do cenário controlado.
- Os casos da bancada de escala `0,002` verificam execução e resultados.
  Os rótulos de tamanhos das consultas são da matriz. A campanha física
  tem condições de tamanho próprias como documentado pela P6.

## Decisões provisórias a rever

Nenhuma adotada na integração. Permanecem as escolhas registradas em
[Decisões da P4](../../docs/decisoes/P4.md).
