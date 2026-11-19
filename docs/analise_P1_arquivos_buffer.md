# Análise Técnica — P1 (Seção 2): Gerente de Arquivos e Gerente de Buffer Pool

**Responsável:** Vivian Maria da Silva e Souza (DRE: `123205793`)  
**Módulo:** Camada 4 (`cbd/storage/`) — `FileManager`, `BufferPool` e `ReplacementPolicy`

Este documento cobre integralmente todos os itens exigidos na **Seção 2 (2.1 e 2.2)**, na **Seção 3.1 (item 1 — tamanho de bloco e fator de blocagem)**, na **Seção 6.2** e na **Seção 7.2 (item 2.a)** do enunciado ([`proposta.md`](../proposta.md)). Todas as decisões adotadas são justificadas com base nos volumes e tamanhos reais da nossa base de dados universitária ([`results/base_stats.csv`](../results/base_stats.csv)) e nas operações relacionais da carga de trabalho ([`bench/workload.py`](../bench/workload.py)), incluindo a **comparação explícita com as alternativas preteridas**.

---

## 1. Contexto Físico da Base de Dados e Escolha do Tamanho de Bloco ($4\text{ KB}$)

O enunciado (§2.1.1 e §3.1.1) solicita a definição do tamanho do bloco da memória secundária e o fator de blocagem entre **$2\text{ KB}$ ($2048\text{ B}$), $4\text{ KB}$ ($4096\text{ B}$) ou $8\text{ KB}$ ($8192\text{ B}$)**.

### Decisão Adotada: Bloco único de **$4\text{ KB}$ ($4096\text{ bytes}$)** para os testes

A tabela abaixo mostra o impacto do tamanho de bloco de $4096\text{ bytes}$ sobre cada tabela da base (`scale = 1.0`), ao lado do que ocorreria nas opções preteridas ($2048\text{ B}$ e $8192\text{ B}$):

| Categoria | Tabela | Registros ($n_r$) | Tam. Médio Registro | Fator de Blocagem ($bfr$) em $4\text{ KB}$ | Blocos ($b_r$) em **$4\text{ KB}$ (Adotado)** | Blocos em $2\text{ KB}$ *(Preterido)* | Blocos em $8\text{ KB}$ *(Preterido)* |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| **Pequenas** | `curso` | $50$ | $156,2\text{ B}$ | $25,0\text{ reg/bloco}$ | **$2$** | $5$ | $1$ |
| *(cabem em $M-2=62$)* | `disciplina` | $1\,000$ | $103,2\text{ B}$ | $37,0\text{ reg/bloco}$ | **$27$** | $55$ | $14$ |
| **Grandes** | `turma` | $100\,000$ | $51,0\text{ B}$ | $73,0\text{ reg/bloco}$ | **$1\,370$** | $2\,778$ | $676$ |
| *($> 10 \times \text{DATA} = 1280$)* | `detalhes` | $50\,000$ | $120,8\text{ B}$ | $31,9\text{ reg/bloco}$ | **$1\,565$** | $3\,216$ | $772$ |
| | `aluno` | $50\,000$ | $152,4\text{ B}$ | $25,5\text{ reg/bloco}$ | **$1\,962$** | $4\,037$ | $968$ |
| | `inscricao` | $500\,000$ | $33,0\text{ B}$ | $109,0\text{ reg/bloco}$ | **$4\,588$** | $9\,260$ | $2\,273$ |

#### Justificativa da escolha de $4\text{ KB}$ e comparação com as opções preteridas ($2\text{ KB}$ e $8\text{ KB}$):
1. **Por que $2\text{ KB}$ ($2048\text{ B}$) foi preterido:**
   - Reduz o fator de blocagem ($bfr$) pela metade e **dobra o número de blocos** de todas as tabelas (ex.: `aluno` passa de $1\,962$ para $4\,037$ blocos; `inscricao` passa de $4\,588$ para $9\,260$ blocos), dobrando o custo de I/O em todas as varreduras sequenciais (*Table Scans*, *Sort-Merge* e *Hash Join*).
   - Aumenta o *overhead* proporcional de cabeçalhos de página (*Slotted Page* de $28\text{ B}$ consome $1,37\%$ em $2\text{ KB}$ contra $0,68\%$ em $4\text{ KB}$) e reduz pela metade o *fan-out* (ordem) dos nós da Árvore B+, aumentando a altura da árvore.
   - Na tabela `disciplina` ($55$ blocos em $2\text{ KB}$), o tamanho fica muito próximo do limite da memória de trabalho ($M - 2 = 62$ blocos), deixando pouca margem para variações de ocupação em B+ e Hash.
2. **Por que $8\text{ KB}$ ($8192\text{ B}$) foi preterido:**
   - Em **buscas pontuais e acessos aleatórios via índice** (ex.: seleção pontual por `aluno.dre`, lookup em *Index Nested-Loop Join* ou `fetch(ptr)` em índice secundário sem ordenação de ponteiros), o SGBD precisa de apenas 1 registro ($\sim 152\text{ B}$), mas é obrigado a transferir $8\,192\text{ B}$ do disco — desperdiçando o dobro de largura de banda e de espaço por frame em comparação a $4\text{ KB}$.
   - Além disso, com $8\text{ KB}$, as tabelas `turma` ($676$ blocos), `detalhes` ($772$ blocos) e `aluno` ($968$ blocos) deixam de ter folga de $10\times$ sobre a área `DATA` ($128$ blocos), reduzindo a pressão sobre o Buffer Pool nos testes de junção grande $\bowtie$ grande.
