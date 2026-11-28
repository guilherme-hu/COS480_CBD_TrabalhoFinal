# Enzo Sampaio · decisões e estudo para a apresentação

Parte P5 do COS480, 27 de novembro de 2026. Escopo implementado no mini monitor:
partition-hash join (GRACE), aproveitamento de hash primário, sort-merge join,
estimativas de custo e validação. O enunciado do repositório define os requisitos
do código. O Trabalho 3 orienta a preparação da prova oral sobre transações,
concorrência e recuperação.

## Fontes consultadas

- **SKS**, Silberschatz, Korth e Sudarshan, *Database System Concepts*, 7ª ed.
  §15.4.1, pp. 701–704, ordenação externa. §15.5.2, pp. 705–706, laço em blocos.
  §15.5.4, pp. 708–711, sort-merge e custos. §15.5.5, pp. 711–718, hash join,
  particionamento recursivo, overflows, custos e híbrido. Caps. 17–19 para
  transações, concorrência e recuperação. A numeração é a impressa no livro.
- **E&N**, Elmasri e Navathe, *Fundamentals of Database Systems*, 7ª ed.
  §18.2, a partir da p. 660, ordenação externa. §18.4.1, J3/J4, pp. 669–672,
  algoritmos de junção. §18.4.2–5, pp. 670–676, buffer, custos, hash geral e
  híbrido. §18.7, pp. 681–682, pipeline. §19.5, a partir da p. 717,
  comparação de custos. Caps. 20–22 para a prova oral.
- **Trabalho 3**, cinco páginas, Temas I–VI. A avaliação prevê no mínimo três
  perguntas por aluno após a apresentação, com os capítulos de transações,
  concorrência e recuperação como referência inicial.

As fórmulas e alternativas dos livros sustentam as escolhas. Limites como
três níveis, M−2 e a precedência de variantes são escolhas deste projeto.
O histórico formal está em [docs/decisoes/P5.md](docs/decisoes/P5.md).

## Decisões de projeto, desde a primeira versão

### D-P5-01 · Escolha do construtor e atalho em memória

O construtor é o lado com menos **blocos**, pois o orçamento é em páginas.
`build="r"` e `"s"` permitem experimentar a outra orientação. Se o construtor
cabe em M−2, reserva-se a área, cria-se o dicionário limitado e varre-se o outro
lado. Caso contrário, GRACE. SKS §15.5.5.1/4 e E&N §18.4.1 J4 descrevem a
economia da execução direta. Para curso com 2 e aluno com 1.962 blocos, M=64,
o custo teórico é 1.964 transferências. GRACE teria ao menos cerca de 5.892,
sem arredondamentos. Hash híbrido reteria parte do construtor durante o
particionamento, com controle de memória mais complexo. A implementação termina
a construção antes do primeiro `yield`, permitindo desistir sem duplicar pares.

### D-P5-02 · Partições, recursão e skew

Aplicar a mesma função aos dois lados garante que chaves iguais se reencontrem.
O número de saídas é `min(M−1, max(2, ceil(1,2*b_build/(M−2))))`. O teto
deixa uma página para o cursor de entrada temporária. O mínimo de dois permite
dividir quando a primeira tentativa em memória falha. A folga de 20% usa a
heurística de SKS §15.5.5.1. Para 1.962 blocos e M=64, 38 partições têm média de
52 blocos contra limite de 62. Usar sempre 63 aumenta arquivos e páginas
parcialmente cheias. Usar quase 32 deixa pouca folga.

Cada nível usa `hash_value` do P2 com seed `100 + nível`. O determinismo
permite reproduzir a distribuição, ao contrário do hash embutido de strings
do Python. No máximo três rodadas. Se o par ainda excede a memória, laço em
blocos sobre aquele par. SKS §15.5.5.2–3 discute repartição e resolução de
overflow. E&N §18.4.4 apresenta o caso geral. A mesma chave nunca se divide
entre partições, mesmo com seed nova. Recursão ilimitada pode não terminar.
Chaves diferentes também podem colidir. O limite de três pode acionar o laço
antes de uma quarta repartição que seria útil. A ordem física da saída fica
livre, mas cada par mantém r+s.

### D-P5-03 · Primeira validação por bfr, depois revista

