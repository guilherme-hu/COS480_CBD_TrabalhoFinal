# Post-mortem — P1 (Vivian Maria da Silva e Souza)

Atualizado em 23/11. Primeira versão em 12/11 (depois do PR #2); esta inclui as revisões de 22/11 e 23/11 (PR #97 e #96).

## O que foi construído

A **Camada 4 (`cbd/storage/`)** do SGBD didático:

1. **`FileManager` (`file_manager.py`):** simulador de disco com arquivos `.dat` em blocos de 4 KB e alocação contígua.
   - Cabeçalho e pilha LIFO de livres no bloco 0, com extensão encadeada.
   - Contadores de leitura e gravação sequencial × aleatória (seeks), globais e por arquivo, com uma cabeça de disco global.
2. **`ReplacementPolicy` (`policies.py`):** LRU e MRU em O(1) sobre `OrderedDict`. Elas só pulam frames fixados.
3. **`BufferPool` (`buffer_pool.py`):**
   - 4 áreas fixas (`CATALOG = 8`, `INDEX = 64`, `DATA = 128`, `WORK = 64`);
   - `pin`/`unpin`/`page` com `dirty` acumulativo, `force`, toss-immediate e `reserve_work`;
   - **prefetch com cursor dono** (`bp.seq_scan`): as pré-lidas ficam fixadas em nome do cursor (SKS §13.5.1.2), e ele as solta ao usá-las, num salto ou ao terminar. Só nas áreas DATA e INDEX, com o lote em duas fases.

**Números finais:** 63 funções de teste da camada 4 (67 execuções), 3 delas de estresse (até 1 milhão de operações com oráculo). Suíte do repositório: 162 passed, 3 skipped. 29 achados registrados em `ai/P1/review.md`.

## Prompts-chave (até 3) e o código bruto devolvido

1. **Implementação da Camada 4 (09/11):** o código bruto da 1ª entrega (commit `ce1221b`) está em [`raw/buffer_pool_v1_ce1221b.py`](raw/buffer_pool_v1_ce1221b.py), [`raw/file_manager_v1_ce1221b.py`](raw/file_manager_v1_ce1221b.py) e [`raw/policies_v1_ce1221b.py`](raw/policies_v1_ce1221b.py). O protótipo ingênuo, usado para comparar as armadilhas do guia §5, está em [`raw/buffer_pool_inicial.py`](raw/buffer_pool_inicial.py).
2. **Fechamento com revisores independentes (22/11, prompt 6):** 4 revisores (corretude, contratos, contagem de I/O, testes) acharam os defeitos 17 a 20 abaixo. Daí saíram as decisões A (segunda chance), B2, C0 e D0.
3. **"Vamos seguir o livro" (23/11, prompts 7 e 8):** o redesenho do prefetch a partir da sugestão do professor (quem faz o prefetch fixa as páginas e as solta quando termina). A 1ª versão do código está em [`raw/buffer_pool_seq_scan_v1.py`](raw/buffer_pool_seq_scan_v1.py) e [`raw/policies_puras_v1.py`](raw/policies_puras_v1.py). Uma 2ª rodada de revisores sobre essa versão achou os defeitos 23 a 27, e um revisor de confirmação sobre o código final achou o 29.

## Defeitos que a IA cometeu

| Defeito | Categoria | Como foi achado | Correção | Teste que impede a volta |
| :--- | :--- | :--- | :--- | :--- |
| 1. Prefetch expulsava o que acabara de ler (`ce1221b`): scan de 20 blocos lia 85 no LRU e 64 no MRU | Contagem de I/O | Revisão do PR #2, com medição de `io.reads` | Proteção do lote; hoje, pré-lidas fixadas pelo cursor | `test_prefetch_nao_expulsa_o_que_acabou_de_ler`, `test_naive_vs_refactored_stress` |
| 2. `misses += 1` antes da escolha da vítima (`misses > io.reads` em `BufferFullError`) | Contagem | Revisão do PR #2 | Contar depois de `_victim` e `fm.read` | `test_buffer_full_nao_incrementa_misses` |
| 3. `clear()` dentro de `reserve_work` duplicava frames em `free` | Estado interno | Revisão do P2 no PR #2 | `_Area.reserved` | `test_clear_inside_reserve_work` |
| 4. Ciclo de vida `close`/`create`/`delete` e `assert` em contratos | Robustez | Revisão do P2 no PR #2 | `close_file`, `delete` idempotente, exceções explícitas | `test_close_reopen_and_recreate_with_buffer`, `test_delete_after_close` |
| 5. Prefetch entre cursores, sob MRU e em arquivos mistos | Contagem e áreas | 2ª revisão do PR #2 | `allow_prefetch` monotipado; a marca `prefetched` (substituída em 23/11) | `test_prefetch_entre_cursores_nao_expulsa_nao_referenciadas`, `test_prefetch_exige_monotyped_e_bloqueia_misto` |
| 6. `lost_free` sem rastreio; `fm.close`/`fm.free` diretos incoerentes com o buffer | Alocação e coerência | 2ª revisão do PR #2 | Extensão encadeada e hooks | `test_lost_free_overflow`, `test_fm_close_and_free_diretos_mantem_coerencia` |
| 7. `HEADER_STRUCT` com 28 bytes em vez de 32 | Formato em disco | Revisão de código pós-Frente 2 (review.md, achado 16) | Struct de 32 bytes | `test_roundtrip` |
| 8. Pré-lidas órfãs protegidas para sempre (LRU 5/6 com 4 frames: 10 misses em vez de 2; prefetch seguinte desligado) | Política × prefetch | Revisor de I/O, 22/11 | Segunda chance (22/11), depois cursor dono (23/11) | `test_prefetch_orfas_soltas_quando_o_cursor_termina`, `test_prefetch_orfas_nao_desligam_prefetch_seguinte` |
| 9. Vítimas sujas gravadas entre leituras do lote: 0 leituras sequenciais, 8 seeks | Contagem seq × rand | Revisor de contratos, 22/11 | Lote em duas fases (B2): 2 seeks | `test_prefetch_com_vitimas_sujas_le_lote_sequencial` |
| 10. Proteção das pré-lidas por heurística sem dono, fora da semântica de pin do SKS | Projeto | Sugestão do professor, 23/11 | Cursor dono `seq_scan`; LRU e MRU puros | `test_prefetch_dois_donos_so_sai_quando_os_dois_soltam` |
| 11. Cursor que salta acumulava pré-lidas: 126 de 128 frames fixados | Memória | Revisor de I/O, 23/11 | S2: um salto solta as restantes | `test_cursor_que_salta_solta_pre_lidas`, estresse com 2 cursores |
| 12. Cartão prometia "pré-leitura nunca causa `BufferFullError`", o que era falso | Documentação | 3 revisores, 23/11 | T0: condição exata e `# LIMITAÇÃO:` | `test_limite_do_teto_documentado` |
| 13. Cursor usado depois do `with` vazava 4 pins | Pin sem unpin | Revisores de corretude e de testes, 23/11 | Marca `_closed` e `RuntimeError` | `test_cursor_fechado_levanta_erro` |
| 14. Erro de E/S no lote perdia frames ou era engolido | Caminho de erro | Revisores, 23/11 | Frames devolvidos e erro propagado | `test_falha_de_gravacao_no_lote_nao_perde_frame`, `test_falha_de_leitura_no_lote_propaga_sem_vazar` |
| 15. 9 mutações sobreviviam aos testes. Uma delas, o lote sem a parada em bloco residente, punha dois frames no mesmo bloco | Qualidade dos testes | Revisor de testes, por mutação, 23/11 | 6 testes novos e contadores por área | `test_lote_para_em_bloco_residente`, `test_toss_dentro_do_cursor`, `test_pre_lida_assumida_atualiza_recencia`, `test_prefetch_na_area_index` |
| 16. S2 soltava as pré-lidas num hit (pedir de novo a página atual), contra o cartão, que diz "num miss"; limite "1 + k" impreciso com aninhamento (`d + k`) | Código × decisão | Revisor de confirmação, 23/11 (achado 29), depois de 92 mil falhas injetadas sem outro defeito | Soltar só num miss; textos corrigidos | `test_repetir_a_pagina_atual_nao_descarta_pre_lidas`, `test_cursor_aninhado_segura_k_pre_lidas_mais_paginas_em_uso` |
| 17. Script da IA apagou 20 testes não commitados (regex guloso) | Ferramenta / processo | A suíte acusou `NameError` | Reconstrução pelo histórico, conferida linha a linha | — (lição de processo) |
| 18. Protótipo ingênuo: `unpin` sobrescrevia `dirty` | Perda de dados (§7.1) | Checklist do guia §5 e estresse | `if dirty: page.dirty = True` | `test_dirty_eviction`, `test_naive_vs_refactored_stress` |
| 19. Protótipo ingênuo: `free_block` sem descartar o frame; `reset_stats` sem zerar a cabeça | Coerência e medição | Protocolo de medição | `_discard_frame`; `_head = None` | `test_free_block_reuse`, `test_cold_resets_head_counts_seek` |

Os achados 19 (gravações de flush sem contador) e 20 (cabeçalho não move a cabeça) do `review.md` foram **decididos e documentados**, sem correção de código (C0 e D0). O achado 28 do `review.md` (`hit_rate` com prefetch, MRU com prefetch) é uma interpretação de medição, também documentada.

## Refatorações e por quê

1. **Prefetch, da heurística para a regra do livro.** O prefetch passou por quatro versões:
   1. proteção só do lote (`batch_frames`, 08/11);
   2. marca `prefetched` (12/11);
   3. segunda chance (22/11);
   4. pin do cursor dono (23/11).

   Cada uma das três primeiras corrigia um efeito colateral da anterior. A quarta tirou as regras especiais das políticas, e o defeito das órfãs deixou de ser possível.
2. **Lote em duas fases (B2).** O caminho de miss ficou um pouco mais longo, mas o lote custa 1 seek, como a arquitetura promete.
3. **Caminho quente.** `__slots__` em `Frame`, `bytearray` pré-alocado (`f.data[:] = raw`) e `free_set` para validar blocos em O(1).

## Evidência: ingênuo × refatorado

Ver `ai/P1/stress.md` §2. Os números principais:

| Cenário | Ingênuo ou versão anterior | Refatorado |
| :--- | :--- | :--- |
| Scan de 20 blocos, `k = 4`, LRU, `DATA = 3` | 85 leituras (`ce1221b`) | 20 |
| Scan de 20 blocos, `k = 4`, MRU, `DATA = 8` | 64 leituras (`ce1221b`) | 20 |
| Órfãs, LRU alternando 5/6 | 10 misses, 0 hits (12/11) | 2 misses, 8 hits (LRU puro) |
| Cursor que salta, `DATA = 128`, `k = 8` | 126 frames fixados (1ª versão do cursor) | ≤ 9 |
| Vítimas sujas, `k = 3` | 0 leituras sequenciais, 8 seeks | 3 sequenciais, 2 seeks |
| 1 milhão de operações com `mark_dirty` | escritas perdidas (protótipo ingênuo) | 100% igual ao oráculo |

## Estimado × medido

| Medição | Estimado (fórmula) | Medido | Bateu? |
| :--- | :--- | :--- | :--- |
| Varredura de 10 blocos (`test_seq_vs_rand`) | 1 seek + 10 transferências (SKS §15.2) | 1 rand + 9 seq, 1 seek | sim |
| Sequential flooding, 4 frames × 5 blocos × 3 passadas | LRU: 15 misses; MRU: 5 + 1 + 1 = 7 misses (SKS §13.5.2) | LRU 15/0; MRU 7/8 | sim |
| Prefetch de 1 miss com `k = 4` (`test_prefetch`) | 1 + 4 leituras, 1 seek | 1 rand + 4 seq; depois 4 hits | sim |
| Dois arquivos intercalados, 16 blocos cada | sem prefetch: 32 seeks; `k = 3`: 32 / (k + 1) = 8 | 32 e 8 | sim |
| Scan de 20 blocos, `k = 4`, `DATA = 3`, LRU | **20 / 5 = 4 misses** (lote de `1 + k`) | **10 misses + 10 pré-lidas** | **não**: o teto, com 1 cursor e 3 frames, só deixa pré-ler 1 bloco por vez (3 − 1 − 1 = 1). Lotes de 2, e não de 5. Leituras totais iguais (20) |
| Scan frio de 90 blocos, `k = 8` (`hit_rate`) | reaproveitamento 0 (cada bloco lido 1 vez) | `hit_rate` 0,89 | **não**, por definição: as pré-lidas assumidas pelo cursor contam como hit. `io.reads = 90` bate com a fórmula. Decisão A: ler `bp.hits` junto com `bp.prefetched` |
| Gravações de flush | `io.writes − dirty_evictions − forced_writes` | igual (identidade exata) | sim (C0) |

## Decisões que mudaríamos hoje

> **Rascunho da IA, para a Vivian confirmar ou reescrever.**

- **Começar o prefetch pela semântica de pin do livro.** O SKS §13.5.1.2 já resolvia o problema. As três versões intermediárias (lote, marca, segunda chance) custaram três rodadas de revisão. A objeção que nos afastou do pin ("um scan interrompido deixa pins presos") só valia sem dono. Um cursor com `with` resolvia desde o início.
- **Desenhar a interface do cursor antes do código da camada 3.** Como `seq_scan` veio depois, P2 e P3 têm de trocar `pin(seq=True)` por `seq_scan` (#83), e as linhas de fator `prefetch` da matriz dependem disso.

## Lições sobre usar IA nesta parte

> **Rascunho da IA, para a Vivian confirmar ou reescrever.**

1. **Só conferir o conteúdo deixa passar bugs de contagem.** O estresse original passava com os dados certos enquanto o prefetch lia 85 blocos em vez de 20. Só testes com a contagem calculada à mão expuseram o defeito.
2. **A IA tende a remendar o sintoma em vez de voltar à teoria.** Cada versão do prefetch corrigia o efeito colateral da anterior com uma regra nova. A solução certa veio de uma pergunta conceitual do professor ("quem é o dono do pin?"), e não de mais uma rodada de testes.
3. **Revisores independentes acham o que o autor não vê, mas também erram.** Em 22/11, um revisor esperava 2 misses no MRU; a conta certa era 10. Todo achado foi reproduzido com teste antes de ser aceito.
4. **Teste de mutação mede a qualidade dos testes.** 43 testes passando não impediam que a remoção de uma linha pusesse dois frames no mesmo bloco. Só o estresse de 1 milhão de operações percebia.
5. **Scripts da IA sobre arquivos não commitados são perigosos.** Um regex guloso apagou 20 testes. Lição: commitar antes de reescrever por script, e conferir a lista de funções antes e depois.