3. **Por que $4\text{ KB}$ ($4096\text{ B}$) é a escolha do grupo:**
   - Garante que todos os registros da base ($33\text{ B}$ a $156,2\text{ B}$ em média; máximo possível de $\sim 520\text{ B}$ em `detalhes`) caibam com folga sem blocagem estendida (*unspanned*), com excelente fator de blocagem ($25$ a $109$ registros/bloco).
   - Equilibra o custo de *scans* sequenciais e buscas pontuais indexadas, mantém as árvores B+ baixas ($h \le 3$) e alinha 1 bloco lógico exatamente ao tamanho de página padrão do sistema operacional Linux ($4\text{ KiB}$).

---

## 2. Seção 2.1 — Gerente de Arquivos (`FileManager`)

O disco secundário é simulado por arquivos binários do sistema operacional (`<root_dir>/<name>.dat`), onde cada arquivo é internamente fatiado em blocos de tamanho fixo $B = 4096\text{ bytes}$. O bloco físico `0` é o cabeçalho (descritor) e os blocos de usuário são numerados a partir de `bno = 1`.

### 2.1.1 Técnica de Alocação nos Arquivos (Contígua vs. Encadeada vs. por Extents)

- **Decisão Adotada:** **Alocação Contígua por arquivo** (crescimento por *append* no final do arquivo + reaproveitamento de blocos liberados via pilha de livres).
  - Dentro de cada arquivo `<name>.dat`, o bloco de número `bno` ocupa exatamente o intervalo contíguo de bytes $[bno \cdot B,\, (bno + 1) \cdot B)$. Para ler ou gravar o bloco `bno`, o `FileManager` posiciona o descritor em `f.seek(bno * B)` e transfere $B$ bytes.

- **Comparação com as alternativas preteridas:**
  1. **Por que a Alocação Encadeada (*Linked Allocation*) foi preterida no `FileManager`:**
     - Na alocação encadeada, os blocos ficam espalhados em qualquer posição e cada bloco armazena um ponteiro físico para o próximo. Isso destrói o acesso direto ao $k$-ésimo bloco (que passa de $O(1)$ para $O(k)$ leituras de disco) e transforma varreduras sequenciais em uma sequência de *seeks* aleatórios à medida que o arquivo sofre inserções e remoções. *(Nota: o encadeamento lógico é usado apenas nas camadas superiores P2/P3 onde faz sentido semântico, como nos blocos de overflow de Hash e no encadeamento de folhas da B+, mas sobre blocos alocados pelo `FileManager`).*
  2. **Por que a Alocação por *Extents* foi preterida em favor da Contígua:**
     - Em um SGBD que armazena todas as tabelas dentro de um único arquivo físico de disco (*tablespace* compartilhado), a alocação contígua pura sofre de *fragmentação externa* e impede o crescimento de uma tabela quando ela encosta nos blocos de outra tabela vizinha — motivo pelo qual SGBDs de *tablespace* único alocam lotes de blocos contíguos chamados *extents* (ex.: $8$ ou $16$ blocos por vez).
     - Contudo, na nossa arquitetura (assim como no PostgreSQL e no MySQL com `innodb_file_per_table`), **cada tabela e cada índice reside em seu próprio arquivo `.dat` independente no sistema operacional**. Portanto, o crescimento de `aluno.dat` (`high_water += 1`) **jamais colide** com `turma.dat` ou `inscricao.dat`. O arquivo inteiro já constitui uma única sequência contígua elástica $[1 \dots \text{high\_water}]$.
     - Além disso, alocar por *extents* (ex.: lotes mínimos de $8$ ou $16$ blocos) causaria **fragmentação interna severa** em tabelas pequenas como `curso` (que ocupa apenas **$2$ blocos** de $4\text{ KB}$, desperdiçando $75\%$ de um *extent* de $8$ blocos) e nas dezenas de arquivos temporários (`TempFile`) criados pelas partições do *Hash Join* e pelos *runs* do *External Sort*, além de inflar artificialmente o indicador **6.1.1 (Ocupação de memória secundária)** com blocos pré-alocados vazios e exigir um diretório de *extents* no cabeçalho.

---

### 2.1.2 Estrutura do Cabeçalho do Arquivo (Descritor — Bloco 0)

- **Decisão Adotada:** O **Bloco físico `0`** (primeiros $4096\text{ bytes}$, offsets $0 \dots 4095$) de cada arquivo `.dat` armazena o descritor do arquivo, com cabeçalho fixo de $32\text{ bytes}$ (`struct.Struct("<4sIIII12x")`) seguido pela área da pilha de blocos livres:

