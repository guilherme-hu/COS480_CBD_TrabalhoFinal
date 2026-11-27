# Testes de estresse — P2

Arquivos: `tests/stress/test_p2_stress.py` (`@pytest.mark.stress`, seed explícita, oráculo `dict`, `pinned() == 0`
depois de cada operação) e `tests/integration/test_p2_e2e.py` (integração com o P1, o P5 e o P6, ponta a ponta).
Issue #55, 26/11. As rodadas curtas usam `P2_STRESS_OPS` e `P2_TABLE_OPS`; o padrão é 100 000 e 8 000.

## O estresse

| Teste | Operações | Configuração | O que confere |
| :-- | --: | :-- | :-- |
| `test_p2_stress_100k_ops_match_dict[heap]` | 100 000 inserts + deletes (e ~33 000 leituras e erros no meio) | B = 4096, DATA = 3 frames, LRU | scan = oráculo (registros e Ptrs) e `stats()["records"]` a cada 10 000; `fetch`; `NotApplicable` na busca; `KeyError` em RID inválido sem mudar nada; `RecordTooLarge`; iterador abandonado no meio solta tudo; `stats()` igual depois de fechar e reabrir (1/3); conteúdo igual depois do `reorganize` (2/3); "queda" no fim (FileManager e BufferPool novos); página suja despejada pelo menos uma vez; scan com cache frio lê só os blocos de dados |
| `...[hash-unique]` | idem | 64 buckets, seed 480 | idem + busca de chave existente e inexistente; chave apagada que volta; `DuplicateKey` sem efeito e sem pin preso; cada registro no bucket `bucket_of(k)`; união dos `scan_bucket` = scan; blocos = 1 + N + overflow; busca com cache frio lê os blocos da cadeia até a chave e para |
| `...[hash-not-unique-zipf]` | idem | 200 chaves com Zipf em 64 buckets (mais de uma chave por bucket) | idem, cadeias de overflow longas |
| `...[hash-unique-float-bulk]` e `...[hash-not-unique-float-bulk]` | idem | chave FLOAT (ramo `hash_value` do `bucket_of`), começa de um `bulk_load` de 3 000 com N por `expected_records` e `load` (D-P2-16) | idem |
| `test_p2_stress_heap_with_many_map_pages` | 20 000 | mapa com 1 página a cada 16 blocos (várias páginas de mapa e várias faixas), prefetch 8 | idem; o scan pelo cursor não lê o mapa (D-P2-19) |
| `test_p2_stress_every_policy_and_block_size` (36 casos) | 6 000 cada | heap, hash UNIQUE e NOT UNIQUE × LRU e MRU (D-P1-08) × com e sem toss-immediate (D-P1-09) × B = 2048, 4096, 8192, prefetch 4 | idem |
| `test_p2_stress_total_skew_one_key` | 20 000 | 8 buckets, todos os registros com a mesma chave | idem (uma cadeia só) |
| `test_table_with_hash_indexes_random_ops_match_oracle[heap]` | 8 000 | Engine, B = 2048, índices hash UNIQUE em `id` e em `e` (com NULL), como na arquitetura §5.3 | índice nos dois sentidos e sem entrada a mais a cada 500; `analyze`; Engine fechado e reaberto a cada 2 000; `reorganize` com conteúdo igual; `DuplicateKey` desfaz o insert sem pin preso |
| `...[hash]` | 8 000 | hash primário em `k` NOT UNIQUE, seed 480, sem índice (§5.3) | idem + `search` de cada chave |

Resultado: todos passam (suíte inteira, 26/11).

## E2E e integração (`test_p2_e2e.py`, 16 testes)

Base do gerador do P6 em escala 0,01 com 50 cursos e 200 disciplinas (para os joins terem vários buckets),
carregada pelo `load_physical` nas variantes HEAP e HASH do plano de cada B (2048, 4096, 8192). A B+ é do P3.

| Teste | O que prova |
| :-- | :-- |
| `test_e2e_every_variant_matches_sqlite_and_survives_reopen` | cada variante = SQLite em conteúdo, n e V(A), depois de reabrir; seed do plano no arquivo; todo registro achado pelo índice com o mesmo Ptr; o bucket de cada registro segue a fórmula do contrato §5.2 |
| `test_e2e_selections_give_the_sqlite_signature` | as 20 igualdades em `aluno.dre` (índice hash + fetch, e hash primário) e as 2 faixas em `aluno.cr` (scan) dão a assinatura do `workload.json` |
| `test_e2e_cold_counts_match_the_cost_formulas` | N4 na pilha real: scan = b leituras e ⌈b / 9⌉ misses com prefetch 8; busca no hash = blocos da cadeia até a chave; índice + fetch = cadeia do índice (INDEX) + 1 (DATA) |
| `test_e2e_bucket_join_with_the_p5_contract` | contrato do P5: os 3 joins por bucket-join (N e seed iguais, N > 1) dão a assinatura do SQLite; os dois lados conferidos |
| `test_e2e_partitioned_join_with_temp_files_both_orientations` | contrato do P5: partição por `bucket_of(k) % 4` em `TempHeap`s e `scan_bucket(i)` para i ≡ j, com o hash no detalhe e no mestre; o arquivo temporário some depois do `drop()` |
| `test_e2e_p1_contract_disk_only_through_the_buffer_and_one_area_per_file` | contrato do P1: só `cbd.storage.buffer_pool` chama `FileManager.read/write`; cada arquivo (inclusive os que somem no `reorganize`) usa uma área só; o heap apagado e reorganizado = SQLite menos os apagados |

