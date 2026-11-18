# Estado da parte P4 — atualizado em 18/11/2026

## Pronto (com teste que prova)

- Diagnóstico e plano de execução no guia P4 §9. A bancada já tem matriz,
  referência SQLite e orquestrador, cobertos pela suíte existente.
- Implementação inicial da #54 em `cbd/exec/access.py`. Os testes isolados em
  `tests/test_access.py` cobrem caminhos, ordem, busca, `NotApplicable`, custo
  de `fetch` e fechamento dos cursores no fim antecipado ou quando `fetch` falha.
- Revisão independente em quatro focos. A regressão de `JoinSide` mutável foi
  reproduzida e corrigida. O bruto e os achados estão em `raw/access_v1.py`
  e `review.md`.
- Baseline no `main` (`f81c3c5`) com Python em UTF-8, diretório temporário
  sob `.pytest_cache`, captura do pytest desativada (`-s`) e entrada/saída
  redirecionadas no Windows para contornar falhas de ambiente:
  60 passaram e 1 foi pulado (`test_reopen_with_other_policy_real_stack`).
- Suíte após a implementação em `p4/54-adaptador-acesso`: 90 passaram e
  2 foram pulados. Ambos exigem `cbd.storage.buffer_pool`. Nenhuma medição
  física da P4 foi executada.
- #64, estresse local concluído com cinco casos aprovados e dois físicos
  pulados. SQLite confirmou 600 tuplas da junção, a ordem da disciplina e
  o caso histórico do BNL com uma passada a menos após a correção.
  [Relatório de estresse](stress.md).
- #67, pacote local consolidado na branch `p4/67-fechamento`. A suíte
  completa teve 166 aprovações e 10 pulados pela pilha real. O resumo das
  decisões, [post-mortem](post_mortem.md), guia de estudo, prompts, bruto e
  histórico de revisão estão preservados. As verificações de dependências
  e acesso a disco não retornaram violações.

## Em andamento

- Aceites entre partes estão listados em [Pendências externas](PENDENCIAS_EXTERNAS.md).
- #58 implementada em `p4/58-ordenacao-externa`. Lotes limitados pelos
  bytes codificados e última fusão com até `M` entradas, conforme D-P4-03.
  Os testes locais cobrem a saída com `M = 3`, contagem de passadas,
  formação de lotes, dois sorts simultâneos e limpeza após fechamento
  antecipado ou exceções. A suíte teve 161 aprovações e oito testes pulados
  pela ausência da pilha física. A integração com armazenamento e P5 segue aberta.
- #59 implementada localmente na branch `p4/59-selecoes`, com matriz de
  caminhos e estimativas em `tests/test_selection.py`. A revisão reproduziu
  e corrigiu falhas com nulos, extremos inclusivos e estimativas de overflow.
  A suíte teve 115 aprovações e três testes pulados. I/O físico e passagem
  de `sort_ptrs` pela bancada continuam em aberto.
- Na #60, o integrante escolheu a heurística do guia para `outer="auto"`.
  Também confirmou o limite por bytes codificados. As duas variantes e a
  estimativa foram implementadas em `p4/60-nested-loop`, com revisão em quatro
  focos e testes locais. A suíte teve 140 aprovações e seis testes pulados
  pela ausência da pilha física. Leituras frias do BNL e INL, comparação
  LRU × MRU e a cobertura de BNL na matriz a–e aguardam as partes externas.
- Conforme orientação do integrante, manter em aberto os aceites que
  dependem de P1–P3, P5 ou P6 sem acesso. A parte implementável e testável
  localmente da #54 está concluída. O aceite físico continua pendente.
- #54 aguarda integração real (#46 e #49). O teste com heap e B+ fica pulado
  até estarem disponíveis `BufferPool`, `Catalog` e `BTreeFile`. A verificação
  física dos caminhos secundários acompanha a integração dos índices.
- Rechecagem em 18/11: #46 e #49 abertas e sem PR vinculado. O PR #2 da
  camada 4 segue aberto. A execução direcionada de
  `test_access_with_real_heap_and_btree` registrou `1 skipped` porque
  `cbd.storage.buffer_pool` ainda não está disponível na branch P4.
- Segunda tentativa em cópia isolada com `cbd/storage` da branch do PR #2
  (`f64e3e3`): o `BufferPool` importou, e o teste registrou `1 skipped` em
  `cbd.files.catalog`. As verificações reais de tuplas, contagens e pins
  ainda dependem de heap, catálogo e B+.
