## Índice dos cartões de decisão

Gerado de `docs/decisoes/`: 22 cartões, 1 provisórios. A última coluna diz em que item da seção anterior o cartão é justificado.

| Cartão | Decisão | Status | Item |
| :--- | :--- | :--- | :--- |
| D-P1-01 | Tamanho do bloco de disco e fator de blocagem | decidida | Tamanhos de bloco; Blocagem |
| D-P1-02 | Técnica de alocação de blocos nos arquivos | decidida | Estruturas dos arquivos e dos cabeçalhos |
| D-P1-03 | Estrutura do cabeçalho do arquivo (Descritor no Bloco 0) | decidida | Estruturas dos arquivos e dos cabeçalhos |
| D-P1-04 | Gerência de blocos livres e coerência no `free_block` / `fm.free` | decidida (revista) | Estruturas dos arquivos e dos cabeçalhos |
| D-P1-05 | Contadores físicos de leitura/gravação e cabeça de disco global (`_head`) | decidida | Configurações da gerência do buffer pool |
| D-P1-06 | Tamanho do Buffer Pool e número de blocos por página | decidida | Tamanhos de bloco; Configurações da gerência do buffer pool |
| D-P1-07 | Repartição do Buffer Pool e Áreas Fixas vs. Autoajustáveis vs. Compartilhadas | decidida | Configurações da gerência do buffer pool |
| D-P1-08 | Políticas de substituição de página (`LRU` vs. `MRU`) e escolha para os testes | decidida | Configurações da gerência do buffer pool |
| D-P1-09 | Emprego de *Toss-Immediate* | decidida | Configurações da gerência do buffer pool |
| D-P1-10 | Fixação de páginas (`pin_count`), preservação de `dirty` e reserva `reserve_work` | decidida | Configurações da gerência do buffer pool |
| D-P1-11 | Gravação forçada de páginas (*Forced Output*) | decidida | Configurações da gerência do buffer pool |
| D-P1-12 | Leitura antecipada (*Prefetching*) sem vazamento de `pin`, proteção entre cursores/MRU e arquivos monotipados | decidida (revista) | Configurações da gerência do buffer pool |
| D-P6-01 | Número de buckets comum nos pares hash do bucket-join | decidida | Funções de hash |
| D-P6-02 | Memória do buffer constante em bytes no experimento de tamanho de bloco | decidida | Tamanhos de bloco; Descrição da base, dos campos e das chaves |
| D-P6-03 | Seeds por tabela e distribuições dos dados | decidida | Descrição da base, dos campos e das chaves |
| D-P6-04 | Ordem física embaralhada por permutação Feistel | decidida | Descrição da base, dos campos e das chaves |
| D-P6-05 | Validação por (contagem, soma de `hash_value` da chave lógica) | decidida | Demais escolhas |
| D-P6-06 | Tipo `INT` (não `INTEGER`) nas chaves do SQLite | decidida | Descrição da base, dos campos e das chaves |
| D-P6-07 | CPF sintético sequencial | decidida | Descrição da base, dos campos e das chaves |
| D-P6-08 | LIST como JSON e DATE como texto ISO na referência SQLite | decidida | Descrição da base, dos campos e das chaves |
| D-P6-09 | Interpretação de "índice secundário clustering" (enunciado §4.2) | provisória | Demais escolhas |
| D-P6-10 | Quais linhas o fator prefetch repete | decidida | Configurações da gerência do buffer pool |
