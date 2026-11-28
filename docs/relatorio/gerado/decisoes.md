## Índice dos cartões de decisão

Gerado de `docs/decisoes/`: 59 cartões, 0 provisórios. A última coluna diz em que item da seção anterior o cartão é justificado.

| Cartão | Decisão | Status | Item |
| :--- | :--- | :--- | :--- |
| D-P1-01 | Tamanho do bloco de disco e fator de blocagem | decidida | Tamanhos de bloco; Blocagem |
| D-P1-02 | Técnica de alocação de blocos nos arquivos | decidida | Estruturas dos arquivos e dos cabeçalhos |
| D-P1-03 | Estrutura do cabeçalho do arquivo (Descritor no Bloco 0) | decidida (revista) | Estruturas dos arquivos e dos cabeçalhos |
| D-P1-04 | Gerência de blocos livres e coerência no `free_block` / `fm.free` | decidida (revista) | Estruturas dos arquivos e dos cabeçalhos |
| D-P1-05 | Contadores físicos de leitura/gravação e cabeça de disco global (`_head`) | decidida (revista) | Configurações da gerência do buffer pool |
| D-P1-06 | Tamanho do Buffer Pool e número de blocos por página | decidida | Tamanhos de bloco; Configurações da gerência do buffer pool |
| D-P1-07 | Repartição do Buffer Pool e Áreas Fixas vs. Autoajustáveis vs. Compartilhadas | decidida | Configurações da gerência do buffer pool |
| D-P1-08 | Políticas de substituição de página (`LRU` vs. `MRU`) e escolha para os testes | decidida | Configurações da gerência do buffer pool |
| D-P1-09 | Emprego de *Toss-Immediate* | decidida | Configurações da gerência do buffer pool |
| D-P1-10 | Fixação de páginas (`pin_count`), preservação de `dirty` e reserva `reserve_work` | decidida | Configurações da gerência do buffer pool |
| D-P1-11 | Gravação forçada de páginas (*Forced Output*) | decidida | Estruturas dos arquivos e dos cabeçalhos; Configurações da gerência do buffer pool |
| D-P1-12 | Leitura antecipada (*Prefetching*) sem vazamento de `pin`, proteção entre cursores/MRU e arquivos monotipados | decidida (revista) | Configurações da gerência do buffer pool |
| D-P2-01 | Marcação dos campos variáveis no registro | decidida | Descrição da base, dos campos e das chaves |
| D-P2-02 | Campos comuns, opcionais e null bitmap | decidida | Descrição da base, dos campos e das chaves |
| D-P2-03 | Campos multivalorados (LIST) | decidida | Descrição da base, dos campos e das chaves |
| D-P2-04 | Campos estruturados e derivados | decidida | Descrição da base, dos campos e das chaves |
| D-P2-05 | Chaves de tamanho fixo que preservam a ordem | revista | Descrição da base, dos campos e das chaves |
| D-P2-06 | Onde se checa o registro maior que o bloco | decidida | Descrição da base, dos campos e das chaves |
| D-P2-07 | Organização dos registros dentro do bloco | decidida (revista) | Blocagem |
| D-P2-08 | Registro apagado e compactação dentro do bloco | decidida | Blocagem; Estruturas dos arquivos e dos cabeçalhos |
| D-P2-09 | Heap: como achar um bloco com espaço (gerência do espaço livre) | decidida | Estruturas dos arquivos e dos cabeçalhos |
| D-P2-10 | Tamanho do RID (ponteiro para registro do heap e do hash) | decidida | Estruturas dos arquivos e dos cabeçalhos |
| D-P2-11 | Heap: quando reorganizar o arquivo inteiro | decidida | Estruturas dos arquivos e dos cabeçalhos |
| D-P2-12 | Catálogo: formato e onde guardar | decidida | Estruturas dos arquivos e dos cabeçalhos |
| D-P2-13 | Catálogo: quando gravar e forçar | decidida | Estruturas dos arquivos e dos cabeçalhos |
| D-P2-14 | Hash: a função que escolhe o bucket | decidida | Funções de hash |
| D-P2-15 | Hash: estrutura e tamanho do bucket | decidida | Estruturas dos arquivos e dos cabeçalhos |
| D-P2-16 | Hash: número de buckets N e fator de carga | decidida | Funções de hash |
| D-P2-17 | Hash: overflow, skew e controle do fator de carga | decidida | Estruturas dos arquivos e dos cabeçalhos |
| D-P2-18 | Índice hash secundário: o que cada entrada guarda e como é feito | decidida | Estruturas dos arquivos e dos cabeçalhos |
| D-P2-19 | Prefetch: onde o heap e o hash leem blocos antecipadamente | decidida | Configurações da gerência do buffer pool |
| D-P4-01 | Escolha do externo no nested-loop | decidida | Demais escolhas |
| D-P4-02 | Tamanho do lote externo do BNL | decidida | Demais escolhas |
| D-P4-03 | Lotes por bytes e fusão final da ordenação externa | decidida | Demais escolhas |
| D-P5-01 | Execução direta quando o construtor cabe na memória | decidida | Funções de hash; Escolhas da P5 — hash join e sort-merge |
| D-P5-02 | Fan-out limitado e término após três níveis | decidida | Funções de hash; Escolhas da P5 — hash join e sort-merge |
| D-P5-03 | Limite lógico com bfr nos testes isolados | revista | Escolhas da P5 — hash join e sort-merge |
| D-P5-04 | Custo por rodadas uniformes e arredondamento explícito | decidida | Escolhas da P5 — hash join e sort-merge |
| D-P5-05 | Plano vazio por quantidade de registros | decidida | Escolhas da P5 — hash join e sort-merge |
| D-P5-06 | Mínimo de um registro por página para bfr esparso em N1 | decidida | Escolhas da P5 — hash join e sort-merge |
| D-P5-07 | HASH primários incompatíveis não usam reaproveitamento | decidida | Escolhas da P5 — hash join e sort-merge |
| D-P5-08 | Alinhar o lado externo pelo bucket físico | decidida | Funções de hash; Escolhas da P5 — hash join e sort-merge |
| D-P5-09 | Excesso em par alinhado sem novas gravações | decidida | Escolhas da P5 — hash join e sort-merge |
| D-P5-10 | Teto físico de WORK e estatísticas correntes | decidida | Escolhas da P5 — hash join e sort-merge |
| D-P5-11 | Reutilizar ordem e dividir WORK entre os sorts | decidida | Demais escolhas; Escolhas da P5 — hash join e sort-merge |
| D-P5-12 | Fusão PK→FK sem acumular grupos | decidida | Escolhas da P5 — hash join e sort-merge |
| D-P5-13 | Custos por entrada do sort-merge | decidida | Demais escolhas; Escolhas da P5 — hash join e sort-merge |
| D-P5-14 | Validação com multiconjunto, cache frio e estresse separado | decidida | Escolhas da P5 — hash join e sort-merge |
| D-P6-01 | Número de buckets comum nos pares hash do bucket-join | decidida | Funções de hash |
| D-P6-02 | Memória do buffer constante em bytes no experimento de tamanho de bloco | decidida | Tamanhos de bloco; Descrição da base, dos campos e das chaves |
| D-P6-03 | Seeds por tabela e distribuições dos dados | decidida | Descrição da base, dos campos e das chaves |
| D-P6-04 | Ordem física embaralhada por permutação Feistel | decidida | Descrição da base, dos campos e das chaves |
| D-P6-05 | Validação por (contagem, soma de `hash_value` da chave lógica) | decidida | Demais escolhas |
| D-P6-06 | Tipo `INT` (não `INTEGER`) nas chaves do SQLite | decidida | Descrição da base, dos campos e das chaves |
| D-P6-07 | CPF sintético sequencial | decidida | Descrição da base, dos campos e das chaves |
| D-P6-08 | LIST como JSON e DATE como texto ISO na referência SQLite | decidida | Descrição da base, dos campos e das chaves |
| D-P6-09 | Interpretação de "índice secundário clustering" (enunciado §4.2) | decidida | Demais escolhas |
| D-P6-10 | Quais linhas o fator prefetch repete | decidida | Configurações da gerência do buffer pool |
| D-P6-11 | Campos de ordenação: uma variante por organização e coluna de acesso | decidida | Campos de ordenação |
