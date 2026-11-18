# Revisão do código gerado — P4

## 18/11/2026 — Adaptador (#54)

### O cursor reavaliava um caminho já validado

- **Arquivo:** `raw/access_v1.py`, `read_ordered`, `lookup`, `_ordered` e
  `_matching` (linhas 47–91). Categoria: corretude.
- **Detecção:** revisão independente de corretude. `JoinSide` é mutável.
  Se o chamador pedia `read_ordered` com `PRIMARY` e depois mudava o caminho
  para `SCAN`, consumir o cursor disparava `KeyError`. A revisão reproduziu
  um efeito equivalente ao mudar a coluna de um `lookup` secundário.
- **Correção:** resolver o arquivo e, quando houver, o índice na criação
  do cursor. Abrir o cursor da camada 3 somente na primeira iteração.
  `test_cursor_keeps_the_validated_access_path` demonstra os dois casos.

### Consultas com múltiplos resultados e buscas redundantes

- **Arquivo:** `tests/test_access.py`, `test_access_paths`. Categoria:
  cobertura de corretude e contagem.
- **Detecção:** revisões de contrato e I/O. Os casos de busca esgotada
  tinham no máximo uma linha. O teste também aceitava `fetch` repetido
  porque só verificava o nome do método. Um operador que perdia detalhes
  ou buscava o mesmo ponteiro duas vezes poderia passar.
- **Correção:** testar busca primária e secundária com duas linhas de mesma
  chave e conferir quais ponteiros foram buscados, na ordem e uma vez cada.

### Integração com a camada 3

- **Arquivo:** `tests/test_access.py`, `test_access_with_real_heap_and_btree`.
  Categoria: cobertura de contagem física.
- **Detecção:** revisão independente de I/O. O teste inicial resfriava o
  cache uma vez e verificava apenas os resultados e o total de pins final.
- **Correção:** usar cache frio por operação e declarar as leituras previstas
  para o heap de um bloco e a B+ de uma folha. Conferir pins após cada
  consumo ou fechamento. Os testes com armazenamento real seguem pendentes.

### Despacho da varredura física com caminho primário

- **Arquivo:** `tests/test_access.py`, `test_scan_always_reads_physical_order`.
- **Detecção:** a revisão dos testes confirmou em memória que substituir
  `scan()` por `range()` só em `PRIMARY` B+ passava com os casos iniciais.
  Isso pode trocar as dicas de cache usadas pela varredura física.
- **Correção:** exercitar `read_all` com caminho primário em hash e B+ e
  exigir exatamente uma chamada a `scan()`.

### Ponteiro opaco no caminho secundário

- **Detecção:** a revisão dos testes confirmou em memória que converter o
  ponteiro para `int` antes do `fetch` passava nos dublês iniciais. Isso
  quebraria o `RID` de heap/hash e a tupla da B+ (arquitetura §5.4).
- **Correção:** usar os dois formatos de ponteiro nos dublês de índice e
  verificar que `fetch` recebe cada ponteiro no formato original.

## 18/11/2026 — Seleções (#59)

- **Faixa em B+ e nulos** (`raw/selection_v1.py`, `_primary_range` e
  `_secondary`). A revisão observou que a varredura exclui nulos e os
  adaptadores de faixa dependiam de o cursor também fazer isso. Um dublê que
  entrega a chave nula reproduz saída incompatível com o filtro SQL. A
  seleção deve aplicar a mesma regra de nulos em todos os caminhos.
- **Extremo inclusivo na estimativa** (`_range_share`). Uma faixa como
  `cr >= max(cr)` era estimada sem resultados porque o intervalo recortado
  tinha largura zero. A consulta pode encontrar o máximo. O caso pontual
  deve usar a média de registros por valor distinto quando o extremo pertence
  ao intervalo.
- **Listas de ponteiros no custo secundário** (`select_range_cost`). A
  revisão de I/O mostrou que o modelo contava folhas e fetches, mas fora
  das folhas o índice também precisa ler listas de ponteiros. O modelo
  deve incluir a proporção estimada de `overflow_blocks` no intervalo.
- **Contrato da bancada.** A revisão confirmou que passar `sort_ptrs` aos
  estimadores da arquitetura resulta em `TypeError`. O ajuste entre partes
  permanece na #36 e em `PENDENCIAS_EXTERNAS.md`.
- **Limite de overflow no hash NOT UNIQUE** (`select_eq_cost`). A média
  de overflow somada ao espaço dos repetidos estimava cinco leituras para
  um único balde com dois blocos de overflow. A estimativa da cadeia fica
  limitada pelo total de blocos que uma cadeia pode ter.