Na fase N1 faltavam heap, codec e sort reais. Os dublês forneciam um número
médio de registros por bloco, `bfr`. Isso permitia provar corrente de
execução, limite em registros, encerramento e multiconjunto. Esperar todas as
dependências adiaria essas verificações. A mesma média pode ocultar registros
grandes em uma partição. Por isso N1 não foi usado como evidência de memória
física ou I/O, e a integração acrescentou o controle de D-P5-10.

### D-P5-04 · Estimativa de GRACE por rodadas

Integramos operador e custo pelo mesmo plano. Em uma rodada, lê-se cada
relação, grava-se e relê-se o temporário. SKS §15.5.5.4 acrescenta até dois
acessos por partição de cada lado por páginas parciais, somando `4*n_h`.
E&N §18.4.4 apresenta a base `3(b_r+b_s)` mais a saída quando materializada.
Aqui a saída segue por iterador. Para 1.962 e 4.588 blocos com 38 partições,
uma rodada estima `3*6.550+4*38=19.802`. Para L rodadas uniformes e P
partições por lado somadas, o modelo aproxima `(2L+1)*(b_r+b_s)+4P`,
mais eventual repetição de sonda no laço final. Estimamos seeks de GRACE com
uma página por buffer de saída e deixamos o valor indefinido nos caminhos
onde não modelamos a sequência de acessos. Cache, distribuição, nulos e
ocupação podem mudar os contadores. Não se ajusta a fórmula ao teste medido.

### D-P5-05 · Reconhecer vazio por cardinalidade

Zero registros implica resultado vazio mesmo que exclusões tenham deixado
páginas alocadas. O plano retorna custo zero e evita cursor/temporário. O plano
original usava zero blocos na execução e uma regra diferente no estimador.
O revisor reproduziu 15 ou 123 transferências estimadas para execução sem
scan, dependendo do construtor. A alternativa de varrer para descobrir vazio
custa os blocos retidos. O plano integrado lê cardinalidade corrente do
arquivo, porque a tabela catalogada reflete a última análise (D-P5-10).

### D-P5-06 · Piso de um registro por página em arquivos esparsos

`bfr=0,5` descreve um registro para cada duas páginas no arquivo
em média. Um registro aceito pela blocagem não estendida ainda cabe em uma
página. Com M=3, o limite anterior virava zero. O limite por quantidade passou
a `floor((M−2)*max(1,bfr))` com bfr positivo e finito. O teto por bytes é
aplicado em conjunto. A revisão preserva o caso vazio separadamente e prova
que arredondar para cima poderia aceitar registros além da reserva lógica.

### D-P5-07 · Dois HASH incompatíveis usam GRACE

Depois de vazio/direto, dois PRIMARY HASH com N ou seed diferentes precisam
seguir GRACE comum, conforme o aceite da #65. Parear os buckets de mesmo
número pode perder resultados. Seria correto escolher um hash e alinhar o
outro pela sua função, mas isso diverge do aceite escolhido por Enzo. Com os
números anteriores, o modelo dá 19.802 para GRACE e 15.802 reaproveitando o
arquivo de 1.962 blocos, com os mesmos 38 grupos. A escolha abre mão dessa
economia possível e mantém o experimento coerente com a issue. SKS
§15.5.5.1 e E&N §18.4.4 fundamentam a necessidade de partições correspondentes.

### D-P5-08 · Reaproveitar a divisão física quando compatível

Se exatamente um PRIMARY HASH está na chave do join, agrupar cada bucket
`i` por `i % n_h` e escrever o outro lado com `bucket_of(k) % n_h`. Usar
o método público respeita inclusive a função especial do P2 para INT.
`n_h ≤ min(N,M−1)` evita grupos sem bucket de referência. Exemplo genérico
com N=10, n_h=3, hash bruto 10. O bucket 0 pertence ao grupo 0, embora
`10 % 3 = 1`. Com N e seed iguais, dois HASH primários pareiam diretamente
os buckets e suas cadeias de overflow. O custo base é `b_r+b_s` sem
gravações. No caso prepartitioned, `b_hash+3*b_outro+2*n_h`. Leitura por
índice secundário não garante essa divisão física. As variantes herdam a
distribuição do hash persistente, que pode ser ruim. Base teórica em SKS
§15.5.5.1 e E&N §18.4.4, com protocolagem no contrato HashFile do P2.

### D-P5-09 · Laço em blocos para bucket alinhado grande