## Os testes pegam defeitos? (mutantes, 26/11)

Cada mutante foi plantado no código da P2, os testes da #55 rodaram (rodada curta) e o arquivo voltou ao original.
M1 a M9 são da 1ª rodada; R1 a R8 são os que sobreviviam na 1ª versão dos testes (achados pelos revisores).

| Mutante | Testes que falham |
| :-- | --: |
| M1 `search` do hash ignora o overflow (enunciado §7.1) | 5 |
| M2 `scan_bucket` ignora o overflow | 10 |
| M3 `delete` do heap sem marcar a página suja | 6 |
| M4 insert do hash liga o overflow sem marcar a página suja | 4 |
| M5 `Table.delete` não tira a chave do índice | 1 |
| M6 `HashIndex.search` devolve só o 1º Ptr | 0: equivalente dentro do escopo (só há índice hash UNIQUE, §5.3); o NOT UNIQUE é coberto em `test_hash_index.py` |
| M7 `Table.insert` não desfaz o que entrou quando o índice recusa | 1 |
| M8 `delete` do hash sem marcar a página suja | 6 |
| M9 hash UNIQUE sem checar duplicata | 3 |
| R1 `reorganize` perde o 1º registro | 9 |
| R2 `TempHeap.scan` lê só 20 blocos | 7 |
| R3 busca NOT UNIQUE devolve o bucket inteiro | 2 |
| R4 `_is_data` só olha a 1ª faixa | 1 |
| R5 o scan pelo cursor pré-lê as páginas do mapa | 1 |
| R6 insert UNIQUE sem `found.close()` (pin preso no `DuplicateKey`) | 3 |
| R7 busca UNIQUE não para no 1º achado (só I/O) | 2 |
| R8 `bucket_of` usa só 2 buckets | 6 |

## Ingênuo × refatorado (`test_naive_hash_versus_refactored`)

Três versões do hash, o mesmo estresse (20 000 inserts + deletes, B = 4096, DATA = 3 frames, 200 chaves no NOT
UNIQUE, seed 480), com `contract=False`: as chaves de `stats()` e o `KeyError` em RID inválido ficam de fora,
para comparar só o comportamento; o contrato é conferido à parte.
- **Ingênuo:** `ai/P2/raw/hash_file_naive_v1.py`, gerado em 26/11 por um subagente com contexto limpo, a partir de
  um pedido simples (prompt em `prompts.md`), sem ler o guia, a arquitetura nem o nosso `cbd/files/`. Nunca editado.
- **Raw v1:** `ai/P2/raw/hash_file_v1.py`, a 1ª versão bruta da #48 (feita com o guia e a arquitetura).
- **Refatorado:** `cbd/files/hash_file.py`.