```text
Offset (bytes)  Tamanho   Tipo    Campo        Descrição
────────────────────────────────────────────────────────────────────────────────────────────
0 .. 3          4 bytes   char[4] magic        Assinatura b"CBD1" (valida integridade)
4 .. 7          4 bytes   uint32  block_size   Tamanho do bloco em bytes (4096)
8 .. 11         4 bytes   uint32  high_water   Maior bno já alocado no arquivo (0 se vazio)
12 .. 15        4 bytes   uint32  n_free       Quantidade de bnos livres atualmente na pilha
16 .. 19        4 bytes   uint32  lost_free    Blocos liberados quando a pilha estava cheia
20 .. 31        12 bytes  bytes   reserved     Reservado para alinhamento em 32 bytes
32 .. 32+4*k    4*k bytes uint32  free_stack   Pilha de k = n_free blocos livres disponíveis
```

- **Semântica operacional e justificativa:**
  - Quando o arquivo é criado (`create`) ou aberto (`open`), o Bloco `0` é carregado para uma estrutura `_FileDesc` em memória e persistido de volta no Bloco `0` em `close()` / `close_all()`.
  - **Exclusão dos contadores experimentais:** A leitura/gravação do Bloco `0` **não** incrementa `fm.io`. O descritor do `FileManager` faz parte da infraestrutura do simulador de sistema de arquivos/disco, e não das transferências de blocos de dados/índices das operações relacionais avaliadas.
  - **Comparação com alternativas preteridas:** Guardar os descritores de todos os arquivos em um arquivo global separado foi preterido porque tornaria os arquivos `.dat` dependentes de estado externo (dificultando apagar/recriar arquivos temporários de `WORK`), enquanto ler/gravar o Bloco `0` do disco a cada `allocate()` foi preterido porque dobraria artificialmente os *seeks* durante inserções.

---

### 2.1.3 Gerência de Blocos Livres

- **Decisão Adotada:** **Pilha LIFO de `bno`s livres residente no Bloco `0` (descritor)**.
  - Com $B = 4096\text{ bytes}$ e $32\text{ bytes}$ de cabeçalho fixo, restam $4064\text{ bytes}$ no Bloco `0`, comportando até:
    $$K_{\max} = \frac{4096 - 32}{4} = 1016\text{ blocos livres}$$
  - **Alocação (`allocate(fid)`):** Se a pilha `free` não estiver vazia, remove e retorna o topo (`free.pop()`) em $O(1)$. Caso contrário, incrementa `high_water += 1` e retorna `high_water`.
  - **Liberação (`free(fid, bno)`):** Valida que $1 \le bno \le \text{high\_water}$ e que `bno` não está livre; se $|free| < K_{\max}$, empilha `bno` em `free`; caso contrário, incrementa `lost_free += 1`.
  - **Contagem exata de blocos ocupados (`n_blocks(fid)`):**
    $$\text{n\_blocks}(fid) = \text{high\_water} - |\text{free}| - \text{lost\_free}$$
  - **Integração com o Buffer Pool (`bp.free_block(fid, bno)`):** Sempre que um bloco é liberado (ex.: fusão de nós na Árvore B+), o `BufferPool` descarta eventual frame residente daquele `(fid, bno)` **sem gravar** antes de chamar `fm.free(fid, bno)`, impedindo que uma futura realocação do mesmo `bno` encontre dados antigos cacheados no buffer.

- **Comparação com alternativas preteridas:**
  1. **Lista encadeada nos próprios blocos livres em disco (*Disk Free-List*):** Preterida porque cada `allocate()` ou `free()` exigiria ler ou escrever no bloco livre no disco (ou no buffer), gerando I/O extra e *seeks* durante operações normais.
  2. **Mapa de bits (*Bitmap*) em blocos dedicados:** Preterido porque exigiria alocar e fixar blocos extras de metadados no Buffer Pool para um volume de deleções que, na nossa carga de manutenção ($-10\%$ sobre `aluno`, que tem $1962$ blocos), libera no máximo $\sim 200$ blocos — muito abaixo do limite de $1016$ entradas já disponível gratuitamente no Bloco `0`.

---

### 2.1.4 Contadores Físicos de Leitura e Gravação (Sequencial vs. Aleatório / Seeks)

- **Decisão Adotada:** Contadores acumulados **globalmente (`fm.io`)** e **por arquivo (`fm.io_by_file[fid]`)** através da estrutura `IOCounters`:
  - `seq_reads`: leituras físicas sequenciais
  - `rand_reads`: leituras físicas aleatórias (com *seek*)
  - `seq_writes`: gravações físicas sequenciais
  - `rand_writes`: gravações físicas aleatórias (com *seek*)
  - Propriedades derivadas usadas pela bancada de testes:
    $$\text{reads} = \text{seq\_reads} + \text{rand\_reads}, \quad \text{writes} = \text{seq\_writes} + \text{rand\_writes}, \quad \text{seeks} = \text{rand\_reads} + \text{rand\_writes}$$