Não há tamanho por bucket no contrato antes da leitura. Tenta-se build
limitado e, se exceder, reabre-se o par e usa-se laço em blocos com M−2
páginas. Isso preserva zero gravações no bucket-join e temporários apenas
do lado sem HASH no prepartitioned. Repartir criaria mais escritas e
pode ser inútil sob skew total. A desvantagem do laço é reler o probe.
Com construtor de 100, probe de 200 e M=64, a fórmula de laço dá
`100+ceil(100/62)*200=500` leituras, mais a tentativa abortada. O cache
pode reduzir a contagem física. SKS §15.5.2 e §15.5.5.3, E&N §18.4.2/4.

### D-P5-10 · Orçamento de bytes, estatísticas correntes e tipo de chave

Reservar M−2 frames e cobrar `len(codec.encode(registro))+slot`, com
limite por página descontado o cabeçalho. Um registro que não cabe na página
corrente começa outra por blocagem não estendida. Usamos também o teto em
registros, calculando bfr dos totais da partição. Isso evita depender só da
média da tabela ou da estimativa pessimista do maior registro do esquema.
Custa CPU para codificar cada entrada retida. O orçamento representa bytes de
páginas do SGBD e suas reservas, com overhead de objetos Python além desse
modelo. Teste com registros de 1.100 bytes prova o estouro antes do limite de
10 linhas. No laço, o registro de transição permanece no cursor enquanto o
lote já carregado é sondado. A reserva e os cursores juntos respeitam M.

O HashFile e o heap mantêm contagens correntes sem varrer o arquivo. O plano
usa essas contagens para evitar a omissão de registros inseridos depois de
`analyze`. As chaves PK/FK da base têm o mesmo tipo, exigido pelos dois
operadores. O hash determinístico distingue `1` e `1.0`, apesar de Python
compará-los como iguais. Rejeitar a mistura é mais previsível do que alterar
a semântica no caminho particionado. Ver SKS §15.5.5.1 e E&N §18.4.2/4.

### D-P5-11 · Sort-merge aproveita ordem B+ e divide WORK

B+ primária na coluna permite `range(None,None)` por ordem, e B+ secundária
entrega ponteiros em ordem seguidos de `fetch`. HASH não oferece essa ordem.
Em vez de ordenar sempre, usamos o adaptador real do P4. Se K lados precisam
ordenar, cada sort usa `floor(M/K)` páginas enquanto os cursores coexistem.
Sua fusão final segue diretamente para a junção. Materializar a saída
ordenada acrescentaria aproximadamente uma escrita e uma releitura por bloco.
SKS §15.4.1 e §15.5.4, E&N §18.2, §18.4.1 J3 e §18.7.

O sort P4 exige três páginas. Dois lados sem ordem exigem M≥6. Um lado
ordenado permite um sort com M=3. Com dois lados já ordenados não há reserva
para sort. Indicar `NotApplicable` sob memória insuficiente faz a bancada
marcar aquela configuração N/A. Acrescentar um sort alternativo em M=3
duplicaria responsabilidade do P4 e exigiria outra estratégia de
materialização. A B+ real depende da #49. Os testes de ordem simulam apenas
o cursor P3, e os casos mistos usam o sort, WORK e o adaptador reais.

### D-P5-12 · Usar a unicidade real do mestre

Avançar a menor chave quando diferem. Quando iguais, emitir r+s e avançar
apenas o detalhe. Se há três inscrições para o mesmo aluno, o mestre
participa dos três pares. Descartar `None` mantém a igualdade SQL.
Exigir UNIQUE em pelo menos um lado permite usar só o registro corrente de
cada entrada. O algoritmo geral de SKS §15.5.4.1 armazena grupos quando ambos
podem repetir. Para PK→FK, E&N §18.4.1 J3 descreve a simplificação. Validar
as duas orientações evita perder casos em que o mestre está em s.

### D-P5-13 · Custo independente por entrada e saída em fluxo

Somar `external_sort_cost` com o mesmo M por lado do operador, ou
`h−1+folhas` para B+ primária. Para B+ secundária, estimar
`h−1+folhas+postagens+n*fetch_cost`. Assim as buscas por ponteiro e páginas
de listas de ponteiros aparecem na estimativa. Uma chave secundária não
agrupada pode tocar muitos blocos aleatórios, de modo que ordenar após scan
pode ser mais barato. A matriz compara os caminhos definidos e não troca
silenciosamente a configuração. A união de fluxo da última passada do sort
evita a dupla cobrança de escrita/leitura. Encerrar cedo quando um lado
termina pode economizar blocos do outro. SKS §15.5.4.2, E&N §18.4.3, §18.7
e §19.5.