| Teste | Ingênuo | Raw v1 (#48) | Refatorado |
| :-- | :-- | :-- | :-- |
| estresse funcional, UNIQUE | passa; 12,3 s; 139 515 leituras, 21 356 gravações | passa; 9,3 s; 146 778 / 32 931 | passa; 9,9 s; 146 778 / 32 931 |
| estresse funcional, NOT UNIQUE | passa; 7,7 s; 113 564 / 21 289 | passa; 5,6 s; 100 483 / 26 900 | passa; 5,8 s; 100 483 / 26 900 |
| chaves de `stats()` da arquitetura §6.3 | faltam `blocks`, `buckets`, `meta_blocks`, `records` | todas | todas |
| `bucket_of(10)` com N = 4, seed 7 (contrato: (10 + 7) % 4 = 1) | 1 (por coincidência: usa `hash_value` também para INT) | 1 | 1 |
| abrir o arquivo, cache frio (3 000 registros, 64 buckets) | 127 leituras (um scan inteiro para recontar) | 1 (a meta) | 1 (a meta) |
| `search` de chave existente, cache frio | 1 | 1 | 1 |
| `insert` UNIQUE, cache frio | 2 leituras, 1 gravação | 3 / 2 | 3 / 2 |

O que a tabela mostra:
- **No comportamento, as três acertam.** A IA percorreu o overflow na busca nas duas gerações, então o defeito
  clássico do enunciado §7.1 não apareceu. Os defeitos da raw v1 registrados em `review.md` (bucket pelo tipo
  do valor, N mal dimensionado sem `buckets`, meta sem a chave) ficam fora deste estresse, que passa
  `params["buckets"]` e chave INT; os testes unitários da #48 é que os pegam.
- **O que separa a ingênua é o contrato e o custo.**
  - `stats()` com outros nomes quebraria o `selection._bucket` do P4 e o `load_physical` do P6.
  - O `bucket_of` INT diverge da D-P2-14: coerente dentro do arquivo, por isso o estresse não acusa.
  - Abrir o arquivo lê 127 blocos em vez de 1.
- **As gravações a mais do refatorado** são a meta (bloco 1), atualizada a cada insert e delete (D-P2-17).
- **Raw v1 e refatorado** fazem exatamente o mesmo I/O neste estresse: a refatoração da #48 mudou o que este
  estresse não exercita.

Vieses a declarar:
1. O pedido proibia ler `docs/`. A IA não tinha como conhecer as chaves de `stats()` da §6.3 nem a D-P2-14, e
   essas divergências são do pedido, não da IA.
2. O subagente rodou na mesma sessão, com um modo de "código mínimo" ativo (o arquivo tem um comentário
   `# ponytail:` na linha 60).
3. As duas versões gravam o hash inteiro numa lista Python no `reorganize` ou no `bulk_load`: a ingênua no
   `reorganize`, e o refatorado no `bulk_load` (`recs = list(recs)`, LIMITAÇÃO documentada, fora da medição).
4. O estresse não mede memória. Com 20 000 operações nas três versões, a sequência é a mesma: o oráculo fica em
   ordem de id depois do `reorganize`.

## Integração com o P4

26/11, numa cópia temporária com a branch `p4/67-fechamento` (Raphael) juntada à P2: 112 testes do P4 passaram
contra a nossa P2, e os 2 testes físicos de `tests/stress/test_p4_stress.py` pararam na preparação (esperavam 150
blocos para 450 cursos, com `PAYLOAD = BLOCK_SIZE - 28`; a P2 grava 113, com o cabeçalho de 10 B da D-P2-07).
27/11, com o P4 já no `main` (PR #108): o teste usa `PAYLOAD = BLOCK_SIZE - SlottedPage.HEADER`, e a suíte inteira
(P4 incluído) passa sobre a P2: 753 testes, 2 pulados.

## E2E com os operadores reais do P4 (`test_p2_e2e.py`, 27/11)

Depois do merge do P4 (PR #108), os operadores de `cbd/exec` rodam sobre a P2 real, na mesma base do E2E (escala
0,01, 50 cursos, 200 disciplinas, B = 2048, 4096 e 8192), e a saída é comparada com a assinatura do SQLite.

| Teste | O que roda | Resultado |
| :-- | :-- | :-- |
| `test_e2e_p4_selections_on_every_p2_path` | `select_eq` em `aluno.dre` pelos 3 caminhos (scan do heap, hash primário, índice hash + fetch) e `select_range` em `aluno.cr` por scan (heap e hash) | assinaturas iguais; hash primário = blocos da cadeia até a chave; índice = cadeia do índice + 1; faixa = `select_range_cost` exato; faixa pelo hash primário recusada (`NotApplicable`) |
| `test_e2e_p4_nested_loop_over_heap_hash_and_hash_index` | `nl_join` dos 3 joins em 4 configurações (BNL heap × heap; INL com o detalhe em hash, com o mestre em hash, e pelo índice hash do mestre), com as áreas da bancada e com DATA e INDEX de 3 páginas | assinaturas iguais; BNL = `nl_join_cost` exato; INL ≤ `nl_join_cost` (a estimativa supõe cada busca com cache frio: `aluno ⋈ inscricao` com o mestre em hash mediu 56 leituras com as áreas da bancada, 4 539 com DATA e INDEX de 3, contra 5 035 estimadas) |
| `test_e2e_p4_external_sort_on_temp_heaps` | `external_sort` de `aluno__heap` por `cr` com WORK = mem = 3, 4 e 8, runs em `TempHeap` da P2 | saída ordenada e completa; cada bloco de run gravado e lido no máximo uma vez; nenhum temporário sobra no disco |

O que essa integração mostrou:
- **Nenhum defeito na P2 nem no P4.** Todas as saídas batem com o SQLite e nenhum pin sobra.
- **O sort difere da fórmula do P4 por causa do reempacotamento.** O `external_sort_cost` supõe que toda passada
  ocupa os mesmos b blocos. Com o registro de `aluno`, de tamanho variável, a fusão grava os registros em outra
  ordem e pode ocupar um bloco a mais ou a menos. Medido em B = 2048, WORK = 3: 144 gravações e 178 leituras,
  contra 136 e 170 da fórmula. Não houve bloco gravado duas vezes: a diferença é só quantos blocos as runs
  ocupam. O teste confere pelos blocos reais das runs.
- **Páginas de run que ficam na área WORK não vão ao disco.** Elas são relidas sem leitura ou descartadas pelo
  `drop()` sem gravação, no máximo `work` de cada lado. Com WORK maior que mem (WORK = 64 e mem = 3), todas as
  runs couberam no buffer e o sort mediu 0 gravações contra 51 estimadas. Isso é o que a docstring do `TempHeap`
  avisa: na bancada, mem = WORK.