- **Regra física de Sequencial vs. Aleatório (Cabeça de Disco Global `_head`):**
  - O `FileManager` mantém o estado `_head: tuple[int, int] | None`, representando o último endereço físico `(fid, bno)` acessado no disco (seja por leitura ou gravação).
  - Em toda chamada a `fm.read(fid, bno)` ou `fm.write(fid, bno, data)`:
    - Se `_head == (fid, bno - 1)`, a cabeça já está posicionada imediatamente antes do bloco: o acesso é **sequencial** (`seq_reads += 1` ou `seq_writes += 1`).
    - Caso contrário (`_head != (fid, bno - 1)`), houve salto de bloco ou troca de arquivo (`fid`): o acesso é **aleatório** (`rand_reads += 1` ou `rand_writes += 1`), contabilizando **$+1$ seek**.
    - Atualiza-se `_head = (fid, bno)`.
  - Ao chamar `reset_stats()` (acionado por `Engine.cold()` antes de cada medição), todos os contadores são zerados e `_head` volta para `None`.

- **Por que uma cabeça global (`_head`) em vez de uma cabeça por arquivo:**
  - Em um disco físico real (e nas fórmulas de custo do Silberschatz, Cap. 13 e Cap. 15), existe **um único braço mecânico**. Se um algoritmo de junção lê o bloco `1` de `aluno.dat` e em seguida lê o bloco `1` de `inscricao.dat` e depois o bloco `2` de `aluno.dat`, o braço precisou se deslocar entre arquivos. Uma cabeça por arquivo contaria erroneamente a leitura do bloco `2` de `aluno.dat` como "sequencial sem seek". A cabeça global captura com exatidão o custo de intercalar arquivos e o ganho do *prefetching* e do buffer em blocos.

---

## 3. Seção 2.2 — Gerente de Buffer Pool (`BufferPool`)

### 3.1 Tamanho Total do Buffer Pool e Blocos por Página (Item 2.2.1)

- **Decisão Adotada:**
  - **Blocos por página:** **$1\text{ página} = 1\text{ bloco}$** ($4096\text{ bytes}$).
  - **Número total de páginas (configuração-base):** **$264$ páginas** ($264 \times 4\text{ KB} = 1\,056\text{ KB}$), distribuídas em:
    - `CATALOG`: **$8$ páginas** ($32\text{ KB}$)
    - `INDEX`: **$64$ páginas** ($256\text{ KB}$)
    - `DATA`: **$128$ páginas** ($512\text{ KB}$)
    - `WORK`: **$64$ páginas** ($M = 64$, $256\text{ KB}$)

- **Justificativa e comparação com alternativas preteridas:**
  - **Por que $1\text{ página} = 1\text{ bloco}$:** Mapear 1 frame diretamente a 1 bloco físico elimina fragmentação interna de frame e permite comparar diretamente 1 *miss* do buffer com 1 leitura física nas fórmulas teóricas dos livros-texto. Páginas compostas por múltiplos blocos foram preteridas porque o efeito de ler blocos vizinhos de uma só vez já é obtido de forma mais flexível e configurável pelo mecanismo de **prefetching**.
  - **Por que $264$ páginas com `DATA = 128` e `WORK = 64`:** O enunciado (§5.1.2 e §6.2) exige que as tabelas pequenas **caibam inteiramente na área de buffer reservada** ($b_r \le M - 2 = 62$ páginas) e que as tabelas grandes **não caibam** no buffer. Com $M = \text{WORK} = 64$ e $\text{DATA} = 128$:
    - Todas as tabelas pequenas (`curso` $= 2$, `disciplina` $= 27$ blocos) cabem com folga em $M - 2 = 62$ páginas.
    - Todas as tabelas grandes (`turma` $= 1\,370$, `detalhes` $= 1\,565$, `aluno` $= 1\,962$, `inscricao` $= 4\,588$ blocos) são mais de **$10\times$ maiores** que `DATA` ($128$) e $> 20\times$ maiores que `WORK` ($64$), exercitando intensamente a substituição de páginas, particionamento em *Hash Join* e múltiplas *runs* no *Sort-Merge Join*.

---

### 3.2 Repartição do Buffer Pool: Por Tipo de Página vs. Por Operação e Quais Áreas (Item 2.2.2)

- **Decisão Adotada:** Repartição **híbrida por tipo de página (`PageType`) com área dedicada à operação (`WORK`)**, dividida em 4 áreas:
  1. **`CATALOG` ($8$ frames):** Exclusiva para o arquivo de metadados e estatísticas do dicionário de dados (`catalog.dat`).
  2. **`INDEX` ($64$ frames):** Exclusiva para nós internos e cabeçalhos de Árvores B+ primárias e para todos os blocos de índices secundários (B+ e Hash, incluindo listas de ponteiros).
  3. **`DATA` ($128$ frames):** Exclusiva para blocos de registros das tabelas-base (páginas de Heap, buckets primários e cadeias de overflow de Hash, e **nós folhas** da Árvore B+ primária).
  4. **`WORK` ($64$ frames):** Área de trabalho reservada exclusivamente às operações relacionais para arquivos temporários (`TempFile` de *runs* da ordenação externa e partições do *Partition-Hash Join*) e alocação de buffers em memória via `reserve_work(k)` (como o bloco externo de $M-2$ páginas do *Block Nested-Loop Join*).