### D-P5-14 · O que foi validado e por que os resultados diferem

Comparamos multiconjuntos no SQLite e no laço duplo quando queremos verificar
todos os valores e multiplicidades. Para a base universitária minúscula
usamos contagem e assinatura lógica da bancada, que não depende de ordem.
O teste de contagem usa a pilha real, cache frio e um registro de 1.200 B
por página de 2.048 B. Distribuir chaves de propósito em 12 partições
isola a primeira rodada. O ensaio de estresse usa seed 480, M=3 para hash,
M=6 para dois sorts e escala de ao menos 50×M. Separar os cenários deixa
claro o que é desvio de custo e o que é teste de pior caso.

| Ensaio | Estimado | Medido | Explicação |
| :--- | ---: | ---: | :--- |
| GRACE, 180+360 blocos, M=20 | 1.668 blocos | 1.620 blocos | A folga 4×12 prevê arredondamento que não ocorreu |
| Sort-merge, mesmos dados e M=20 | 2.700 | 2.700 | Dois sorts com 10 páginas e passadas previstas |
| Bucket-join, 11+11 buckets, M=4 | 22 leituras, 0 escritas | 22 leituras, 0 escritas | Um bloco por bucket e nenhum temporário |

No estresse de skew total, a versão ingênua reteve 400 registros do
construtor. A versão paginada usa um registro por lote de uma página,
mantendo a resposta e a liberação de recursos. Para paginar, transferiu mais
blocos: 14.515 leituras e 1.714 escritas versus 700 leituras e zero
escritas da versão sem limite. É um custo de respeitar a restrição. A
comparação justa declara o limite de memória antes de comparar tempo ou I/O.

### Decisões de implementação que valem para os dois operadores

- **Contrato e isolamento.** Os operadores retornam `r_rec+s_rec` e metadados
  de plano sem alterar assinaturas, tabela ou formato de página. TempFile e
  cursores são fechados em sucesso, abandono e erro. Páginas sujas do
  temporário descartado podem nunca ir ao disco. Esse comportamento explica
  por que medir apenas o tamanho dos temporários superestima gravações.
- **Separação da lógica e da medida.** O custo usa metadados e nunca executa
  o join. O contador observa de fato transferências DATA/WORK/INDEX. Dublês
  ajudam a verificar caminhos B+ ainda ausentes. Casos com dublês não
  embasam números de disco. Para resultados completos de a–e, integrar
  a B+ real e rodar a bancada nessa versão.
- **Pilha e versões.** Integração feita em worktree `p5/entrega` sobre
  `baae948`, mantendo as worktrees intermediárias. O código bruto anterior
  e os novos snapshots ficam em `ai/P5/raw/`. A comparação com o código
  ingênuo mede a consequência de materializar todo o build.
- **Correções defendíveis.** Uma referência do `for` retinha um registro
  entre lotes. Medimos [2,3,3] antes e [2,2,2] depois. Uma chamada de
  contabilização foi tirada de um `assert` porque `python -O` remove
  asserts. Testes de fronteira distinguem `floor` de `ceil`, casos
  de build invertido e o encerramento de cursores ativos.

## Perguntas P5 para responder sem consultar

1. **Como você garante que a mesma chave não se perde?** A mesma função
   e a mesma seed rotulam os dois lados de cada rodada. Quando reaproveito
   HASH, alinho pelo bucket físico em vez de usar outro hash.
2. **O que impede que o GRACE fique recursando para sempre?** Teto de três
   níveis. Repetições da mesma chave não se separam, então o laço em blocos
   processa o par grande sem ultrapassar a memória.
3. **Por que ordenar as entradas pode não compensar?** Se vêm de B+ na
   chave, já chegam ordenadas. Se usam B+ secundária, o custo de buscar
   cada tuple por ponteiro pode superar um scan seguido de sort.
4. **O que mede a sua fórmula?** Transferências de páginas estimadas.
   Tempo também depende de CPU de codec/hash e seeks. Cache e páginas de
   temporários nunca escritas mudam as contagens reais.
5. **Como provou a multiplicidade e a limpeza?** Oráculo por multiconjunto,
   `None` e mestre nos dois lados, além de fechar após cinco tuplas,
   conferir `pinned()==0` e comparar arquivos antes/depois.
