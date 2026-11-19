# Estado da parte P1 — atualizado em 12/11

## Pronto (com teste que prova)

- **`cbd/storage/file_manager.py` (`FileManager`)**:
  - Criação (`create`, fechando descritor aberto se recriado com o mesmo nome), abertura (`open`), fechamento (`close` com extensão física até `high_water` se necessário, `close_all`), remoção idempotente (`delete`, inclusive após `close`), e verificação (`exists`, `is_open`, `is_valid_block`) de arquivos `.dat` no SO.
  - Cabeçalho descritor persistido no Bloco físico `0` (`HEADER_STRUCT` de 32 bytes + pilha LIFO de até 1016 blocos livres em $4\text{ KB}$ + blocos de extensão encadeados via `ext_bno` sem perda/`lost_free`), mantido em memória (`_FileDesc`) enquanto aberto e excluído de `fm.io`.
  - Coordenação formal via `FileManager.register_hooks(...)` (`on_before_close`, `on_before_free`, `on_before_delete`) para manter coerência com o `BufferPool` mesmo em chamadas diretas a `fm.close()`/`fm.free()`/`fm.delete()`.
  - Alocação contígua (`allocate`), liberação (`free`) e contagem exata de blocos ocupados (`n_blocks = high_water - len(free)`).
  - Contadores físicos globais (`fm.io`) e por arquivo (`fm.io_by_file[fid]`) distinguindo `seq_reads`, `rand_reads`, `seq_writes`, `rand_writes` e `seeks` via cabeça global `_head`, com reinício `_head = None` em `reset_stats()`.
  - **Testes (8):** `tests/test_file_manager.py` (`test_roundtrip`, `test_free_reuse`, `test_seq_vs_rand`, `test_interleaved_files_count_seeks`, `test_invalid_reads_and_deletes`, `test_lost_free_overflow`, `test_delete_after_close`, `test_cold_resets_head_counts_seek`).
- **`cbd/storage/policies.py` (`LRUPolicy`, `MRUPolicy`, `make_policy`)**:
  - Implementação de `LRU` e `MRU` sobre `OrderedDict`, respeitando `pin_count == 0` (`D-P1-08`) e preferindo frames já referenciados (`not prefetched`) antes de escolher páginas pré-lidas como vítima tanto no `LRU` quanto no `MRU`.
  - **Testes (2):** `tests/test_policies.py` (`test_lru_vs_mru_victims`, `test_sequential_flooding`).
- **`cbd/storage/buffer_pool.py` (`BufferPool`, `Frame`/`Page`)**:
  - 4 áreas fixas isoladas (`CATALOG=8`, `INDEX=64`, `DATA=128`, `WORK=64`).
  - `pin`, `unpin` (preservando `dirty`), context manager `page()`, `new_page`, `force`, `flush_file`, `flush_all`, `drop_file`, `close_file` (`flush_file + drop_file + fm.close`), `free_block`, `clear` (preservando frames em `area.reserved`), `reserve_work` (com pré-checagem atômica) e `allow_prefetch(fid, ptype)`.
  - Suporte a `toss_immediate` (`area.tossed`) e `prefetch` com `Frame.prefetched` protegendo páginas pré-lidas entre cursores e em `LRU`/`MRU`, com bloqueio em arquivos não-monotipados/mistos (`D-P1-12`).
  - Contagem de `misses` estritamente após `_victim(area)` e `fm.read` terem sucesso.
  - Validações de contrato com exceções explícitas (`RuntimeError`/`ValueError`), seguras mesmo sob `python3 -O`.
  - **Testes (30 = 28 unitários + 2 de estresse):** `tests/test_buffer_pool.py` (28 funções / 29 execuções) e `tests/stress/test_buffer_stress.py` (1.000.000 ops em 10.000 blocos, `fm.io.reads == misses + prefetched`, `fm.io.writes == dirty_evictions + forced_writes`).
- **Documentação e Registros de IA**:
  - Cartões de decisão `D-P1-01` a `D-P1-12` (com revisões de `D-P1-04`, `D-P1-09` e `D-P1-12`) e tabela resumo em `docs/decisoes/P1.md`.
  - Guia de estudo e perguntas de fixação (1 a 12) em `docs/estudo/P1.md`.
  - Registros de IA em `ai/P1/prompts.md`, `ai/P1/review.md`, `ai/P1/stress.md`, `ai/P1/post_mortem.md` e `ai/P1/raw/` (incluindo `file_manager_v1_ce1221b.py` e `policies_v1_ce1221b.py`).

## Em andamento

- Nenhuma tarefa pendente da Camada 4 (40 funções / 41 execuções de teste passando; pronta para merge no `main`).

## Falta

- Após a entrega das camadas superiores pelos colegas (P2–P6), coordenar a bateria integrada de estresse (`tests/stress/`, Issue `#70`) e consolidar o `POST_MORTEM.md` da raiz do repositório (Issue `#72`).

## Requisitos do enunciado cobertos