- **Justificativa:** Separar `DATA`, `INDEX` e `CATALOG` por tipo de página protege índices e metadados, enquanto ter a área `WORK` dedicada às operações garante que os algoritmos de ordenação e junção disponham exatamente de $M$ páginas conhecidas pelas fórmulas de custo (ex.: $\lceil b_r / M \rceil$ *runs* iniciais no *External Sort*), sem competir com a leitura das páginas de entrada em `DATA`.

---

### 3.3 Áreas Fixas vs. Autoajustáveis vs. Compartilhadas (Item 2.2.3)

- **Decisão Adotada:** **Áreas Fixas** (cada `PageType` possui um conjunto próprio e imutável de frames e sua própria instância da política de substituição).

- **Comparação com as alternativas preteridas:**
  1. **Por que Áreas Compartilhadas (Pool Global Único) foram preteridas:** Em um pool único compartilhado, uma operação de varredura sequencial em uma tabela grande (`inscricao` com $4\,588$ blocos) ou a geração de partições em `WORK` causaria a expulsão das páginas de `INDEX` (raiz e níveis internos da B+) e de `CATALOG`. Além disso, a quantidade efetiva de memória $M$ disponível para um algoritmo variaria imprevisivelmente conforme o tráfego de dados, invalidando a comparação direta com as fórmulas analíticas de custo.
  2. **Por que Áreas Autoajustáveis (Tamanho Dinâmico com Limites) foram preteridas:** Políticas autoajustáveis alteram o tamanho das partições ao longo do tempo com base na taxa recente de faltas (*fault rate*). Em uma bancada experimental cujo objetivo é isolar variáveis (Seção 6 do enunciado), áreas autoajustáveis introduzem histerese e não-determinismo no tamanho de $M$ e de `DATA`, dificultando isolar o efeito puro da organização física dos arquivos. Áreas fixas garantem **isolamento estrito e $100\%$ de determinismo**.

---

### 3.4 Políticas de Substituição de Página: Comparação e Escolha (Item 2.2.4 e Seção 6.2)

O enunciado (§2.2.4 e §6.2) pede para definir quais políticas entre **LRU, MRU, FIFO e Clock (segunda chance)** são comparadas e **escolher a política mais apropriada à execução do teste**, justificando a escolha com base nas operações do banco.

#### A. As duas políticas implementadas (`LRU` e `MRU`) e por que `FIFO` e `CLOCK` foram preteridas
O módulo `cbd/storage/policies.py` implementa exclusivamente as **duas políticas selecionadas pelo grupo: `LRU` e `MRU`**, pelos seguintes motivos:
- **Por que `FIFO` foi preterida:** Substitui a página carregada há mais tempo no buffer (`loaded`), ignorando completamente se ela foi acessada centenas de vezes desde então (`accessed`). Assim, a página da raiz de uma Árvore B+ (carregada logo no início da consulta e usada em toda busca) seria a **primeira a ser expulsa** quando o buffer enchesse, além de sofrer da Anomalia de Belady.
- **Por que `CLOCK` (Segunda Chance) foi preterida:** O algoritmo Clock usa um ponteiro circular e um bit `ref` ($0$ ou $1$) para aproximar o comportamento do LRU com menor custo de manutenção de fila. Nas cargas de banco de dados, ele produz taxas de acerto muito próximas às do LRU, acrescentando pouca informação analítica quando já dispomos do LRU exato.
- **Por que `LRU` e `MRU` são as duas políticas implementadas:** Elas representam decisões opostas (expulsar o **menos** recente vs. o **mais** recente) que respondem de forma antagônica aos dois padrões de acesso das nossas operações relacionais:

| Padrão de Operação na nossa Base | Comportamento do **LRU** | Comportamento do **MRU** | Melhor Política |
| :--- | :--- | :--- | :---: |
| **1. Seleções e Joins com Índices B+ / Hash (Configs b, c, d, e) e Skew Zipf (`aluno.curso`)** | A raiz da B+, nós internos de nível 1 e páginas de cursos populares (Zipf) recebem `pin` frequentemente e vão para o fim da fila (`move_to_end`), **permanecendo residentes no buffer** (`hit_rate` alto). | Como a raiz da B+ ou um nó interno acabou de ser acessado na descida da árvore, ele se torna o "mais recentemente usado" e é **expulso imediatamente** na primeira falta de página! | **LRU** |
| **2. *Sort-Merge Join*, *Hash Join* e *Table Scan* simples** | Leitura sequencial de passadas únicas; com `toss_immediate` ativo, páginas já varridas são recicladas imediatamente e o LRU preserva metadados e índices. | Semelhante em passadas únicas, mas prejudica eventuais acessos repetidos a estruturas auxiliares. | **LRU** (com *toss*) |
| **3. *Block Nested-Loop Join* sem índice (Config a) com tabela interna $b_S > \text{DATA}$** (ex.: $b_S = 132 > 128$, ou `curso` $\bowtie$ `aluno`) | Sofre **Inundação Sequencial (*Sequential Flooding*)**: ao ler o bloco $129$ da tabela interna, o LRU expulsa o bloco $1$; ao ler o $130$, expulsa o $2$. Na volta seguinte do laço externo (que começa pedindo o bloco $1$), **todos os blocos dão *miss* ($0\%$ de acerto)**. | Ao ler o bloco $129$ após encher os $128$ frames, o MRU expulsa o **bloco $128$** (que acabou de ser processado nesta passada!). Os blocos **$1 \dots 127$ ficam retidos no buffer**, gerando **$127$ *hits* garantidos** em cada volta do laço externo! | **MRU** |