6. **Quando um join cabe em M=3?** O hash tem uma página de construção e
   duas disponíveis para cursores temporários. No sort-merge com uma
   entrada já ordenada, as três páginas atendem o único sort. Dois sorts
   simultâneos precisam de pelo menos seis sob o contrato do P4.

## Roteiro da prova oral do Trabalho 3

**I. Transações e escalonamentos** · SKS cap. 17, especialmente §17.1,
§17.3 e §17.6. E&N cap. 20, especialmente §20.1–5.

- Numa transferência, o débito e o crédito compõem uma transação. Uma
  conta pode ser um item lógico. A granularidade do bloqueio pode ser
  registro, página ou tabela. **A**tomicidade é tudo ou nada.
  **C**onsistência preserva invariantes quando a transação é correta.
  **I**solamento restringe interferências e **D**urabilidade preserva um
  commit após falhas cobertas. Estados: ativa, parcialmente confirmada,
  confirmada, falha, abortada e terminada. O último comando não é por si
  só confirmação durável.
- Ensaiar cinco anomalias com pequenas sequências: atualização perdida,
  leitura suja, leitura não repetível, resumo inconsistente e fantasma.
  Em um fantasma, uma consulta por faixa repetida encontra registros
  novos porque outra transação inseriu uma linha que satisfaz o predicado.
- Escalonamento é a ordem intercalada das operações que mantém a ordem
  interna de cada transação. Conflito exige transações distintas, mesmo
  item e ao menos uma escrita. Aresta Tᵢ→Tⱼ se um conflito ocorre nessa
  ordem. Grafo acíclico implica serializabilidade por conflito. Por
  visão, preservar leituras iniciais, quem forneceu cada valor e escritor
  final. Escritas cegas permitem serializáveis por visão que não são
  serializáveis por conflito.
- Se T₂ lê um valor de T₁, recuperação exige T₁ confirmar primeiro.
  Sem cascata exige que a própria leitura só ocorra após o commit de T₁.
  Estrito impede também escrever item alterado por transação pendente.
  Estrito implica sem cascata, que implica recuperável. Essas propriedades
  são independentes do teste de ciclo do grafo.
  Com `w1(X), r2(X), c2, a1`, T₂ confirmou um valor que foi desfeito,
  então o escalonamento é irrecuperável. Com `w1(X), r2(X), c1, c2`,
  os commits estão na ordem correta, mas houve leitura pendente.
  Com `w1(X), c1, r2(X), c2`, a leitura também é sem cascata.
- Níveis SQL para revisar: Read Uncommitted, Read Committed, Repeatable
  Read e Serializable. Na classificação clássica, avançam de permitir
  leitura suja a proibir leitura suja, não repetível e fantasma. A
  implementação de produto pode oferecer garantias adicionais.

**II/III. Bloqueios, 2PL e granularidade** · SKS §18.1–3, E&N §21.1 e §21.5.

- Lock binário distingue livre/ocupado. Lock S permite leitores simultâneos.
  Lock X exclui outros acessos incompatíveis. A tabela de locks associa
  objeto, detentores, modo e fila. Upgrade S→X pertence ao crescimento e
  downgrade X→S ao encolhimento do 2PL, se a conversão for permitida.
- 2PL básico: após liberar o primeiro lock, não tomar novos. Garante
  serializabilidade por conflito, pode ter deadlock e não garante por si
  só recuperação sem cascata. Conservativo adquire tudo antes de começar
  para evitar deadlock. Estrito mantém X até commit/abort. Rigoroso mantém
  S e X. Evitar confundir **duas fases de locking** com **two-phase commit**
  distribuído.
- Deadlock é ciclo no grafo de espera. Detectar, escolher vítima, desfazer
  e reiniciar. Starvation é espera ou reinício continuado de um participante,
  possível mesmo sem ciclo. Envelhecimento ou fila justa pode ajudar.
  Wait-die: velho espera, jovem aborta. Wound-wait: velho preempta jovem,
  jovem espera pelo velho. Conservar o timestamp no reinício dá chance à
  transação antiga.
- Protocolo de árvore exige deter o pai para adquirir um nó posterior ao
  primeiro. Permite soltar cedo e proíbe readquirir o nó. Garante
  serializabilidade por conflito e evita deadlock sob suas regras, sem
  garantir recuperação estrita por si só. Em granularidade múltipla,
  IS/IX indicam intenção em descendentes, SIX combina S no nó e IX abaixo.
  Adquirir de cima para baixo, liberar filhos antes do pai e observar 2PL.

