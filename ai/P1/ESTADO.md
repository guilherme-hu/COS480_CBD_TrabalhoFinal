# Estado da parte P1 — atualizado em 23/11

## Pronto (com teste que prova)

**Testes da camada 4:**

| Arquivo | Funções | Execuções |
| :--- | :--- | :--- |
| `tests/test_file_manager.py` | 9 | 9 |
| `tests/test_buffer_pool.py` | 49 | 53 |
| `tests/test_policies.py` | 2 | 2 |
| `tests/stress/test_buffer_stress.py` | 3 | 3 |

**Suíte do repositório:** `python -m pytest -q` dá 162 passed e 3 skipped (os testes `stress` são pulados nessa execução). Com `-m stress`, os 3 de estresse passam.

- **`cbd/storage/file_manager.py` (`FileManager`)**
  - Arquivos `.dat` com blocos de 4 KB e alocação contígua (D-P1-01, D-P1-02).
  - Cabeçalho no bloco 0, fora de `io` e fora da cabeça global (D-P1-03, revisão D0).
  - Pilha LIFO de livres, com extensão encadeada quando passa de 1016 entradas (D-P1-04).
  - Contadores `seq`/`rand` de leitura e gravação, globais e por arquivo, com a cabeça global `_head` (D-P1-05).
  - Hooks de coerência com o `BufferPool`.
- **`cbd/storage/policies.py`:** LRU e MRU puros, que só pulam frames fixados (D-P1-08; revisão de 23/11 da D-P1-12).
- **`cbd/storage/buffer_pool.py`**
  - 4 áreas fixas (D-P1-06, D-P1-07).
  - `pin`/`unpin`/`page`, com o `dirty` acumulativo (D-P1-10).
  - `reserve_work` e toss-immediate (D-P1-09).
  - `force` (D-P1-11).
  - `flush_*`, `drop_file`, `close_file`, `free_block` e `clear`.
  - **Prefetch com cursor dono** `bp.seq_scan`/`SeqScan` (D-P1-12, revista em 23/11):
    - pré-lidas fixadas em nome do cursor;
    - S2: um miss fora das pré-lidas solta as restantes (no máximo `k` pré-lidas por cursor, mais as páginas em uso);
    - teto e garantia T0;
    - só nas áreas DATA e INDEX;
    - lote em duas fases (B2);
    - erros de E/S no lote tratados;
    - cursor fechado levanta `RuntimeError`.
- **Documentação:**
  - 12 cartões em `docs/decisoes/P1.md`, nenhum provisório, com o resumo e "como se combinam";
  - guia P1;
  - análise P1;
  - estudo, com as perguntas 1 a 20;
  - relatório gerado.
- **Registros de IA:**
  - `ai/P1/prompts.md`: prompts 1 a 9;
  - `ai/P1/review.md`: achados 1 a 29;
  - `ai/P1/stress.md`;
  - `ai/P1/post_mortem.md`;
  - `ai/P1/raw/`: v1 `ce1221b`, protótipo ingênuo, `buffer_pool_seq_scan_v1` e `policies_puras_v1`.

## Em andamento

- PR #97 (fecha a #42): código, testes e docs da P1. Revisão pedida a P2 e P6.
- PR #96 (fecha a #95): arquitetura §3.2 e comentários de `contracts.py` (interface do `seq_scan`). Revisão pedida a P2 e P6.
- Branch `p1/45-fechamento`, empilhada na `p1/42-contagens-explicadas`: fechamento da #45 (este arquivo, post-mortem, `stress.md`, resumo das decisões, estudo).

## Falta

- Merge do #96 e do #97, e depois conferir `python -m pytest -q` e os `grep` do AGENTS §2 **no `main`** (último critério da #45).
- Decisão H: como expor `fm.allocate` (H0, H1 ou H2; o agente recomenda H0). Ainda não foi discutida com a integrante.
- Correções simples E, F, G, I e J, levantadas pelos revisores de 22/11. Não foram feitas; cada uma precisa de um teste que falha antes.
- #70: estresse integrado das camadas, depois das entregas de P2 a P5.
- #72: consolidar o `POST_MORTEM.md` da raiz com os post-mortems de todas as partes.
- Mensagem ao grupo sobre o `seq_scan` (P3, P4, P5, P6): texto pronto, envio pela integrante.

## Requisitos do enunciado cobertos