#### B. Escolha da Política para a Execução dos Testes da Bancada
1. **Política escolhida para a bateria geral de testes:** **`LRU`** (combinada com áreas fixas e `toss_immediate`).
   - *Justificativa:* Na grande maioria das configurações testadas (seleções pontuais e por faixa, joins nas configurações b, c, d, e com índices primários/secundários, *Sort-Merge Join* e *Partition-Hash Join*), há forte localidade temporal nos níveis superiores das árvores B+, diretórios de hash e chaves com distribuição Zipf (`curso`). Nesses cenários o **LRU** maximiza a taxa de acerto, enquanto o `toss_immediate` impede que varreduras sequenciais únicas poluam o buffer.
2. **Experimento comparativo dedicado LRU vs. MRU (*Sequential Flooding* no BNLJ):**
   - Para comprovar experimentalmente a teoria de Silberschatz (§13.5.4 e §15.5.1), executamos a comparação direta **LRU vs. MRU** no *Block Nested-Loop Join* (Config a) quando a relação interna ultrapassa a capacidade da área `DATA` ($b_{\text{inner}} > \text{DATA}$), demonstrando a queda drástica de *hit rate* do LRU por *sequential flooding* e a economia de $\text{DATA} - 2$ leituras físicas por passada proporcionada pelo **MRU**.

---

### 3.5 Emprego de *Toss-Immediate* (Item 2.2.5)

- **Decisão Adotada:** **Sim**, suportado via parâmetro `BufferConfig.toss_immediate: bool` e dica `unpin(page, toss=True)`.
- **Funcionamento e Justificativa:**
  - Quando um operador realiza uma varredura sequencial de passagem única (ex.: `HeapFile.scan()`, leitura de *runs* em `TempFile.scan()` no *External Sort* ou leitura de partições no *Hash Join*), os blocos já processados não serão reutilizados.
  - Ao chamar `unpin(page, toss=True)` com `toss_immediate=True` e `pin_count == 0`, o frame é inserido na fila prioritária `area.tossed`. Na próxima falta de página naquela área, `_victim(area)` reutiliza imediatamente o frame de `tossed` antes de consultar a política LRU/MRU.
  - **Comparação com a alternativa preterida (sem *toss-immediate*):** Sem *toss-immediate*, uma varredura sequencial sobre `inscricao` ($4\,588$ blocos) colocaria cada bloco lido no topo de recência do LRU, expulsando páginas de dados de outras tabelas que realmente seriam reutilizadas em breve.

---

### 3.6 Fixação de Blocos — *Pinned Blocks* e Reserva de Trabalho (Item 2.2.6)

- **Decisão Adotada:** Controle por **contador inteiro `pin_count` em cada `Frame`**, auxiliado por *context manager* (`with bp.page(...)`) e por reserva explícita na área de trabalho (`with bp.reserve_work(n)`).
- **Funcionamento e Justificativa:**
  - `pin(fid, bno, ptype)` e `new_page(fid, ptype)` retornam o `Frame` com `pin_count` incrementado ($\ge 1$). Múltiplos cursores podem fixar a mesma página simultaneamente (`pin_count = 2, 3...`).
  - `unpin(page, dirty=..., toss=...)` valida `pin_count > 0` e decrementa `pin_count -= 1`.
  - **Invariante de segurança:** Nenhum frame com `pin_count > 0` pode ser escolhido como vítima em `_victim(area)`, descartado em `drop_file`, liberado em `free_block` ou esvaziado em `clear`. Se uma área precisar de vítima e todos os seus frames estiverem com `pin_count > 0`, o `BufferPool` levanta `BufferFullError`.
  - **Controle estrito de memória nos algoritmos (`reserve_work(k)`):** Para cumprir a exigência do enunciado (§7.1) de que nenhum algoritmo de junção ou ordenação use estruturas Python maiores que a área de buffer reservada ($M = 64$), `bp.reserve_work(k)` retira $k$ frames da área `WORK` enquanto durar o bloco `with`, permitindo ao operador manter em memória no máximo $k \times bfr$ registros e deixando exatamente $M - k$ frames disponíveis para páginas de `TempFile`.

---

### 3.7 Gravação Forçada — *Forced Output* (Item 2.2.7)

- **Decisão Adotada:** **Sim**, implementada pelo método `bp.force(page)`.
- **Funcionamento e Justificativa:**
  - Normalmente, o `BufferPool` adota a política *lazy write-back* (*steal / no-force*): páginas modificadas recebem `dirty = True` e só são gravadas no disco quando escolhidas como vítimas de substituição (`dirty_evictions`) ou no fechamento/limpeza do buffer (`flush_all`), economizando escritas físicas quando uma mesma página sofre várias atualizações em memória.
  - Entretanto, quando uma operação exige persistência imediata no disco (como o `Catalog.analyze()` ao gravar metadados consolidados da tabela), `bp.force(page)` executa `fm.write(page.fid, page.bno, page.data)` imediatamente, define `page.dirty = False`, **mantém o `pin_count` inalterado** e incrementa o contador específico `bp.stats.forced_writes += 1`.