| Enunciado § | Onde está | Teste que prova |
| :--- | :--- | :--- |
| **§2.1.1 / §3.1.1** Tamanho do bloco ($4\text{ KB}$) e alocação contígua | `cbd/storage/file_manager.py` (`block_size`, `allocate`, `read`, `write`), `docs/decisoes/P1.md` (`D-P1-01`, `D-P1-02`) | `tests/test_file_manager.py::test_roundtrip`, `test_seq_vs_rand` |
| **§2.1.2** Estrutura do cabeçalho do arquivo (Bloco 0) | `cbd/storage/file_manager.py` (`HEADER_STRUCT`, `_write_header`, `_read_header`), `D-P1-03` | `tests/test_file_manager.py::test_roundtrip`, `test_invalid_reads_and_deletes` |
| **§2.1.3** Gerência de blocos livres (pilha LIFO no Bloco 0 + extensão encadeada + `free_block`) | `cbd/storage/file_manager.py` (`allocate`, `free`, `n_blocks`), `cbd/storage/buffer_pool.py` (`free_block`), `D-P1-04` | `tests/test_file_manager.py::test_free_reuse`, `test_lost_free_overflow`, `tests/test_buffer_pool.py::test_free_block_reuse`, `test_free_block_descarta_suja_sem_gravar` |
| **§2.1.4 / §6.1.2** Contadores de leitura/gravação sequencial e aleatória (`seeks`) | `cbd/storage/file_manager.py` (`_count`, `reset_stats`, `io`, `io_by_file`), `D-P1-05` | `tests/test_file_manager.py::test_seq_vs_rand`, `test_interleaved_files_count_seeks`, `test_cold_resets_head_counts_seek` |
| **§2.2.1** Nº de páginas no Buffer Pool e blocos por página ($1\text{ pág} = 1\text{ bloco}$) | `cbd/storage/buffer_pool.py` (`Frame`, `BufferPool.__init__`), `D-P1-06` | `tests/test_buffer_pool.py::test_hit_miss`, `test_areas_isolated` |
| **§2.2.2 / §2.2.3** Repartição em 4 áreas fixas (`CATALOG`, `INDEX`, `DATA`, `WORK`) | `cbd/storage/buffer_pool.py` (`_Area`, `_areas`, `stats_by_area`), `D-P1-07` | `tests/test_buffer_pool.py::test_areas_isolated`, `test_reserve_work` |
| **§2.2.4 / §6.2** Políticas de substituição (`LRU` e `MRU`) e escolha justificada | `cbd/storage/policies.py` (`LRUPolicy`, `MRUPolicy`, `make_policy`), `D-P1-08` | `tests/test_policies.py::test_lru_vs_mru_victims`, `test_sequential_flooding` |
| **§2.2.5** Emprego de *toss-immediate* | `cbd/storage/buffer_pool.py` (`unpin(..., toss=True)`, `_victim_or_none`), `D-P1-09` | `tests/test_buffer_pool.py::test_toss` |
| **§2.2.6** Fixação de blocos (`pin`/`unpin`), preservação de `dirty` e `reserve_work` | `cbd/storage/buffer_pool.py` (`pin`, `unpin`, `page`, `reserve_work`), `D-P1-10` | `tests/test_buffer_pool.py::test_dirty_eviction`, `test_not_dirty_no_write`, `test_buffer_full`, `test_buffer_full_nao_incrementa_misses`, `test_reserve_work`, `test_clear_inside_reserve_work`, `test_drop_file_e_unpin_invariantes` |
| **§2.2.7** Gravação forçada (*forced output*) | `cbd/storage/buffer_pool.py` (`force`), `D-P1-11` | `tests/test_buffer_pool.py::test_force` |
| **§2.2.8** Substituição com prioridade / retenção preferencial de índice e catálogo | `cbd/storage/buffer_pool.py` (áreas fixas `INDEX` e `CATALOG` isoladas de `DATA` e `WORK`), `D-P1-07` | `tests/test_buffer_pool.py::test_areas_isolated` |
| **§2.2.9** Leitura antecipada (*prefetching*) sem vazamento, protegida entre cursores/MRU e monotipada | `cbd/storage/buffer_pool.py` (`_prefetch`, `allow_prefetch`), `D-P1-12` | `tests/test_buffer_pool.py::test_prefetch`, `test_prefetch_nao_expulsa_o_que_acabou_de_ler`, `test_prefetch_entre_cursores_nao_expulsa_nao_referenciadas`, `test_prefetch_com_mru_nao_expulsa_antes_do_uso`, `test_prefetch_exige_monotyped_e_bloqueia_misto`, `test_interleaved_files_with_prefetch_reduces_seeks` |
| **§7.2.3** Registro de uso de IA, code review crítico e teste de estresse ingênuo × refatorado | `ai/P1/prompts.md`, `ai/P1/review.md`, `ai/P1/stress.md`, `ai/P1/raw/` | `tests/stress/test_buffer_stress.py::test_buffer_random_stress_with_oracle`, `test_naive_vs_refactored_stress` |

## Desvios ou dúvidas sobre o contrato

- **`Policy.CLOCK` e `Policy.FIFO` em `make_policy` (`D-P1-08`):** Apenas `Policy.LRU` e `Policy.MRU` foram selecionadas e implementadas; `00_ARQUITETURA_GERAL.md` e `P6_bancada_relatorio.md` atualizados.
- **`BufferPool.close_file(fid)` e `BufferPool.allow_prefetch(fid, ptype)` (`D-P1-12`):** Adicionados e documentados em `00_ARQUITETURA_GERAL.md` (P2/P3 devem chamar `allow_prefetch` em arquivos monotipados para habilitar prefetch).
- **Protocolo `flush`/`snapshot()` e `toss_immediate=False` em `LRU × MRU`:** Documentado em `00_ARQUITETURA_GERAL.md` §8.1.

## Armadilhas conhecidas / bugs abertos

- Nenhum bug aberto. Todos os achados das revisões 1 e 2 do PR #2 foram documentados em `ai/P1/review.md`, cobertos por testes de regressão e corrigidos.

## Decisões provisórias a rever

- Nenhuma decisão provisória. Todas as 12 decisões (`D-P1-01` a `D-P1-12`) estão com status `decidida` em `docs/decisoes/P1.md`.