| Enunciado § | Onde está | Teste que prova |
| :--- | :--- | :--- |
| **§2.1.1 / §3.1.1** Tamanho do bloco (4 KB) e alocação contígua | `file_manager.py` (`block_size`, `allocate`, `read`, `write`); D-P1-01, D-P1-02 | `test_file_manager.py::test_roundtrip`, `::test_seq_vs_rand` |
| **§2.1.2** Cabeçalho do arquivo (bloco 0) | `file_manager.py` (`HEADER_STRUCT`, `_write_header`, `_read_header`); D-P1-03 | `::test_roundtrip`, `::test_invalid_reads_and_deletes`, `::test_cabecalho_de_outro_arquivo_nao_move_cabeca` |
| **§2.1.3** Gerência de blocos livres | `file_manager.py` (`allocate`, `free`, extensão encadeada); `buffer_pool.py` (`free_block`); D-P1-04 | `::test_free_reuse`, `::test_lost_free_overflow`, `test_buffer_pool.py::test_free_block_reuse`, `::test_free_block_descarta_suja_sem_gravar` |
| **§2.1.4 / §6.1.2** Leituras e gravações sequenciais × aleatórias, seeks | `file_manager.py` (`_count`, `_head`, `io`, `io_by_file`); D-P1-05 | `::test_seq_vs_rand`, `::test_interleaved_files_count_seeks`, `::test_cold_resets_head_counts_seek`, `test_buffer_pool.py::test_identidade_das_gravacoes_com_flush` |
| **§2.2.1** Páginas no buffer e blocos por página (1 página = 1 bloco, 264 páginas) | `buffer_pool.py` (`Frame`, `__init__`); D-P1-06 | `test_buffer_pool.py::test_hit_miss`, `::test_areas_isolated` |
| **§2.2.2 / §2.2.3** Repartição em áreas fixas | `buffer_pool.py` (`_Area`, `stats_by_area`); D-P1-07 | `::test_areas_isolated`, `::test_reserve_work` |
| **§2.2.4 / §6.2** Políticas LRU e MRU, escolha justificada | `policies.py`; D-P1-08 | `test_policies.py::test_lru_vs_mru_victims` (ordem das vítimas), `::test_sequential_flooding` (LRU 15 × MRU 7) |
| **§2.2.5** Toss-immediate | `buffer_pool.py` (`unpin(toss=True)`, `area.tossed`, `SeqScan.page(toss)`); D-P1-09 | `::test_toss`, `::test_toss_dentro_do_cursor` |
| **§2.2.6** Fixação (`pin`/`unpin`), `dirty` e `reserve_work` | `buffer_pool.py` (`pin`, `unpin`, `page`, `reserve_work`); D-P1-10 | `::test_dirty_eviction`, `::test_not_dirty_no_write`, `::test_buffer_full`, `::test_buffer_full_nao_incrementa_misses`, `::test_reserve_work`, `::test_clear_inside_reserve_work` |
| **§2.2.7** Gravação forçada | `buffer_pool.py` (`force`); D-P1-11 | `::test_force` |
| **§2.2.8** Retenção preferencial de índice e catálogo | áreas `INDEX` e `CATALOG` isoladas; D-P1-07 | `::test_areas_isolated` |
| **§2.2.9** Leitura antecipada (prefetch) | `buffer_pool.py` (`seq_scan`, `SeqScan`, `_prefetch_victims`, `_prefetch_read`, `allow_prefetch`); D-P1-12 | `::test_prefetch` (5 leituras: 1 rand + 4 seq), `::test_interleaved_files_with_prefetch_reduces_seeks` (8 × 32 seeks), `::test_prefetch_dois_donos_so_sai_quando_os_dois_soltam`, `::test_cursor_que_salta_solta_pre_lidas`, `::test_repetir_a_pagina_atual_nao_descarta_pre_lidas`, `::test_teto_do_prefetch_garante_frame_para_outro_cursor`, `::test_limite_do_teto_documentado`, `::test_prefetch_com_vitimas_sujas_le_lote_sequencial` |
| **§7.1** Armadilhas do código de IA (leitura fora do buffer, pin sem unpin, página alterada sem dirty) | `grep` do AGENTS §2; `ai/P1/review.md` | `::test_cursor_solta_pre_lidas_em_break_e_excecao`, `::test_cursor_fechado_levanta_erro`, `::test_not_dirty_no_write` |
| **§7.2.3** Registro de IA, revisão crítica e estresse ingênuo × refatorado | `ai/P1/prompts.md`, `review.md`, `stress.md`, `raw/` | `tests/stress/test_buffer_stress.py` (3 testes) |

## Desvios ou dúvidas sobre o contrato

- **`seq_scan`/`SeqScan`** (novos) e **`pin(seq=True)`** (que deixou de pré-ler): estão no #96. Até o merge, a arquitetura no `main` ainda descreve `pin(seq=True)`.
- **`allow_prefetch` e WORK:** `allow_prefetch` levanta `ValueError` na área WORK. A #83 (P2) pedia o prefetch no `TempHeap`; o aviso foi comentado na própria #83.
- **Só LRU e MRU:** `Policy.CLOCK` e `Policy.FIFO` não são aceitas por `make_policy` (D-P1-08).

## Armadilhas conhecidas / bugs abertos

- **Garantia de memória do prefetch é condicional** (T0, `# LIMITAÇÃO:` em `_prefetch_victims`). Com DATA = 3 e k = 8, por exemplo, dois pins comuns dentro de um cursor levam `BufferFullError`. Fora da matriz.
- **MRU com prefetch** num laço interno sobe os seeks (12 × 5). A matriz usa LRU (`# LIMITAÇÃO:` no docstring de `seq_scan`).
- **`hit_rate` com prefetch** inclui as pré-lidas assumidas pelo cursor; ler junto com `bp.prefetched` (decisão A, 23/11).
- **Prefetch sem efeito na matriz:** enquanto o P2 não usar `seq_scan` e `allow_prefetch` (#83), as linhas de fator `prefetch` medem efeito zero.

## Decisões provisórias a rever

- Nenhuma. Todos os 12 cartões estão `decidida`. As escolhas de 22 e 23/11 (A/segunda chance, depois substituída; B2; C0; D0; S2; T0; `hit_rate` A) foram todas feitas por Vivian no chat.