- Reexecução dos testes do adaptador na cópia isolada: 30 passaram e a
  integração foi pulada no catálogo. A #46 depende do heap (#43), e a #49
  depende do núcleo B+ (#47). Não há referências publicadas com essas
  implementações. Quando estiverem disponíveis, repetir o teste de integração
  e a suíte completa. Publicar a branch somente após o aceite da etapa.

## Falta

- Aceites físicos de #54, #58, #59, #60 e #64. A tentativa isolada da #64
  com camada 4 do PR #2 passou pelos cinco casos locais e pulou os dois
  físicos pela falta de `cbd.files.catalog`.
- P5 consumir a ordenação. P6 ajustar `sort_ptrs` nos estimadores das
  variantes secundárias e executar a matriz. O carregador da bancada
  resolveu as seis funções de seleção, nested-loop e custo. O teste de
  assinatura reproduziu `TypeError` ao passar `sort_ptrs` aos dois custos.
- Aceite da #67 no `main`, publicação conforme condição do integrante e
  integração do post-mortem da P4 na consolidação do grupo (#72).

## Requisitos do enunciado cobertos

| Enunciado § | Onde está | Teste que prova |
| :--- | :--- | :--- |
| §5.1 a–e, leitura das entradas e buscas diretas | `cbd/exec/access.py`, chamado pelos futuros joins | `test_access_paths`, `test_scan_always_reads_physical_order` e `test_incompatible_paths_raise_before_opening_a_cursor` provam a lógica com dublês. Teste físico pendente. |
| §5.1 nested-loop em blocos e indexado | `cbd/exec/nested_loop.py` | `test_block_preserves_multiplicity_nulls_and_orientation`, `test_indexed_preserves_multiplicity_and_orientation` e `test_indexed_supports_secondary_on_either_side`. I/O físico e BNL a–e na matriz pendentes. |
| §5.1 orientação externa/interna e combinações de tamanho | `cbd/exec/nested_loop.py`, `bench/workload.py`, `bench/matrix.py` | `test_outer_choice_and_cost_use_the_same_plan` prova a escolha local e `test_join_matrix_size` prova as configurações da bancada. |
| §5.1 sort-merge, etapa de ordenação da P4 | `cbd/exec/external_sort.py` | `test_external_sort_small_M`, `test_external_sort_final_pass_accepts_all_m_inputs`, `test_external_sort_two_interleaved_sorts_share_the_budget`. Aceite físico pendente. |
| §5.2 igualdade em `dre` e faixa em `cr`, caminhos a–c | `cbd/exec/selection.py` e `bench/matrix.py` | `test_select_eq_matrix`, `test_select_range_matrix`, `test_sort_ptrs_batches_and_closes_early`, `test_select_costs_use_documented_block_models`. I/O real e ajustes na bancada pendentes. |
| §5 comparação com implementação de referência | `bench/sqlite_ref.py`, `bench/run.py` e `tests/stress/test_p4_stress.py` | `test_p4_bnl_stress_sqlite_and_work_limit` compara tuplas inteiras e assinatura. `test_p4_sort_stress_sqlite_and_work_limit` compara o sort com SQLite. Bancada física pendente. |
| §7.2.3 comparação entre bruto e revisado em estresse | `ai/P4/raw/`, `ai/P4/stress.md` e `ai/P4/post_mortem.md` | `test_p4_historical_bnl_extra_pass` e `test_p4_bnl_stress_sqlite_and_work_limit` observam passadas e pico do lote. Transferências físicas pendentes. |

## Desvios ou dúvidas sobre o contrato

- A P6 passa `sort_ptrs` aos estimadores de igualdade e faixa nas linhas
  secundárias, fora de suas assinaturas oficiais. Alinhar na #36.
- O guia da P5 referencia `_bfr_from_sample`, auxiliar privado fora da
  arquitetura §7.1. A integração precisa combinar como compartilhar esse cálculo.

## Armadilhas conhecidas / bugs abertos

- Confirmar fechamento de cursores na pilha real, contagens e consumo do
  sort pela P5. O custo da ordenação supõe a mesma ocupação dos blocos
  antes e depois de ordenar.
- A interpretação de índice secundário clustering segue a arquitetura §5.3
  enquanto a questão do grupo na #8 estiver aberta.

## Decisões provisórias a rever

Nenhuma adotada. As alternativas no plano ainda serão debatidas.