---

### 3.8 Substituição com Prioridade / Retenção Preferencial de Índice e Catálogo (Item 2.2.8)

- **Decisão Adotada:** **Sim, garantida estruturalmente pelo isolamento das áreas fixas (`CATALOG = 8`, `INDEX = 64`, `DATA = 128`, `WORK = 64`)**, complementada pela fila `tossed` dentro de cada área.
- **Justificativa e comparação com a alternativa preterida:**
  - Uma alternativa seria usar um pool único compartilhado com pesos dinâmicos de prioridade por tipo de página na escolha de vítima. Essa opção foi preterida porque, sob pressão extrema (ex.: *Table Scan* de $4\,588$ blocos ou *Hash Join* pesado), até páginas de peso alto acabam expulsas ou reduzem o espaço de trabalho $M$ dos operadores.
  - Com áreas fixas dedicadas para `INDEX` ($64$ páginas) e `CATALOG` ($8$ páginas), o SGBD garante **retenção preferencial absoluta**: páginas de índice e catálogo têm memória cativa e **jamais** podem ser substituídas por faltas de página de dados (`DATA`) ou de arquivos temporários (`WORK`).

---

### 3.9 Leitura Antecipada — *Prefetching* de Blocos (Item 2.2.9)

- **Decisão Adotada:** **Sim**, controlada por `BufferConfig.prefetch = k` (padrão $k = 0$ na configuração-base; testado com $k = 8$ nos experimentos de varredura).
- **Funcionamento e Justificativa:**
  - Quando uma camada superior solicita uma página em modo sequencial (`pin(fid, bno, ptype, seq=True)`) e ocorre um **miss**, após carregar o bloco `bno`, se `cfg.prefetch = k > 0`, o `BufferPool` dispara `_prefetch(area, fid, bno + 1, k)`:
  - Para cada bloco $b \in [bno + 1,\, bno + k]$, enquanto $b \le \text{high\_water}$, $b$ ainda não estiver residente no buffer e houver vítima livre (`pin_count == 0`) na área:
    1. Lê o bloco $b$ do `FileManager` (`fm.read(fid, b)`).
    2. Instala no frame com **`pin_count = 0`** e `dirty = False` (para não vazar `pin` caso o cursor pare antes do fim).
    3. Incrementa `prefetched += 1` (sem contar como `request` nem `miss` até que o cursor efetivamente peça o bloco).
  - **Ganho físico sobre a nossa base:** Como o `FileManager` usa alocação contígua, ler `bno` seguido imediatamente de `bno + 1 ... bno + k` movimenta a cabeça do disco `_head` em blocos consecutivos (`seq_reads`). Quando há duas varreduras concorrentes (ex.: intercalando duas tabelas ou duas *runs* no *Sort-Merge*), ler blocos de $1$ em $1$ causaria $1$ *seek* a cada bloco alternado; com `prefetch = 8`, o número de *seeks* cai em até $8\times$!

---

## 4. Métricas Registradas pelo Gerente de Arquivos e Buffer Pool (Seção 2.2 e Seção 6.1)

A tabela a seguir resume todos os indicadores coletados por `Engine.snapshot()` (a partir de `fm.io`, `bp.stats` e `bp.stats_by_area`) e gravados nos CSVs da bancada de testes:

| Métrica no CSV | Origem | Definição Exata |
| :--- | :--- | :--- |
| `io.reads` | `FileManager` | Total de blocos lidos fisicamente do disco (`seq_reads + rand_reads`). |
| `io.writes` | `FileManager` | Total de blocos gravados fisicamente no disco (`seq_writes + rand_writes`). |
| `io.seeks` | `FileManager` | Total de deslocamentos não-sequenciais da cabeça do disco (`rand_reads + rand_writes`). |
| `io.seq_reads` / `io.rand_reads` | `FileManager` | Leituras físicas em que `_head == (fid, bno-1)` vs. `_head != (fid, bno-1)`. |
| `io.seq_writes` / `io.rand_writes` | `FileManager` | Gravações físicas em que `_head == (fid, bno-1)` vs. `_head != (fid, bno-1)`. |
| `bp.requests` | `BufferPool` | Número total de chamadas a `pin()` (solicitações de páginas feitas pelas camadas superiores). |
| `bp.hits` | `BufferPool` | Solicitações `pin()` em que a página `(fid, bno)` já estava residente no `BufferPool`. |
| `bp.misses` | `BufferPool` | Faltas de página em `pin()` (cada *miss* resulta em 1 leitura física `fm.read`). |
| `bp.hit_rate` | `BufferPool` | Taxa de acerto do buffer: $\frac{\text{bp.hits}}{\text{bp.requests}}$ ($0,0$ se `requests == 0`). |
| `bp.dirty_evictions` | `BufferPool` | Páginas modificadas (`dirty == True`) gravadas no disco ao serem expulsas por `_victim()`. |
| `bp.forced_writes` | `BufferPool` | Gravações forçadas imediatas disparadas por `bp.force(page)`. |
| `bp.prefetched` | `BufferPool` | Blocos carregados antecipadamente pelo mecanismo de *prefetching* em `pin(..., seq=True)`. |
| `bp.<AREA>.requests/hits/misses` | `BufferPool` | Contadores desagregados para cada área (`CATALOG`, `INDEX`, `DATA`, `WORK`). |

