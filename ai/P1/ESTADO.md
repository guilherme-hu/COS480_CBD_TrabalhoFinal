# Estado da parte P1 — atualizado em 08/11

## Pronto (com teste que prova)

- **`cbd/storage/file_manager.py` (`FileManager`)**:
  - Criação (`create`), abertura (`open`), fechamento (`close`, `close_all`), remoção (`delete`) e verificação (`exists`, `is_valid_block`) de arquivos `.dat` no SO.
  - Cabeçalho descritor persistido no Bloco físico `0` (`HEADER_STRUCT` de 32 bytes + pilha LIFO de até 1016 blocos livres em $4\text{ KB}$), mantido em memória (`_FileDesc`) enquanto aberto e excluído de `fm.io`.
  - Alocação contígua (`allocate`), liberação (`free`) e contagem exata de blocos ocupados (`n_blocks = high_water - len(free) - lost_free`).
  - Contadores físicos globais (`fm.io`) e por arquivo (`fm.io_by_file[fid]`) distinguindo `seq_reads`, `rand_reads`, `seq_writes`, `rand_writes` e `seeks` via cabeça global `_head`, com reinício `_head = None` em `reset_stats()`.
  - **Testes:** `tests/test_file_manager.py` (`test_roundtrip`, `test_free_reuse`, `test_seq_vs_rand`, `test_interleaved_files_count_seeks`, `test_invalid_reads_and_deletes`).
- **`cbd/storage/policies.py` (`LRUPolicy`, `MRUPolicy`, `make_policy`)**:
  - Implementação de `LRU` e `MRU` sobre `OrderedDict`, respeitando `pin_count == 0` na escolha de vítima.
  - **Testes:** `tests/test_policies.py` (`test_lru_vs_mru_victims`, `test_sequential_flooding`).
- **`cbd/storage/buffer_pool.py` (`BufferPool`, `Frame`/`Page`)**:
  - 4 áreas fixas isoladas (`CATALOG=8`, `INDEX=64`, `DATA=128`, `WORK=64`).
  - `pin`, `unpin` (preservando `dirty`), context manager `page()`, `new_page`, `force`, `flush_file`, `flush_all`, `drop_file`, `free_block` (descartando cópia em cache), `clear`, `reserve_work`.
  - Suporte a `toss_immediate` (`area.tossed`) e `prefetch` sem vazamento de `pin` e sem auto-expulsão (`exclude=batch_frames`).
  - Contagem de `misses` estritamente após `_victim(area)` ter sucesso.
  - **Testes:** `tests/test_buffer_pool.py` (13 testes unitários) e `tests/stress/test_buffer_stress.py` (2 testes de estresse com oráculo e comparação ingênuo × refatorado).
- **Documentação e Registros de IA**:
  - Cartões de decisão `D-P1-01` a `D-P1-12` e tabela resumo em `docs/decisoes/P1.md`.
  - Análise técnica detalhada em `docs/analise_P1_arquivos_buffer.md`.
  - Guia de estudo e perguntas de fixação em `docs/estudo/P1.md`.
  - Registros de IA em `ai/P1/prompts.md`, `ai/P1/review.md`, `ai/P1/stress.md`, `ai/P1/post_mortem.md` e `ai/P1/raw/`.

## Em andamento

- Nenhuma tarefa pendente da Camada 4 (pronta para merge no `main`).

## Falta

- Após a entrega das camadas superiores pelos colegas (P2–P6), coordenar a bateria integrada de estresse (`tests/stress/`) e consolidar o `POST_MORTEM.md` da raiz do repositório (previsto no cronograma para a etapa final de integração).

## Requisitos do enunciado cobertos