**IV/V. Timestamps, versões, validação e snapshot** · SKS §18.5–8,
E&N §21.2–4.

- Timestamp ordering compara o timestamp da transação com os últimos
  timestamps de leitura/escrita do item. A regra de Thomas pode ignorar
  escrita obsoleta em vez de abortar, se a leitura já ocorrida não exige
  abortar. A variante estrita evita exposição antes da confirmação.
- MVCC mantém versões para leitores compatíveis. Em timestamp ordering
  multiversão, o leitor escolhe a versão com maior timestamp de escrita
  que não ultrapasse seu timestamp. No MV2PL, o write pode criar versão
  e a certificação da escrita coordena quando ela passa a ser visível.
  Garantias dependem do protocolo e da visibilidade de escritas pendentes.
- Controle otimista separa leitura/cálculo, validação e escrita. Faz
  sentido com poucos conflitos e pode desperdiçar trabalho de
  transações longas sob disputa.
- Snapshot isolation lê versões confirmadas do início e impede escritas
  concorrentes conflitantes no mesmo item. Exemplo de write skew:
  dois plantonistas veem dois ativos e desativam pessoas diferentes.
  Ambos escrevem itens distintos e podem violar o mínimo de um ativo.
  SSI procura dependências perigosas de leitura/escrita e aborta para
  impedir anomalias serializáveis. Preparar a diferença entre snapshot
  da visão e execução equivalente a uma ordem serial.

**VI. Recuperação e sua relação com P5** · SKS cap. 19, especialmente
§19.2–4 e §19.7/9. E&N cap. 22, especialmente §22.1–5 e §22.7.

- RAM é volátil. Disco ou SSD é não volátil, mas pode falhar. Armazenamento
  estável é uma abstração construída com redundância e protocolos. Distinguir
  falha de uma transação, crash que perde RAM e falha do meio persistente.
  Backup e log arquivado recuperam perda de mídia de forma distinta de
  um rollback.
- WAL exige log de atualização persistido antes de sua página suja e
  registro de commit persistido antes de confirmar ao usuário. **Steal**
  permite expulsar uma página com alteração pendente e exige poder
  desfazer essa alteração. **No-force** permite commit sem descarregar
  todas as páginas e exige poder refazer alterações confirmadas.
  No-steal e force retiram essas respectivas necessidades sob o modelo
  simples. Em atualização diferida, páginas pendentes não vão ao disco,
  e pode bastar NO-UNDO/REDO. Em imediata com steal/no-force, UNDO/REDO.
- Checkpoint limita o trabalho de recuperação. Fuzzy registra o progresso
  sem parar toda a atividade, com informações para encontrar páginas sujas
  e transações ativas. ARIES usa análise, redo repetindo a história e undo
  dos perdedores, com registros de compensação. Idempotência significa
  que refazer o mesmo efeito não altera o resultado após a primeira vez.
- Shadow paging mantém páginas antigas alcançáveis até publicar o novo
  mapa por troca atômica de raiz. Pode dispensar UNDO/REDO no modelo
  simples, com custos de cópia, fragmentação e coleta de páginas.
  Para perda de mídia, revisar backup consistente, logs, redundância
  local e backup remoto. No SKS §19.7, verificar quando o protocolo
  one-safe, two-very-safe ou two-safe confirma diante de indisponibilidade
  do remoto, pois latência e tolerância à perda mudam. One-safe confirma
  após o log durável só no primário, podendo perder commits no failover.
  Two-very-safe espera log durável nos dois e perde disponibilidade se
  um deles estiver fora. Two-safe espera ambos quando ativos e permite
  confirmação só no primário quando o remoto está indisponível.
- **Ligação com P5.** `pin` mantém uma página no frame enquanto um cursor
  a usa. Um lock coordena transações sobre um item lógico. Um latch protege
  uma estrutura interna durante acesso concorrente. Desafixar permite a
  substituição, mas não confirma transação. As páginas WORK de um join são
  derivadas da entrada e podem ser descartadas em abandono. WAL, undo
  e redo protegem dados persistentes em um SGBD transacional. O `cold()`
  da bancada controla o estado inicial da medição e não equivale a
  checkpoint transacional.

Treine respostas com um escalonamento pequeno, o invariante exigido e o
contraexemplo. Para a implementação, acrescente a quantidade de páginas,
a fórmula e o que foi efetivamente observado.
