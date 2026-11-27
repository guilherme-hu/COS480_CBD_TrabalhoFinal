# Post-mortem — P4 (Raphael)

## O que foi construído

A parte P4 fornece o adaptador de caminhos de acesso, seleções por igualdade
e faixa, a ordenação externa consumida pelo sort-merge da P5 e nested-loop
em blocos e indexado. Os operadores devolvem iteradores e têm estimadores
de custo em blocos. O trabalho local inclui testes de escopo, vida útil de
cursores, orçamento WORK e estresse com SQLite.

## Prompts-chave e código bruto devolvido

1. Iniciar o trabalho pelo adaptador #54. [`access_v1.py`](raw/access_v1.py).
2. Implementar seleções e nested-loop #59/#60, definindo o externo automático
   e o limite de lote. [`selection_v1.py`](raw/selection_v1.py) ·
   [`nested_loop_v1.py`](raw/nested_loop_v1.py).
3. Finalizar a ordenação externa #58 com bytes codificados e fusão final
   com até `M` entradas. [`external_sort_v1.py`](raw/external_sort_v1.py).

O [log de prompts](prompts.md) registra as instruções completas. A versão
[`nested_loop_unbounded_baseline.py`](raw/nested_loop_unbounded_baseline.py)
foi criada para a comparação controlada da #64. O código inicial histórico
de nested-loop também foi executado no estresse e permanece preservado.

## Defeitos que a IA cometeu

| Defeito | Categoria | Como foi achado | Correção | Teste que impede a volta |
| :--- | :--- | :--- | :--- | :--- |
| Adaptador reavaliava o `JoinSide` mutável depois de validar. | Corretude | Dois testes alteraram o caminho ou a coluna antes de consumir o iterador e reproduziram `KeyError`. | Guardar arquivo e índice no momento da validação. | `test_cursor_keeps_the_validated_access_path` |
| B+ podia repassar nulos na faixa, quando o cursor os expunha. | SQL e cursores | Dublês expuseram a entrada nula em primário e secundário. | Filtrar a chave nula antes de entregar ou buscar o registro. | `test_unbounded_range_filters_nullable_keys_even_if_cursor_yields_them` |
| Extremo inclusivo e listas de ponteiros ficavam fora da estimativa de faixa. | Modelo de custo | Faixas equivalentes estimavam 0 e 2 matches. A revisão de I/O também detectou páginas de ponteiros ausentes. | Tratar intervalo pontual com cardinalidade média e estimar os blocos de ponteiros na faixa. | `test_range_cost_counts_matches_at_an_included_extreme`, `test_select_costs_use_documented_block_models` |
| Estimativa hash contava mais páginas que o máximo da cadeia. | Modelo de custo | Um bucket e dois overflows produziram estimativa de cinco leituras. | Limitar a estimativa a `1 + overflow_blocks`. | `test_hash_eq_cost_respects_the_largest_possible_chain` |
| BNL arredondava duas vezes e a estimativa ignorava o teto em registros. | I/O e memória | 501 registros em 62 páginas abriam lote extra. Outro teste exigia quatro passadas em vez de três previstas. | Calcular o teto em inteiros e incluir o limite em registros no custo. | `test_p4_historical_bnl_extra_pass`, `test_block_cost_accounts_for_rounded_record_limit` |
| Sort falhava na validação e deixava cursor já aberto. | Liberação de recursos | Testes consumiram uma linha, então passaram chave ou orçamento inválidos. | Validar dentro do contexto de fechamento do cursor. | `test_external_sort_validation_closes_started_input` |

Mais detalhes, incluindo lacunas encontradas por mutação, em [review.md](review.md).

## Refatorações e por quê

- Fixar a escolha do acesso evita que uma mutação tardia do `JoinSide`
  altere a leitura no meio da operação. Os cursores são fechados em erro e
  no consumo parcial.
- O BNL usa lotes limitados pelos bytes codificados e por um teto de
  registros calculado em inteiros. A estrutura em memória agrupa por chave
  dentro do lote. Cada lote continua a percorrer a relação interna.
- O sort alterna formação de runs com fusões de até `M − 1` entradas
  quando há uma gravação. A fusão final entrega tuplas diretamente e pode
  manter até `M` entradas abertas.

## Evidência: ingênuo × refatorado

Na rodada de 26/11 com P1/P2 disponíveis, as três versões devolveram as
mesmas **600 tuplas** que o SQLite com `M = 3` e 150 páginas físicas por
tabela. O controle ingênuo reteve **600 registros externos** (300.000
bytes codificados e slots). As duas versões em blocos retiveram no máximo
**4 por lote** (2.000 bytes) com `curso` externo. Com `disciplina` no
externo, o revisado manteve até **4 registros** da mesma chave por lote e
preservou o resultado.

No cenário de 501 registros em 62 páginas, com `M = 64`, o primeiro BNL
histórico fez **duas varreduras lógicas** do interno. O revisado fez **uma**.
As quatro tuplas coincidiram com o SQLite em ambos. Essa é uma melhoria
observada sobre o bruto original. A evidência de memória sobre o controle
e as contagens completas estão em [stress.md](stress.md).

## Estimado × medido

| Cenário | Estimativa sem reuso | Verificado localmente | Medição física |
| :--- | :--- | :--- | :--- |
| BNL revisado, 150 blocos por lado, `M = 3` | 22.650 leituras | 150 varreduras e resultado SQLite igual. | 22.650 leituras, zero gravações |
| BNL com 501 registros em 62 páginas | 63 leituras | Histórico fez duas passadas e revisado uma. Contando blocos sem reuso, a execução histórica corresponderia a 64 leituras. | Caso de regressão lógica |
| Sort de 150 blocos, `M = 3` | 1.050 leituras e 900 gravações | 75 runs, seis fusões e 600 registros ordenados como no SQLite. | 1.050 leituras e 900 gravações |
| BNL em cinco frames DATA | 21 leituras sem reuso | As políticas usam a mesma orientação e as mesmas tuplas. | LRU 21, MRU 17 |
| INL com hash secundário | Cinco leituras sem reuso | A segunda busca repete a mesma chave. | Três leituras, pois índice e heap ainda estão no buffer |

O número de passadas é observável sem disco real. Uma diferença física
também pode vir do buffer, como reuso de páginas ou temporários descartados
antes da gravação, ou da ocupação dos blocos após ordenar registros variáveis.
Os casos B+ e a rodada completa do grupo permanecem em
[pendências externas](PENDENCIAS_EXTERNAS.md).

## Decisões que mudaríamos hoje

- Testaríamos cedo a estimativa de todas as orientações do BNL como
  comparação com a heurística escolhida. Com `M = 64`, lados de 63 e 120
  blocos podem estimar 303 leituras com o menor externo e 246 com o maior.
  A escolha atual mantém a política simples. As orientações forçadas
  permitem conferir o desvio na bancada.
- Antes de variar `sort_ptrs` na matriz, exercitaríamos os argumentos
  reais da bancada com o estimador para detectar a diferença de contrato.
  O alinhamento segue com P6 na #36.

## Lições sobre usar IA nesta parte

Testes de resultado precisam conferir conteúdo, multiplicidade e ordem dos
campos. Testes de memória precisam limitar a entrada consumida por lote e
verificar que lotes antigos foram liberados. O SQLite confirma a semântica,
e as métricas da pilha real confirmam quantos blocos foram transferidos.
Guardar a primeira versão e reproduzir a falha antes de corrigi-la permite
explicar de onde veio a melhora na apresentação.
