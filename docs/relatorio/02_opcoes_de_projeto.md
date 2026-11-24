# Opções de projeto

O enunciado (§7.2.2.a) pede a justificativa de **todas** as opções adotadas, com
a comparação com as preteridas. Este capítulo segue a ordem da lista do
enunciado: tamanhos de bloco, blocagem, base, estruturas dos arquivos e dos
cabeçalhos, campos de ordenação, funções de hash, ordem das árvores, buffer pool
e demais escolhas. Cada linha resume uma decisão: a escolha, as opções
preteridas, o motivo e o cartão de origem. Os cartões estão em
`docs/decisoes/Px.md`. Cada cartão traz as opções completas com o custo em
blocos, os impactos e o teste que confirma o efeito. A seção seguinte, gerada,
lista todos os cartões com o seu status.

> **Situação em 19/11.** Os cartões de P1 e P6 estão no `main`. Os do P4 estão na
> branch `p4/67-fechamento`. P2, P3 e P5 ainda não registraram cartões. Nas linhas
> marcadas **cartão pendente**, o texto descreve o projeto atual do guia da parte
> e ainda não tem a comparação registrada pelo dono. Nenhuma decisão da P6 está
> provisória.

## Tamanhos de bloco

| Escolha | Preterida(s) | Por quê | Cartão |
| :------------ | :-------- | :----------------- | :----- |
| B = 4096 bytes na matriz principal | 2048 e 8192 bytes como padrão | É o meio-termo entre o scan (com 8 KB seriam metade das leituras) e o acesso pontual (com 8 KB cada leitura traz o dobro de bytes para um registro de ~150 bytes). Também coincide com a página de 4 KiB do sistema operacional. Com as áreas padrão, as tabelas menores (até 27 blocos) cabem em $M - 2 = 62$ páginas e as maiores passam de $10 \times$ `DATA`. Com a memória constante em bytes (linha abaixo), esse critério vale também em 2 e 8 KB. Por isso o tamanho de bloco não é descartado: ele é medido como fator. | D-P1-01 |
| 1 página do buffer = 1 bloco | vários blocos por página; buffer muito pequeno ou muito grande | Um *miss* custa exatamente uma leitura física, e a contagem de blocos fica direta. A leitura de vários blocos seguidos é feita pelo prefetch. | D-P1-06 |
| 2048 e 8192 só no experimento de fator único, com a memória do buffer **constante em bytes** (WORK = 128, 64 e 32 páginas para B = 2048, 4096 e 8192) | mesmo número de páginas em todo B; aumentar a base | Com o mesmo número de páginas, um bloco maior também daria mais memória ao buffer, e não se saberia qual dos dois explica a diferença medida. | D-P6-02 |

## Blocagem

| Escolha | Preterida(s) | Por quê | Cartão |
| :------------ | :-------- | :----------------- | :----- |
| Registros não estendidos (*unspanned*). Registro maior que o espaço útil do bloco gera `RecordTooLarge` | registros estendidos (*spanned*) | Os registros da base têm de 33 a 156 bytes em média e no máximo cerca de 520 bytes: todos cabem num bloco de 4 KB. Partir registros só acrescentaria leituras extras e código. | D-P1-01 |
| Página com diretório de slots (*slotted page*, SKS Fig. 13.6) em todos os arquivos: cabeçalho de 10 B (nº de slots, fim do espaço livre, bytes apagados e próximo bloco, que encadeia o overflow do hash e as folhas da B+) e 4 B por registro no diretório; RID = (bloco, slot) estável | vagas fixas do tamanho máximo (SKS §13.2.1); registros colados sem diretório (E&N §16.6); híbrido com vagas fixas só para `inscricao` | Quatro tabelas têm registro variável, e o RID precisa continuar válido quando o bloco é compactado (índices e folhas da B+ apontam para ele). Vagas fixas dariam 12 500 blocos em `aluno` (contra 1 815); sem diretório, compactar quebraria os índices. O híbrido tiraria 10% da `inscricao` (4 425 → ≈3 969 blocos) mas exigiria dois formatos de página e mudaria o contrato usado por P3 e P4; o ganho seria igual em todas as configurações. | D-P2-07 |
| Registro apagado: o slot é marcado e o espaço vira buraco; o `insert` reaproveita o número de um slot apagado e compacta o bloco só quando o espaço contínuo não basta (compactação preguiçosa) | compactar a cada remoção (SKS §13.2.2); nunca compactar no bloco, só no `reorganize` do arquivo (E&N §16.6) | Compactar a cada remoção dá os mesmos blocos, mas move bytes em toda remoção e deixa velhas as referências obtidas antes, justamente no padrão do split e da fusão das folhas da B+. Nunca compactar desperdiça o espaço dos apagados: apagar e reinserir 10% de `aluno` levaria a tabela de 1 815 para ≈1 997 blocos até o `reorganize`. A matriz não apaga nada depois da carga, então nenhuma contagem dela muda. | D-P2-08 |

