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

## Em andamento

- Aceites entre partes estão listados em [Pendências externas](PENDENCIAS_EXTERNAS.md).
- Decisão da #58 confirmada pelo integrante: bytes codificados e última
  fusão com até `M` entradas. Implementação ainda pendente na branch da #58.
- #59 implementada localmente na branch `p4/59-selecoes`, com matriz de
  caminhos e estimativas em `tests/test_selection.py`. A revisão reproduziu
  e corrigiu falhas com nulos, extremos inclusivos e estimativas de overflow.
  A suíte teve 115 aprovações e três testes pulados. I/O físico e passagem
  de `sort_ptrs` pela bancada continuam em aberto.
- Na #60, o integrante escolheu a heurística do guia para `outer="auto"`.
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

- Integração da #54, seguida de #58, #59, #60, #64 e #67. O guia P4 §9 traz
  a sequência e os bloqueios.
- Medições de blocos com a pilha real, revisão do código e evidências para
  o relatório de IA conforme os componentes forem implementados.

## Requisitos do enunciado cobertos

| Enunciado § | Onde está | Teste que prova |
| :--- | :--- | :--- |
| §5.1 a–e, leitura das entradas e buscas diretas | `cbd/exec/access.py`, chamado pelos futuros joins | `test_access_paths`, `test_scan_always_reads_physical_order` e `test_incompatible_paths_raise_before_opening_a_cursor` provam a lógica com dublês. Teste físico pendente. |
| §5.1 nested-loop em blocos e configurações a–e | Matriz em `bench/matrix.py`; `cbd/exec/nested_loop.py` pendente | `test_config_paths_match_architecture` prova a matriz. Testes do operador pendentes. |
| §5.1 orientação externa/interna e combinações de tamanho | Casos em `bench/workload.py` e `bench/matrix.py`; escolha no operador pendente | `test_join_matrix_size` prova a quantidade de casos, inclusive os de lados forçados. Contagens físicas pendentes. |
| §5.1 sort-merge, etapa de ordenação da P4 | `cbd/exec/external_sort.py` pendente | Pendente. |
| §5.2 igualdade em `dre` e faixa em `cr`, caminhos a–c | `cbd/exec/selection.py` e `bench/matrix.py` | `test_select_eq_matrix`, `test_select_range_matrix`, `test_sort_ptrs_batches_and_closes_early`, `test_select_costs_use_documented_block_models`. I/O real e ajustes na bancada pendentes. |
| §5 comparação com implementação de referência | `bench/sqlite_ref.py` e `bench/run.py` já disponíveis | `test_validation_detects_extra_or_missing_tuple_without_touching_counters` cobre a validação da bancada. Integração com operadores reais pendente. |

## Desvios ou dúvidas sobre o contrato

- A P6 passa `sort_ptrs` ao estimador de faixa, fora de sua assinatura
  oficial. Alinhar na #36.
- O guia da P5 referencia `_bfr_from_sample`, auxiliar privado fora da
  arquitetura §7.1. A integração precisa combinar como compartilhar esse cálculo.

## Armadilhas conhecidas / bugs abertos

- Confirmar fechamento de cursores na pilha real. Limite de memória com
  registros variáveis e número de passadas do sort seguem em aberto.
  Cenários no guia P4 §9.
- A interpretação de índice secundário clustering segue a arquitetura §5.3
  enquanto a questão do grupo na #8 estiver aberta.

## Decisões provisórias a rever

Nenhuma adotada. As alternativas no plano ainda serão debatidas.