---

## 5. Referências Exatas nos Livros-Texto da Pasta do Projeto

Todas as escolhas acima estão fundamentadas diretamente nos dois livros-texto presentes na raiz do repositório:
- **SKS:** Silberschatz, Korth & Sudarshan, *Database System Concepts*, 7th Edition (`SKS_Database System Concepts, Seventh Edition.pdf`).
- **E&N:** Elmasri & Navathe, *Fundamentals of Database Systems*, 7th Edition (`EN_Fundamentals_of_Database_Systems.pdf`).

| Decisão / Conceito da Seção 2 | Onde está no **Silberschatz (SKS)** | Onde está no **Elmasri & Navathe (E&N)** |
| :--- | :--- | :--- |
| **Tamanho de bloco ($4\text{ KB}$ vs. $2\text{ KB}$/$8\text{ KB}$) e Fator de Blocagem ($bfr$)** | **Cap. 13, §13.2** (*File Organization*, pág. 588 / PDF pág. 616): blocos padrão de $4$ a $8\text{ KB}$ e registros *unspanned*. | **Cap. 16, §16.2.1** (pág. 550) e **§16.4.3** (págs. 563–564 / PDF págs. 593–594): $bfr = \lfloor B/R \rfloor$ e $b = \lceil r/bfr \rceil$. |
| **Alocação Contígua vs. Encadeada vs. *Extents*** | **Cap. 12, Exercício 12.7** (pág. 583) e **Cap. 13, §13.3.3** (pág. 598 / PDF pág. 625): uma relação por arquivo do SO. | **Cap. 16, §16.4.4** (*Allocating File Blocks on Disk*, pág. 564 / PDF pág. 594): compara *contiguous*, *linked* e *clusters/extents*. |
| **Cabeçalho do Arquivo (Descritor — Bloco 0) carregado no `Open`** | **Cap. 13, §13.2.1** (págs. 590–591 / PDF págs. 618–619): *file header* no início do arquivo. | **Cap. 16, §16.4.5** (*File Headers*, pág. 564 / PDF pág. 594) e **§16.5** (operação `Open`, pág. 565 / PDF pág. 595). |
| **Gerência de Espaço Livre (*Free List* no cabeçalho)** | **Cap. 13, §13.2.1** (pág. 591, Fig. 13.4) e **§13.3.1** (págs. 596–597): *free list* no cabeçalho vs. *free-space map*. | **Cap. 16, §16.6** (*Heap Files*, págs. 567–568). |
| **Buffer Manager, `pin-count` e `dirty bit`** | **Cap. 13, §13.5.1 e §13.5.1.2** (*Pinned blocks*, págs. 604–605 / PDF págs. 631–632). | **Cap. 16, §16.3.1** (*Buffer Management*, págs. 557–558 / PDF págs. 587–588): define explicitamente `pin-count` e `dirty bit`. |
| **Gravação Forçada (*Forced Output*)** | **Cap. 13, §13.5.1.5** (*Forced output of blocks*, pág. 607 / PDF pág. 634). | **Cap. 16, §16.3.2** (*force-writing*, pág. 559 / PDF pág. 589). |
| **Políticas LRU, MRU, Clock e FIFO + *Sequential Flooding* no Join** | **Cap. 13, §13.5.2** (*Buffer-Replacement Strategies*, págs. 607–609 / PDF págs. 634–636, Fig. 13.13): demonstra por que **MRU** vence **LRU** no laço interno do *Nested-Loop Join*. | **Cap. 16, §16.3.2** (*Buffer Replacement Strategies*, págs. 559–560 / PDF págs. 589–590): define **LRU**, **Clock**, **FIFO** e **MRU**. |
| **Emprego de *Toss-Immediate*** | **Cap. 13, §13.5.2** (pág. 608 / PDF pág. 635): define a estratégia *toss-immediate* ao finalizar um bloco em varredura. | — |
| **Retenção Preferencial de Catálogo e Índices** | **Cap. 13, §13.5.2** (pág. 609 / PDF pág. 636): retenção de blocos do *data dictionary* (catálogo) e de *indices* em memória. | **Cap. 16, §16.3.2** (pág. 559 / PDF pág. 589): retenção do bloco raiz de índices no buffer. |
| **Leitura Antecipada (*Prefetching* / *Double Buffering*)** | **Cap. 12 e Cap. 13, §13.2** (leituras de blocos consecutivos sem *seek*). | **Cap. 16, §16.3** (*Buffering of Blocks*, págs. 556–557 / PDF págs. 586–587): leitura contínua de blocos consecutivos eliminando *seeks*. |