| Enunciado § | Onde está | Teste que prova |
| :--- | :--- | :--- |
| **§2.1.1 / §3.1.1** Tamanho do bloco ($4\text{ KB}$) e alocação contígua | `cbd/storage/file_manager.py` (`block_size`, `allocate`, `read`, `write`), `docs/decisoes/P1.md` (`D-P1-01`, `D-P1-02`) | `tests/test_file_manager.py::test_roundtrip`, `test_seq_vs_rand` |
| **§2.1.2** Estrutura do cabeçalho do arquivo (Bloco 0) | `cbd/storage/file_manager.py` (`HEADER_STRUCT`, `_write_header`, `_read_header`), `D-P1-03` | `tests/test_file_manager.py::test_roundtrip`, `test_invalid_reads_and_deletes` |
| **§2.1.3** Gerência de blocos livres (pilha LIFO no Bloco 0 + `free_block`) | `cbd/storage/file_manager.py` (`allocate`, `free`, `n_blocks`), `cbd/storage/buffer_pool.py` (`free_block`), `D-P1-04` | `tests/test_file_manager.py::test_free_reuse`, `tests/test_buffer_pool.py::test_free_block_reuse` |
| **§2.1.4 / §6.1.2** Contadores de leitura/gravação sequencial e aleatória (`seeks`) | `cbd/storage/file_manager.py` (`_count`, `reset_stats`, `io`, `io_by_file`), `D-P1-05` | `tests/test_file_manager.py::test_seq_vs_rand`, `test_interleaved_files_count_seeks` |
| **§2.2.1** Nº de páginas no Buffer Pool e blocos por página ($1\text{ pág} = 1\text{ bloco}$) | `cbd/storage/buffer_pool.py` (`Frame`, `BufferPool.__init__`), `D-P1-06` | `tests/test_buffer_pool.py::test_hit_miss`, `test_areas_isolated` |
| **§2.2.2 / §2.2.3** Repartição em 4 áreas fixas (`CATALOG`, `INDEX`, `DATA`, `WORK`) | `cbd/storage/buffer_pool.py` (`_Area`, `_areas`, `stats_by_area`), `D-P1-07` | `tests/test_buffer_pool.py::test_areas_isolated`, `test_reserve_work` |
| **§2.2.4 / §6.2** Políticas de substituição (`LRU` e `MRU`) e escolha justificada | `cbd/storage/policies.py` (`LRUPolicy`, `MRUPolicy`, `make_policy`), `D-P1-08` | `tests/test_policies.py::test_lru_vs_mru_victims`, `test_sequential_flooding` |
| **§2.2.5** Emprego de *toss-immediate* | `cbd/storage/buffer_pool.py` (`unpin(..., toss=True)`, `_victim_or_none`), `D-P1-09` | `tests/test_buffer_pool.py::test_toss` |
| **§2.2.6** Fixação de blocos (`pin`/`unpin`), preservação de `dirty` e `reserve_work` | `cbd/storage/buffer_pool.py` (`pin`, `unpin`, `page`, `reserve_work`), `D-P1-10` | `tests/test_buffer_pool.py::test_dirty_eviction`, `test_not_dirty_no_write`, `test_buffer_full`, `test_buffer_full_nao_incrementa_misses`, `test_reserve_work` |
| **§2.2.7** Gravação forçada (*forced output*) | `cbd/storage/buffer_pool.py` (`force`), `D-P1-11` | `tests/test_buffer_pool.py::test_force` |
| **§2.2.8** Substituição com prioridade / retenção preferencial de índice e catálogo | `cbd/storage/buffer_pool.py` (áreas fixas `INDEX` e `CATALOG` isoladas de `DATA` e `WORK`), `D-P1-07` | `tests/test_buffer_pool.py::test_areas_isolated` |
| **§2.2.9** Leitura antecipada (*prefetching*) sem vazamento de `pin` nem auto-expulsão | `cbd/storage/buffer_pool.py` (`_prefetch`), `D-P1-12` | `tests/test_buffer_pool.py::test_prefetch`, `test_prefetch_nao_expulsa_o_que_acabou_de_ler` |
| **§7.2.3** Registro de uso de IA, code review crítico e teste de estresse ingênuo × refatorado | `ai/P1/prompts.md`, `ai/P1/review.md`, `ai/P1/stress.md`, `ai/P1/raw/` | `tests/stress/test_buffer_stress.py::test_buffer_random_stress_with_oracle`, `test_naive_vs_refactored_stress` |

## Desvios ou dúvidas sobre o contrato

- **`Policy.CLOCK` e `Policy.FIFO` em `make_policy` (`D-P1-08`):** Conforme permitido pelo guia P1 §3.1 (item 4) e decidido por P1, apenas `Policy.LRU` e `Policy.MRU` são implementadas; passar `Policy.CLOCK` ou `Policy.FIFO` levanta `ValueError`. **Atenção para P6 (Pozzato):** os experimentos da bancada devem configurar `Policy.LRU` (padrão) ou `Policy.MRU`.

## Armadilhas conhecidas / bugs abertos

- Nenhum bug aberto. Os dois defeitos reais encontrados na revisão do commit `ce1221b` (auto-expulsão no `_prefetch` sob MRU/área pequena e incremento de `misses` antes de `_victim`) foram documentados em `ai/P1/review.md`, cobertos por testes de regressão em `tests/test_buffer_pool.py` e corrigidos.

## Decisões provisórias a rever

- Nenhuma decisão provisória. Todas as 12 decisões (`D-P1-01` a `D-P1-12`) estão com status `decidida` em `docs/decisoes/P1.md`.