- **Indicador de ordenação sem resultados** (`_report`). A revisão confirmou
  que uma faixa vazia aparecia como `sort_ptrs_applied=True` mesmo sem montar
  lote. O indicador deve começar falso e mudar ao produzir uma linha do lote.
- **Cobertura de despacho e lotes.** A revisão dos testes apontou que trocar
  uma igualdade indexada por varredura ainda devolveria a mesma tupla, e que
  ordenar somente o primeiro lote passaria na comparação por conjunto.
  Os testes passam a exigir as chamadas exatas e a ordem de todos os lotes.
- **Cobertura de `close()` e custo de igualdade secundária.** Remover o
  fechamento explícito passava porque o dublê era destruído no CPython.
  O teste guarda uma referência ao cursor e verifica que foi liberado.
  A estimativa A4 agora é conferida tanto para hash quanto para B+.

## 18/11/2026 — Nested-loop (#60)

- **Passadas subestimadas quando o limite por registros é arredondado**
  (`raw/nested_loop_v1.py`, `_block` e `nl_join_cost`). A revisão encontrou
  um caso com quatro linhas em três blocos, `M = 3` e `bfr = 4/3`. O lote
  aceita no máximo um registro e o operador varre o interno quatro vezes,
  mas a estimativa do livro previa três. O custo deve usar o máximo entre
  a conta por blocos e a conta por registros, com o mesmo limite de lote.
  A ocupação variável por bytes ainda pode fazer o medido divergir.
- **Arredondamento binário do limite médio** (`raw/nested_loop_v1.py`,
  `_block`). Com 501 linhas em 62 páginas, `int(62 × (501/62))` pode dar
  500. A divisão inteira de `k × n_records` por `n_blocks` implementa
  `floor(k × bfr)` sem arredondamento anterior.
- **Dublê de índice ligava um nome indefinido** (`tests/test_nested_loop.py`,
  `make_side`). Os casos `SECONDARY` falharam com `NameError` no primeiro
  teste. O índice do dublê deve usar `table.data`, o arquivo criado para a tabela.
- **Cenário LRU × MRU mascarado pelo pin do externo** (`test_bnl_flooding`).
  Na revisão por traço de frames, o cenário com quatro blocos internos e
  três frames DATA podia produzir as mesmas quinze leituras nas duas
  políticas. O teste agora usa seis blocos internos e cinco frames para
  expor reuso mesmo quando um frame está fixado pelo externo. A contagem
  real segue pendente.
- **Cobertura do BNL por posição, ocupação e duração de lotes.** As
  mutações poderiam passar quando todas as chaves ocupavam o primeiro
  campo, um lote ficava retido após seu uso ou o limite incluía somente
  cargas estritamente menores que a página. Os testes cobrem posições
  distintas, encaixe exato, referência a lotes descartados e externo vazio.

## 18/11/2026 — Ordenação externa (#58)

- **Cursor aberto após falha de validação** (`raw/external_sort_v1.py`,
  `external_sort`). A revisão reproduziu um cursor já consumido até a
  primeira linha que permanecia aberto se `mem` fosse inválido ou a chave
  não existisse. A validação precisa estar dentro do contexto que fecha a
  entrada, antes de reservar WORK.
- **Orçamento da fonte.** Um `TempFile.scan()` já utiliza uma página
  WORK. `mem = eng.M` não deixa espaço para o sort e para essa fonte. A
  arquitetura dá `mem` ao sort, e a P5 usa fontes em DATA e INDEX. O guia
  e os testes explicitam o caso de uma fonte WORK com orçamento separado.
- **Estimado × medido.** O modelo recebe só os blocos da entrada. O mesmo
  conjunto de registros pode ocupar um número diferente de blocos depois
  de ordenado (o empacotamento depende da ordem). O custo em blocos assume
  blocagem comparável entre as passadas e precisa dizer isso expressamente.
- **Cobertura de streaming e ocupação.** A revisão fez três mutações em
  memória que passavam: materializar a fusão intermediária em uma lista,
  desconsiderar o slot e zerar os seeks. Agora o teste verifica leitores
  abertos na primeira gravação de uma fusão, a cobrança de cabeçalho e
  slot e os seeks estimados para seis e cinquenta blocos.
- **Cobertura da posição da coluna.** Fixar a primeira coluna em uma
  mutação passava porque todos os casos usavam a chave nessa posição. Um
  teste em múltiplos runs pede ordenação pelo segundo campo.