## Descrição da base, dos campos e das chaves

| Escolha | Preterida(s) | Por quê | Cartão |
| :------------ | :-------- | :----------------- | :----- |
| Base universitária de 6 tabelas (arquitetura §8.3) com os volumes padrão. Só `curso`, `disciplina`, `aluno` e `inscricao` entram nos experimentos | aumentar a base (~67 mil alunos); subconjuntos das tabelas | Os três joins mestre-detalhe do escopo cobrem as três combinações de tamanho com as tabelas reais, sem subconjuntos (arquitetura §0, issue #5). Com a memória constante em bytes, a base atual é adequada nos três tamanhos de bloco (D-P6-02). Campos, chaves (UNIQUE, chave composta de `inscricao`) e chaves estrangeiras estão descritos no capítulo 3. | D-P6-02 |
| Uma seed por tabela, `aluno.curso` com Zipf ($s = 0{,}7$) e `cr` normal truncada | uma seed global; tudo uniforme; skew forte | Mudar o volume de uma tabela não altera as outras. O skew moderado faz a estimativa "uniforme" dos livros errar em casos que o relatório explica, sem dominar a matriz. | D-P6-03 |
| Ordem física embaralhada por permutação Feistel | emitir em ordem de chave; `shuffle` de lista; sorteio com rejeição | Em ordem de chave, um índice secundário pareceria *clustering* por acaso. A permutação embaralha em memória constante, mesmo no domínio de $5 \times 10^8$ pares de `inscricao`. | D-P6-04 |
| CPF sintético = DRE com zeros à esquerda | CPF válido com dígitos verificadores; aleatório com conferência | É único por construção e sequencial a partir de `00000000001`, sem vir de pessoa nenhuma. Cerca de 1% dos valores (499 de 50 mil) passa por acaso no teste dos dígitos verificadores, o que não afeta nada: nenhum experimento usa `cpf`. | D-P6-07 |
| Referência SQLite com chaves `INT` (não `INTEGER PRIMARY KEY`), LIST em JSON e DATE em ISO | `INTEGER PRIMARY KEY`; tabela filha; separador; `pickle` | `INTEGER PRIMARY KEY` reordenaria a tabela pela chave e desfaria o embaralhamento. JSON e ISO voltam exatamente ao valor original e podem ser lidos. | D-P6-06, D-P6-08 |
| Formato do registro: parte fixa primeiro, depois cada campo variável precedido do seu tamanho em 2 bytes (E&N §16.4.2) | diretório (offset, tamanho) do SKS Fig. 13.5; tudo fixo no tamanho máximo; caractere separador | O resultado é a contagem de blocos: sem o offset, que é redundante, `aluno` passa de 1 962 para 1 885 blocos (−3,9%) e o código fica mais simples. A vantagem do diretório (pular direto para um campo variável) não é usada: todas as chaves e colunas de seleção são fixas. Tudo fixo daria 12 500 blocos em `aluno`; separador conflita com o `\|` do `endereco`. | D-P2-01 |
| Campo comum gravado direto; campo opcional NULL gravado como **sentinela** do tipo (INT $-2^{31}$, FLOAT NaN, DATE ordinal 0, CHAR `0xFF`, variável com tamanho `0xFFFF`), sem *null bitmap* (E&N §16.4.2) | *null bitmap* com espaço reservado (SKS Fig. 13.5); bitmap só dos opcionais; NULL sem espaço (variante do SKS p. 593); pares campo-valor | O byte do bitmap é 3% do registro de 33 B da `inscricao`: sem ele, cabem 113 registros por bloco em vez de 109, e a tabela cai de 4 588 para 4 425 blocos (o BNL `aluno ⋈ inscricao` cai 6,6%). O NULL continua ocupando o espaço do campo: os campos fixos ficam em posição constante e NULL → valor é atualizado no lugar. O `encode` recusa os valores reservados. | D-P2-02 |
| LIST (campo multivalorado) dentro do registro: depois do tamanho total, cada item com o seu tamanho em 1 byte, sem contagem; até 16 itens | contagem + tamanho de 2 B (guia); separador entre itens (E&N); espaço para o máximo de itens; tabela à parte (1FN, SKS §6.7.2) | Uma regra só no formato: campos e itens delimitados por tamanho. A contagem é redundante com o tamanho total, e o maior item do esquema tem 60 B. A tabela à parte obrigaria um join escondido em toda consulta que devolve o aluno completo; o separador confunde lista vazia com lista de um texto vazio. `aluno` cai de 1 852 para 1 830 blocos. | D-P2-03 |
| Campo estruturado (`endereco`) como uma string única que o codec não interpreta; campo derivado (ex.: idade) não armazenado, calculado quando necessário; `cr` armazenado | achatar o endereço em colunas (SKS §6.7.2, E&N §9.1); tipo estruturado no codec; idade gravada | Nenhuma consulta usa partes do endereço, e o E&N §3.3.1 admite o composto como atributo simples nesse caso; achatar daria 1 864 blocos em `aluno` (contra 1 830) e mudaria o esquema. Os dois livros mandam calcular o derivado; gravar a idade custaria 104 blocos e envelheceria a cada aniversário. O `cr` não é derivável na base (só um semestre de inscrições) e é coluna de seleção e de B+. | D-P2-04 |
| INT em 4 bytes no registro e na chave (o "integer" do E&N §16.4.1, p. 560); chave de índice e de B+ em bytes de tamanho fixo que preservam a ordem: INT em 4 e FLOAT em 8 bytes big-endian com o sinal ajustado, −0,0 normalizado, NaN e NULL recusados; DATE em 4 bytes; CHAR preenchido até `size` | INT em 8 bytes (o "long"); 4 B só no registro; inteiro do tamanho da faixa de cada coluna; comparar valores decodificados; chave de tamanho variável (SKS §14.4.3); FLOAT em 4 bytes; inteiro escalado para o `cr`; byte de prefixo para NULL | O maior INT da base é 50 000, e a chave tem o mesmo tamanho do campo no registro, uma regra só. Com 8 B, `inscricao` teria 32 B por registro e 4 425 blocos em 4 KB; com 4 B tem 24 B e 3 449 blocos (−22%), `aluno` cai de 1 815 para 1 664 e o BNL `aluno ⋈ inscricao` de 134 565 para 94 787 blocos. Na B+, o fan-out sobe de 205 para 256. Inteiro do tamanho da faixa dependeria dos dados atuais e mudaria o esquema. A B+ compara `bytes` (guia P3): a árvore não precisa conhecer tipos e a chave composta (chave, seq) vira concatenação. FLOAT em 4 bytes perde a ordem exata (6,57 vira 6,5700001…). Nenhuma coluna-chave é nula. | D-P2-05 |
| `RecordTooLarge` levantado por quem insere na página (heap e hash, com `SlottedPage.max_record(B)`); o codec não conhece o bloco | parâmetro `block_size` no `RecordCodec`; recusar o esquema inteiro se `max_size` não couber | O codec só traduz valores em bytes; quem sabe o tamanho da página é quem insere. Assim a assinatura do codec fica igual à da arquitetura e o tamanho do cabeçalho não é copiado em dois lugares. Na base, a proteção nunca dispara: o maior registro possível (989 B, `detalhes`) é menos da metade do menor limite (2 034 B em blocos de 2 KB). | D-P2-06 |

## Estruturas dos arquivos e dos cabeçalhos

| Escolha | Preterida(s) | Por quê | Cartão |
| :------------ | :-------- | :----------------- | :----- |
| Alocação contígua por arquivo, com reaproveitamento dos blocos livres | alocação encadeada; *extents* | O endereço do bloco é calculado direto pelo seu número. Um scan de $b_r$ blocos custa $b_r$ leituras, das quais só a primeira é aleatória (1 *seek*). | D-P1-02 |
| Cabeçalho no bloco 0 de cada arquivo (32 bytes mais a pilha de livres), mantido em memória enquanto o arquivo está aberto | arquivo global de descritores; ler e gravar o bloco 0 a cada `allocate` | O cabeçalho não gera I/O durante a operação medida e não cria *seeks* falsos. Cada arquivo se descreve sozinho. | D-P1-03 |
| Blocos livres numa pilha LIFO, guardada no bloco 0 e, acima de 1016 entradas (em 4 KB), em blocos de extensão encadeados gravados no `close()` | lista encadeada nos blocos livres; *bitmap* | Alocar e liberar custa $O(1)$ sem I/O. A extensão encadeada substituiu o contador de livres perdidos (revisão do PR #2). | D-P1-04 |
| Heap: inserção no último bloco, mapa de espaço livre consultado só quando há apagados, compactação dentro do bloco e `reorganize` acima de 20% de apagados | lista de registros livres (*free list*), citada no enunciado §3.2.1 | Projeto atual do guia P2 §5.1. | **cartão pendente (P2, #62)** |
| Hash: bucket = 1 bloco primário mais cadeia de overflow, bucket $i$ no bloco $2 + i$, sem diretório. Fator de carga acompanhado em `stats()` e `reorganize()` refaz o hash com o $N$ do $n$ atual | hashing dinâmico (extensível, linear) | O enunciado §3.5.1 exige hashing externo estático. Colisão por encadeamento de overflow e controle do fator de carga seguem o guia P2 §6.1. | **cartão pendente (P2, #62)** |
| Nó da B+: chave composta (chave, seq) de tamanho fixo, folhas em *slotted page* encadeadas, sem ponteiro para o pai | chave de grupo de campos (enunciado §3.4.1); ponteiro para o pai | Projeto atual do guia P3 §2: uma chave de um campo só, e a descida guarda o caminho em vez de manter ponteiros para o pai. | **cartão pendente (P3, #44)** |

## Campos de ordenação

| Escolha | Preterida(s) | Por quê | Cartão |
| :------------ | :-------- | :----------------- | :----- |
| Heap sem ordenação. B+ e hash primários em cada coluna de acesso: `curso.codigo`, `disciplina.curso`, `aluno.dre`, `aluno.curso`, `aluno.cr` e `inscricao.dre` (UNIQUE ou NOT UNIQUE conforme a coluna) | uma organização fixa por tabela | São as colunas de junção e de seleção do escopo. Com cada organização disponível na coluna que o algoritmo usa, as configurações a–e do enunciado §5.1 podem todas ser medidas. Cada variante representa um banco organizado de um jeito; o custo das cópias é só de carga, fora da medição. | D-P6-11 |
| Em NOT UNIQUE, a B+ ordena por (chave, seq), com `seq` oculto e crescente | duplicatas soltas na folha | Projeto atual do guia P3 §2: toda entrada fica única e o ponteiro lógico, estável. | **cartão pendente (P3, #44)** |

## Funções de hash

| Escolha | Preterida(s) | Por quê | Cartão |
| :------------ | :-------- | :----------------- | :----- |
| `hash_value`: FNV-1a de 64 bits com a seed misturada e finalizador splitmix64 | `hash()` do Python; `k mod N` | O `hash()` de `str` muda a cada processo (`PYTHONHASHSEED`). O que vai para disco ou define partição precisa dar o mesmo valor em toda execução (arquitetura §2). O enunciado §3.5.3 pede a função de melhor dispersão. Como todas as chaves de hash são inteiras, `k mod N` é uma alternativa real; a comparação pelo qui-quadrado de `test_dispersion` (guia P2 §4) ainda falta. | **cartão pendente (P2, #62)** |
| Hash primário estático, $N = \lceil n / (bfr \cdot 0{,}8) \rceil$, limitado a $V(A)$ em NOT UNIQUE | hashing dinâmico; $N$ fixo | Projeto atual do guia P2 §6.1. O limite por $V(A)$ evita ~2 450 buckets vazios em `aluno` organizado por `curso` (50 valores). | **cartão pendente (P2, #62)** |
| Pares do bucket-join com o mesmo $N$ (o menor $N$ padrão do par, limitado por $V(A)$) e a mesma seed | o maior $N$ padrão; $N = V(A)$ | O custo do bucket-join é $b_r + b_s$, então buckets vazios entram direto na medição. O maior $N$ enche de blocos vazios o lado que deveria ser barato. | D-P6-01 |
| Hash join: construtor = lado de menos blocos; $n_h = \lceil 1{,}2 \cdot b_{build} / (M - 2) \rceil \le M - 1$ partições, recursão até 3 níveis com nova seed e, no skew, nested-loop em blocos; com um lado em hash primário, só o outro é particionado (`prepartitioned`) | a registrar pelo P5 | Projeto atual do guia P5 §4. | **cartão pendente (P5, #71)** |

## Ordem das árvores

| Escolha | Preterida(s) | Por quê | Cartão |
| :------------ | :-------- | :----------------- | :----- |
| Fan-out $n = \lfloor (B - 8) / (k_s + 8 + 4) \rfloor + 1$ com chave de tamanho fixo. Ocupação mínima de $\lceil n/2 \rceil$ filhos nos nós internos e 50% dos bytes nas folhas. *Split* na inserção, redistribuição ou fusão na remoção. Carga em massa com 90% de ocupação | chave de tamanho variável; redistribuição na inserção | Projeto atual do guia P3 §2. | **cartão pendente (P3, #44)** |

## Configurações da gerência do buffer pool

| Escolha | Preterida(s) | Por quê | Cartão |
| :------------ | :-------- | :----------------- | :----- |
| Contadores de leitura e gravação sequencial e aleatória, globais e por arquivo, com uma cabeça de disco global | cabeça por arquivo; só o total | A cabeça global registra o *seek* quando um join alterna entre dois arquivos, como faria um braço mecânico. | D-P1-05 |
| 264 páginas no total (`CATALOG` = 8, `INDEX` = 64, `DATA` = 128, `WORK` = 64) | buffer muito pequeno ou muito grande | Satisfaz ao mesmo tempo $b_{menor} \le M - 2$ e $b_{maior} > 10 \times$ `DATA`. | D-P1-06 |
| Quatro áreas fixas, uma por tipo de página | repartição por operação (enunciado §2.2.2); pool único compartilhado; áreas que se ajustam sozinhas | $M$ fica constante e reprodutível, e um scan em `DATA` nunca expulsa a raiz da B+ em `INDEX`. | D-P1-07 |
| LRU e MRU: LRU na bateria geral e MRU no laço interno do nested-loop quando $b_S >$ `DATA`. A confirmação por operação sai do experimento da issue #16 (cartão da P6 a criar) | FIFO (ignora o reuso); Clock (só aproxima o LRU) | São os dois comportamentos opostos do SKS §13.5.2: o LRU aproveita o reuso recente e o MRU resiste ao *sequential flooding* no laço interno do nested-loop. | D-P1-08 |
| Toss-immediate por `unpin(..., toss=True)`, ligado por configuração | só a política | Páginas de um scan de passagem única são recicladas primeiro. A comparação LRU × MRU roda com o toss desligado, porque a fila de toss é FIFO e anularia o MRU. | D-P1-09 |
| `pin_count` por frame, `dirty` acumulado e `reserve_work(n)` para a memória dos operadores | marca booleana de fixação; memória fora do buffer | Vários cursores podem fixar a mesma página, nenhuma escrita se perde e dá para verificar que nenhum operador reserva mais de $M$ páginas. O limite das estruturas Python de cada operador continua sendo responsabilidade dele (arquitetura §3.4). | D-P1-10 |
| Gravação forçada com `force(page)` | só gravar na expulsão | Permite gravar já uma página crítica (o catálogo) sem desafixá-la. | D-P1-11 |
| Prefetch de $k$ blocos em `pin(seq=True)`, que protege páginas ainda não usadas e só age em arquivo declarado com `allow_prefetch` | sem prefetch; prefetch sem proteção entre cursores | Reduz *seeks* quando os acessos se intercalam, sem expulsar o que outro cursor acabou de trazer. | D-P1-12 |
| O fator prefetch mede os scans de faixa como controle e o INL maior $\bowtie$ maior com o externo em heap | só os scans; BNL; fusão de runs | Num scan de um arquivo só, o prefetch não muda leituras nem *seeks*. O efeito aparece quando o scan do externo é intercalado com buscas no índice. | D-P6-10 |

## Demais escolhas

| Escolha | Preterida(s) | Por quê | Cartão |
| :------------ | :-------- | :----------------- | :----- |
| Externo do nested-loop: no BNL, o lado de menos blocos; no INL, o lado sem caminho pesquisável e, se os dois forem pesquisáveis, o de menos registros | comparar o custo estimado das duas orientações | A regra é simples de inspecionar e usa estatísticas disponíveis antes do join. A bancada também mede as duas orientações forçadas. | D-P4-01 |
| Lote externo do BNL de $M - 2$ páginas, medido pelos bytes codificados, com teto em registros | lote pelo `bfr` médio ou pelo tamanho máximo do esquema | Respeita o limite de memória com registros variáveis e não desperdiça espaço quando as tuplas são curtas. | D-P4-02 |
| Ordenação externa: runs formadas por bytes com $M - 1$ páginas, fusões intermediárias com $M - 1$ entradas e a final com até $M$ | dimensionar pela média; limitar a fusão final a $M - 1$ | O orçamento de memória fica explícito, e há uma fusão a menos quando sobram exatamente $M$ runs. | D-P4-03 |
| Sort-merge join: $M$ dividido igualmente entre os lados que precisam ser ordenados, e a fusão do join feita em *pipeline* com a última passada de cada sort | $M$ inteiro para cada sort; materializar a saída ordenada (fórmula do livro, $b_r + b_s$ + sorts) | Projeto atual do guia P5 §3: as duas fusões finais correm ao mesmo tempo e dividem $M$, o que pode exigir mais passadas. O *pipeline* economiza uma gravação e uma leitura de cada lado. | **cartão pendente (P5, #71)** |
| Validação de cada resultado por (contagem, soma de `hash_value` da chave lógica) contra o SQLite | comparar as saídas inteiras; XOR; hash da tupla inteira | Valida em memória constante, independe da ordem da saída e detecta falta, sobra, duplicata e par trocado. Limitação: uma coluna errada fora da chave não é detectada. | D-P6-05 |
| "Índice secundário clustering" (§4.2) lido como B+ NOT UNIQUE com os ponteiros de cada chave agrupados | *clustering index* de livro; secundário sobre tabela agrupada; ponteiros ordenados | É a estrutura que o enunciado descreve. O termo dos livros (*clustering index* do E&N: arquivo ordenado pelo campo NOT UNIQUE) também é medido: é a linha B+ primária em `cr`. Assim o relatório mostra as duas leituras com número. | D-P6-09 |

> Os cartões do P4 (D-P4-01 a D-P4-03) estão na branch `p4/67-fechamento` e entram no
> índice abaixo quando o PR do P4 for mesclado.
