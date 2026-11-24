# Code Review Crítico — P1 (Gerente de Arquivos e Gerente de Buffer Pool)

Este documento registra os defeitos identificados no código gerado por IA e as correções aplicadas na versão final (`cbd/storage/file_manager.py`, `cbd/storage/policies.py` e `cbd/storage/buffer_pool.py`).

> **Nota de transparência sobre os arquivos em `ai/P1/raw/` (Enunciado §7.2.3):**
> - [`ai/P1/raw/buffer_pool_v1_ce1221b.py`](raw/buffer_pool_v1_ce1221b.py): cópia exata e sem edição do `cbd/storage/buffer_pool.py` entregue na primeira versão da branch (commit `ce1221b`), onde os defeitos reais **#1** e **#2** abaixo estavam presentes e foram detectados na revisão de código do PR.
> - [`ai/P1/raw/buffer_pool_inicial.py`](raw/buffer_pool_inicial.py): protótipo ingênuo reconstruído posteriormente para exercitar na suíte de estresse (`test_naive_vs_refactored_stress`) as armadilhas clássicas listadas no guia P1 §5 (defeitos **#3**, **#4**, **#5** e **#6**).

---

## 1. [Defeito Real — Commit `ce1221b`] Auto-expulsão de blocos recém-carregados no `_prefetch` — Bloqueante / Crítico

- **Arquivo / Local:** `ai/P1/raw/buffer_pool_v1_ce1221b.py` (linhas 190–208, `BufferPool._prefetch`) vs. `cbd/storage/buffer_pool.py` (`BufferPool._prefetch`).
- **Categoria:** Distorção grave de contagem de I/O e violação da semântica de substituição (Enunciado §2.2.9 e §6.1.2).
- **Defeito:** Ao sofrer um *miss* em `pin(..., seq=True)` com `prefetch = k` ($k = 4$), `_prefetch(area, fid, bno + 1, k)` buscava até $k$ blocos subsequentes instalando cada um com `pin_count = 0` e chamando `area.policy.loaded(f)`:
  - **Na política `MRU` (ex.: `DATA = 8`, `prefetch = 4`):** Para trazer o 1º bloco do prefetch (`b = 11`), o buffer descartava o frame que o scan acabara de desafixar com `toss=True`. Porém, ao instalar o bloco `11` com `pin_count = 0`, `policy.loaded(f)` colocava o bloco `11` no topo de recência ("mais recentemente usado"). Na iteração seguinte do mesmo laço (`b = 12`), `area.policy.victim()` escolhia o frame não-fixado mais recente — que era o próprio bloco `11` recém-pré-carregado! Em seguida, o `12` era expulso pelo `13`, e o `13` pelo `14`. Ao final do prefetch, apenas o `14` permanecia no buffer, e quando o scan pedia `11`, `12` e `13`, todos sofriam novo *miss* e eram relidos do disco (elevando as leituras físicas de **20** para **64** em um scan de 20 blocos!).
  - **Na política `LRU` com área pequena (ex.: `DATA = 3`, `prefetch = 4`):** Com 1 frame fixado pelo bloco atual do scan e apenas 2 frames disponíveis, o prefetch instalava `b+1` e `b+2` nos 2 frames livres e, ao tentar pré-carregar `b+3` e `b+4`, expulsava `b+1` e `b+2` antes mesmo de o scan lê-los (elevando as leituras físicas de **20** para **85**!).
- **Por que os testes iniciais não haviam detectado:** O `test_prefetch` original usava `DATA = 8` com `LRU` e `prefetch = 4` (sobrava espaço livre para os 5 blocos), enquanto o `test_buffer_random_stress_with_oracle` usava `DATA = 3` e `prefetch = 2`, mas seu oráculo validava apenas o conteúdo dos bytes e a identidade `requests == hits + misses`, sem verificar o limite de leituras físicas `fm.io.reads`.
- **Detecção:** Detectado na revisão independente do PR (reproduzido em scan sequencial de 20 blocos com `toss=True` e `prefetch=4`).
- **Correção Aplicada:** Em `BufferPool._prefetch`, mantemos o conjunto `batch_frames` dos frames instalados na própria chamada de `_prefetch`. Se `_victim_or_none(area, exclude=batch_frames)` encontrar um candidato pertencente a `batch_frames` (ou seja, o prefetch teria de expulsar um bloco que ele mesmo acabou de trazer nesta chamada), ele retorna `None` sem expulsá-lo e o laço de `_prefetch` interrompe imediatamente (`break`). O teste parametrizado `test_prefetch_nao_expulsa_o_que_acabou_de_ler` comprova que tanto `(Policy.LRU, 3)` quanto `(Policy.MRU, 8)` realizam exatamente `fm.io.reads == 20` leituras.

---

## 2. [Defeito Real — Commit `ce1221b`] Incremento de `misses` antes de `_victim(area)` e `fm.read` em `pin()` — Médio

- **Arquivo / Local:** `ai/P1/raw/buffer_pool_v1_ce1221b.py` (linhas 175–177, `BufferPool.pin`) vs. `cbd/storage/buffer_pool.py` (`BufferPool.pin`).
- **Categoria:** Precisão dos contadores de Buffer Pool (Enunciado §2.2 e §6.1).
- **Defeito:** Em `BufferPool.pin()`, quando `(fid, bno)` não estava em `self._table`, os contadores `area.stats.misses += 1` e `self.stats.misses += 1` eram incrementados **antes** da chamada `f = self._victim(area)` e da leitura física `self.fm.read(fid, bno)`. Quando todos os frames da área estavam fixados (`pin_count > 0`) e `_victim(area)` levantava `BufferFullError` (ou quando `bno` era inválido), nenhuma leitura física ocorria, mas `bp.stats.misses` ficava maior que `fm.io.reads`, violando a invariante `1 miss == 1 leitura física`.
- **Detecção:** Detectado na revisão de código do PR e reproduzido por `test_buffer_full_nao_incrementa_misses`.
- **Correção Aplicada:** Validamos `self.fm.is_valid_block(fid, bno)` antes de expulsar vítima e só incrementamos `requests` e `misses` após `self.fm.read(fid, bno)` concluir com sucesso.

---

## 3. [Defeito Real — Revisão do PR #2 por P2] `clear()` dentro de `reserve_work()` duplica frames na `free_list` — Médio

- **Arquivo / Local:** `ai/P1/raw/buffer_pool_v1_ce1221b.py` (`BufferPool.clear` e `BufferPool.reserve_work`) vs. `cbd/storage/buffer_pool.py`.
- **Categoria:** Corrupção de estado interno do Buffer Pool / gerenciamento da área `WORK` (Enunciado §2.2.2 e §7.1).
- **Defeito:** `reserve_work(k)` retirava `k` frames de `WORK` guardando-os na lista local `taken`. Se `bp.clear()` (ou `eng.cold()`) fosse chamado dentro do bloco `with bp.reserve_work(k):`, `clear()` reconstruía `area.free_list = list(area.frames)` com todos os 64 frames da área `WORK`. Em seguida, ao sair do bloco `with`, o `finally` de `reserve_work` executava `area.free_list.extend(taken)`, reinserindo os `k` frames reservados uma segunda vez (`len(free_list) == 64 + k`, com referências duplicadas ao mesmo `Frame`). A partir daí, dois `pin()` distintos podiam receber o mesmo objeto `Frame`, sobrescrevendo os dados um do outro.
- **Detecção:** Detectado pelo colega P2 durante a revisão do PR #2.
- **Correção Aplicada:** `_AreaState` passa a rastrear `reserved: set[Frame]`. `BufferPool.clear()` levanta `RuntimeError` explícito se houver páginas fixadas (`self.pinned() > 0`) ou frames reservados em `WORK` (`area.reserved`), e mesmo na reconstrução filtra `[f for f in area.frames if f not in area.reserved]`. Comprovado pelo teste `test_clear_inside_reserve_work`.

---

## 4. [Defeito Real — Revisão do PR #2 por P2] Interação `FileManager.allocate/close/create/delete` com `BufferPool` e uso de `assert` em invariantes de contrato — Médio

- **Arquivo / Local:** `cbd/storage/file_manager.py` (`allocate`, `create`, `close`, `delete`) e `cbd/storage/buffer_pool.py` (`pin`, `unpin`, `clear`, `free_block`, `drop_file`, `close_file`).
- **Categoria:** Robustez de ciclo de vida de arquivos e verificação de contratos.
- **Defeito:**
  1. Quando `new_page` alocava um bloco via `fm.allocate(fid)` (incrementando `high_water`) e a página era desafixada sem `flush_file(fid)` antes de `fm.close(fid)`, o arquivo físico `.cbd` não continha os bytes do bloco recém-alocado; ao reabrir o arquivo e chamar `bp.pin(fid, 1)`, `fm.read` falhava com `ValueError: além do fim físico`.
  2. Chamar `fm.create(name)` sobre um arquivo já aberto truncava o arquivo físico sem fechar o descritor antigo em `_files`, e `fm.delete(fid)` chamado duas vezes levantava `KeyError` (dificultando `TempFile.drop` idempotente na Camada 3).
  3. Invariantes de contrato no `BufferPool` (`unpin` sem `pin`, `area` divergente em `pin`, `clear`/`free_block`/`drop_file` com página fixada) usavam `assert`, que desaparece sob `python -O`.
- **Detecção:** Detectado pelo colega P2 durante a revisão do PR #2.
- **Correção Aplicada:**
  1. `FileManager.close(fid)` faz `truncate(expected_min_size)` para garantir que o arquivo físico em disco cubra `(high_water + 1) * block_size` mesmo sem `flush` prévio; além disso, `BufferPool.close_file(fid)` foi adicionado para executar `flush_file(fid) + drop_file(fid) + fm.close(fid)` em uma única chamada segura.
  2. `FileManager.create(name)` fecha eventual descritor aberto de mesmo nome antes de recriar, e `FileManager.delete(fid)` tornou-se idempotente para `fid`s já removidos na sessão (e passou a suportar também `delete()` após `close()`).
  3. Todas as validações de contrato em `BufferPool` foram convertidas de `assert` para `ValueError`/`RuntimeError` explícitos.

---

## 5. [Defeito Real — 2ª Revisão do PR #2] Prefetch entre cursores, sob `MRU` e em arquivos mistos — Crítico

- **Arquivo / Local:** `cbd/storage/buffer_pool.py` (`Frame`, `_victim_or_none`, `pin`, `_prefetch`) e `cbd/storage/policies.py` (`MRUPolicy.victim`).
- **Categoria:** Distorção de contagem de I/O entre cursores e conflito `DATA` × `INDEX`.
- **Defeito:**
  1. `exclude=batch_frames` protegia apenas a chamada atual de `_prefetch`: um segundo cursor fazendo `pin(..., seq=True)` na mesma área expulsava páginas pré-lidas ainda não consumidas pelo primeiro cursor.
  2. `_prefetch` chamava `policy.loaded(f)`, que no `MRU` colocava páginas pré-lidas no topo de recência, fazendo o `MRU` expulsá-las antes do primeiro `pin()`.
  3. `_prefetch` assumia que todo `fid` era monotipado na área `area.ptype`, podendo instalar blocos de `DATA` na área `INDEX` (ou vice-versa).
- **Detecção:** Detectado na 2ª revisão independente do PR #2.
- **Correção Aplicada (`D-P1-12` revista):** Adicionado `Frame.prefetched` (limpo no 1º `pin()`), `for_prefetch=True` em `_victim_or_none` (para parar antes de expulsar qualquer página `prefetched`), preferência em `MRUPolicy.victim()` por frames já referenciados (`not prefetched`), e exigência de declaração explícita `allow_prefetch(fid, ptype)` com revogação automática para arquivos mistos (`_mixed_fids`). Comprovado por `test_prefetch_entre_cursores_nao_expulsa_nao_referenciadas`, `test_prefetch_com_mru_nao_expulsa_antes_do_uso` e `test_prefetch_exige_monotyped_e_bloqueia_misto`.

---

## 6. [Defeito Real — 2ª Revisão do PR #2] `lost_free` sem rastreio e chamadas diretas a `fm.close()`/`fm.free()` — Alto

- **Arquivo / Local:** `cbd/storage/file_manager.py` (`_write_header`, `_read_header`, `free`, `close`) e `cbd/storage/buffer_pool.py` (`__init__`).
- **Categoria:** Corretude de alocação e coerência de cache.
- **Defeito:**
  1. Blocos que excediam `_max_free` no Bloco 0 iam para `lost_free` sem entrar em `free_set`, permanecendo válidos para `read()` e passíveis de dupla liberação em `free()`.
  2. Chamar `fm.close(fid)` ou `fm.free(fid, bno)` diretamente (sem `bp.close_file`/`bp.free_block`) descartava páginas `dirty` sem gravar ou deixava frames *stale* no buffer.
- **Detecção:** Detectado na 2ª revisão independente do PR #2.
- **Correção Aplicada (`D-P1-04` revista):** Substituído `lost_free` por encadeamento em blocos de extensão (`EXT_STRUCT`, `ext_bno` no Bloco 0) mantendo `free_set` completo e `n_blocks = high_water - len(free)`; adicionados hooks `_on_before_close` e `_on_before_free` no `FileManager` registrados pelo `BufferPool.__init__`. Comprovado por `test_lost_free_overflow` e `test_fm_close_and_free_diretos_mantem_coerencia`.

---

## 7. [Protótipo Ingênuo] Perda silenciosa de páginas sujas no `unpin` (`page.dirty = dirty`) — Crítico

- **Arquivo / Local:** `ai/P1/raw/buffer_pool_inicial.py` (`NaiveBufferPool.unpin`) vs. `cbd/storage/buffer_pool.py` (`BufferPool.unpin`).
- **Categoria:** Correção / Integridade de Dados (Enunciado §7.1: *"página alterada sem ligar o dirty bit"*).
- **Defeito:** Na versão ingênua, `unpin(page, dirty=False)` fazia `page.dirty = dirty`. Quando o chamador marcava a página como suja durante o uso (`page.mark_dirty()`) ou a criava via `new_page()` (que nasce com `dirty=True`) e depois saía do bloco chamando `unpin(page)` com o padrão `dirty=False`, o bit `dirty` era sobrescrito para `False`. Na expulsão seguinte, a página não era gravada no disco e todas as alterações eram perdidas silenciosamente.
- **Detecção:** Exercitado em `test_dirty_eviction` (quando se usa `p1.mark_dirty()` dentro de `with pool.page(...)`) e em `test_naive_vs_refactored_stress`.
- **Correção Aplicada:** Em `BufferPool.unpin()`, alterar apenas quando `dirty` for verdadeiro (`if dirty: page.dirty = True`), preservando qualquer marcação prévia de sujeira até que a página seja efetivamente gravada no disco (`_evict_frame`, `force` ou `flush_*`).

---

## 8. [Protótipo Ingênuo] Vazamento de `pin` e inflação de `requests` no *Prefetching* — Crítico

- **Arquivo / Local:** `ai/P1/raw/buffer_pool_inicial.py` (`NaiveBufferPool.pin`) vs. `cbd/storage/buffer_pool.py` (`BufferPool._prefetch`).
- **Categoria:** Vazamento de recursos (*pin leak*) e distorção de métricas (Enunciado §7.1).
- **Defeito:** A implementação ingênua de *prefetch* chamava recursivamente `self.pin(fid, b, ptype)` para os próximos $k$ blocos sem fazer `unpin`. Isso deixava os blocos pré-carregados com `pin_count = 1` (impedindo sua substituição futura e violando a asserção `bp.pinned() == 0` ao fim da consulta), além de somar falsos `requests` e `misses` antes de o operador realmente pedir as páginas.
- **Detecção:** Exercitado por `test_prefetch` (`assert pool.pinned() == 0` e verificação de `pool.stats.requests == 1`).
- **Correção Aplicada:** Criado o método dedicado `_prefetch(area, fid, start, k)`, que verifica `fm.is_valid_block(fid, b)`, instala o bloco com **`pin_count = 0`** e incrementa exclusivamente `stats.prefetched += 1`.

---

## 9. [Protótipo Ingênuo] Cabeça do disco (`_head`) não reiniciada em `reset_stats()` — Metodológico

- **Arquivo / Local:** `ai/P1/raw/buffer_pool_inicial.py` (`NaiveFileManager.reset_stats`) vs. `cbd/storage/file_manager.py` (`FileManager.reset_stats`).
- **Categoria:** Precisão dos contadores experimentais (P1 §2.5 e §5).
- **Defeito:** `reset_stats()` zerava `self.io`, mas mantinha `self._head` apontando para o último bloco acessado antes de `Engine.cold()`. Se a operação anterior terminasse no bloco $b-1$ e a medição começasse lendo o bloco $b$, a primeira leitura física contava como `seq_reads` em vez de `rand_reads` (faltando 1 *seek* em relação à fórmula teórica do Silberschatz).
- **Detecção:** Exercitado em `test_seq_vs_rand` e `test_cold_resets_head_counts_seek`.
- **Correção Aplicada:** `reset_stats()` define explicitamente `self._head = None`, garantindo que o primeiro acesso físico após `cold()` sempre contabilize 1 *seek* inicial (`rand_reads = 1`).

---

## 10. [Protótipo Ingênuo] Frame fantasma após `free_block` e leitura silenciosa além do EOF — Correção

- **Arquivo / Local:** `ai/P1/raw/buffer_pool_inicial.py` (`NaiveBufferPool.free_block` e `NaiveFileManager.read`).
- **Categoria:** Coerência de cache e detecção de falhas.
- **Defeito:**
  1. `free_block(fid, bno)` devolvia `bno` à pilha de livres do `FileManager`, mas deixava o frame antigo em `_table[(fid, bno)]`. Quando `allocate()` reutilizava o mesmo `bno`, um acesso subsequente podia encontrar o frame antigo no buffer.
  2. `read(fid, bno)` preenchia com bytes zero leituras além do fim físico do arquivo, mascarando bugs de acesso a blocos não gravados.
- **Detecção:** Exercitado por `test_free_block_reuse`, `test_free_block_descarta_suja_sem_gravar` e `test_invalid_reads_and_deletes`.
- **Correção Aplicada:** `BufferPool.free_block` descarta o frame residente via `_discard_frame(f)` antes de chamar `fm.free(fid, bno)`, e `FileManager.read` valida `1 <= bno <= high_water`, `bno not in free_set` e `len(raw) == block_size`, levantando `ValueError` imediatamente em caso de acesso inválido.

---

## 11. `FileManager.open` não limpa `_closed_paths` ao reabrir arquivo fechado — Alto

- **Arquivo / Local:** `cbd/storage/file_manager.py:170-202` (`open`) vs. `248-252` (`delete`).
- **Categoria:** Ciclo de vida de descritores / integridade de arquivo em disco.
- **Defeito:** `create(name)` removia entradas de `self._closed_paths` apontando para o mesmo `path`, mas `open(name)` não fazia o mesmo. Se um arquivo fosse criado (`fid1`), fechado e reaberto (`fid2`), uma chamada tardia `fm.delete(fid1)` encontrava `fid1` em `_closed_paths` e removia o arquivo `.dat` do disco enquanto `fid2` ainda estava aberto.
- **Detecção:** Revisão independente pós-Frente 2.
- **Correção Aplicada:** `FileManager.open` limpa todas as entradas de `self._closed_paths` com `old_path == path` ao abrir o arquivo, espelhando `create()`.

---

## 12. `FileManager.delete` em arquivo aberto não notifica o `BufferPool` — Médio/Alto

- **Arquivo / Local:** `cbd/storage/file_manager.py:240-261` (`delete`) e `cbd/storage/buffer_pool.py:118-125` (`__init__`).
- **Categoria:** Coerência entre `FileManager` e `BufferPool`.
- **Defeito:** Embora `close()` e `free()` já chamassem `_on_before_close` e `_on_before_free`, `delete(fid)` em arquivo ainda aberto fechava e apagava o `.dat` sem notificar o `BufferPool`. Páginas residentes continuavam em `bp._table` (retornando *HIT* para arquivo já deletado) e páginas fixadas (`pin_count > 0`) não levantavam `RuntimeError`.
- **Detecção:** Revisão independente pós-Frente 2.
- **Correção Aplicada:** Adicionado hook `_on_before_delete` em `FileManager` (chamado em `delete(fid)` quando `fid` ainda está em `_desc`), conectado a `self.drop_file(fid)` em `BufferPool.__init__`.

---

## 13. `BufferPool.allow_prefetch` e `new_page` não detectam mistura completa de `PageType` — Médio

- **Arquivo / Local:** `cbd/storage/buffer_pool.py:136-154` (`allow_prefetch`), `314-316` (`pin`) e `357-379` (`new_page`).
- **Categoria:** Isolamento entre áreas do buffer pool / prevenção de prefetch em arquivo misto (D-P1-12).
- **Defeito:** A detecção de mistura só ocorria em `pin()` se `fid` já estivesse em `_monotyped`. Assim: (a) acessar `fid` como `INDEX` antes de declarar `allow_prefetch(fid, DATA)` era aceito sem erro; (b) criar página de `INDEX` via `new_page()` após `allow_prefetch(fid, DATA)` não revogava `_monotyped[fid]`.
- **Detecção:** Revisão independente pós-Frente 1.
- **Correção Aplicada:** Adicionado rastreamento `_seen_ptypes: dict[int, PageType]` atualizado em `pin()` (hit e miss) e `new_page()`, migrando `fid` para `_mixed_fids` em caso de divergência e validando `_seen_ptypes` também em `allow_prefetch()`.

---

## 14. Assimetria entre `LRUPolicy.victim` e `MRUPolicy.victim` durante prefetch entre cursores — Médio

- **Arquivo / Local:** `cbd/storage/policies.py:56-63` (`LRUPolicy.victim`) vs. `98-110` (`MRUPolicy.victim`).
- **Categoria:** Política de substituição e prefetch (D-P1-12).
- **Defeito:** `MRUPolicy.victim()` já preferia frames `not prefetched` com *fallback* para `prefetched=True`, mas `LRUPolicy.victim()` retornava diretamente o primeiro frame com `pin_count == 0`. Se o frame mais antigo da fila LRU estivesse com `prefetched=True`, `_victim_or_none(..., for_prefetch=True)` abortava o prefetch mesmo havendo outros frames `prefetched=False` disponíveis.
- **Detecção:** Revisão de código pós-Frente 1.
- **Correção Aplicada:** `LRUPolicy.victim()` atualizado com a mesma preferência por `not getattr(f, "prefetched", False)` e *fallback* para o mais antigo `prefetched=True`.

---

## 15. Docstring desatualizada em `BufferPool.page` e teste comparativo incompleto — Baixo

- **Arquivo / Local:** `cbd/storage/buffer_pool.py:424` e `tests/stress/test_buffer_stress.py:96-116`.
- **Categoria:** Documentação de contrato e completude de teste.
- **Defeito:** (a) A docstring de `BufferPool.page` ainda descrevia `bno` como `0-indexado` em vez de `1-indexado, bno >= 1`; (b) a parte 1 de `test_naive_vs_refactored_stress` prometia comparar `BufferPoolV1` (85 leituras) vs. `BufferPool` refatorado (20 leituras), mas só executava `BufferPoolV1`.
- **Detecção:** Revisão independente pós-Frente 3.
- **Correção Aplicada:** Docstring de `BufferPool.page` atualizada para `1-indexado, bno >= 1` e parte 1 de `test_naive_vs_refactored_stress` completada com a execução lado a lado do `BufferPool` refatorado (`assert ref_reads == 20`).

---

## 16. `HEADER_STRUCT` com 28 bytes em vez de 32 bytes — Alto

- **Arquivo / Local:** `cbd/storage/file_manager.py:23-24` (`HEADER_STRUCT`).
- **Categoria:** Layout em disco / violação da decisão `D-P1-03` e `D-P1-04`.
- **Defeito:** Ao adicionar o quarto inteiro `ext_bno` (`I`, 4 bytes) ao cabeçalho original `<4sIII16x` (32 bytes), a área reservada foi reduzida para `8x` em vez de `12x` (`<4sIIII8x` = 28 bytes). Isso fazia `HEADER_STRUCT.size == 28`, deixando `_max_free = 1017` em 4 KB (em vez de `1016`) e `_max_free = 9` em 64 B (em vez de `8`), além de deslocar o offset inicial da pilha de livres no Bloco 0.
- **Detecção:** Revisão de código pós-Frente 2.
- **Correção Aplicada:** Alterado `HEADER_STRUCT` para `struct.Struct("<4sIIII12x")` (32 bytes exatos) e adicionada asserção explícita de `HEADER_STRUCT.size == 32` e `_max_free == 8` em `test_lost_free_overflow`.

---

## 17. Páginas pré-lidas órfãs ficam protegidas para sempre e estrangulam a área — Médio

- **Arquivo / Local:** `cbd/storage/policies.py` (`LRUPolicy.victim`, `MRUPolicy.victim`), introduzido pela revisão de 12/11 da `D-P1-12` (achado 14 acima).
- **Categoria:** Política de substituição × prefetch (contagem de hits/misses).
- **Defeito:** as duas políticas preferem **sempre** um frame `prefetched=False`. Um frame pré-lido só é escolhido se não sobrar nenhum outro. A marca `prefetched` só sai quando alguém dá `pin` na página. Se o cursor para antes de usar o que foi pré-lido (seleção que acha a chave, faixa que termina cedo), essas páginas viram órfãs protegidas sem prazo. Com isso, (a) a área útil encolhe para `capacidade − órfãs` nos `pin` comuns e (b) o próprio `_prefetch` deixa de funcionar, porque ele para quando a próxima vítima é `prefetched`.
- **Cenário:** DATA = 4, `prefetch = 3`. `pin(1, seq=True)` pré-lê 2, 3 e 4, e o cursor para. Em seguida, 10 pins alternando os blocos 5 e 6. **LRU:** 10 misses e 0 hits, contra 2 misses e 8 hits do LRU puro. **MRU:** os misses empatam com o MRU puro (10), porque o MRU também nunca expulsa páginas antigas. A diferença aparece num scan seguinte com `seq=True`, que não consegue pré-ler nada.
- **Detecção:** revisor independente de contagem de I/O (AGENTS §8.2), em 22/11/2026, com teste de sondagem. Ao verificar, a IA principal notou que a expectativa do revisor para o **MRU** estava errada (ele esperava 2 misses, mas o MRU puro também dá 10) e trocou o cenário do MRU por um que mede a perda de prefetch. O revisor também erra.
- **Alcance nas medições:** na matriz atual, nenhum. O prefetch só é ligado em scans completos (`factor_experiments`, D-P6-10), que não deixam órfãs, e `Engine.cold()` limpa o buffer entre as medições. O sistema continua errado em qualquer carga com cursor que para cedo.
- **Correção aplicada:** segunda chance para a marca de pré-leitura (`D-P1-12`, revista em 22/11). Um `pin` comum que passa por cima de uma página `prefetched` sem pin tira a marca dela. Na passada seguinte, ela concorre como qualquer outra. O caminho do `_prefetch` (`for_prefetch=True`) não tira marcas, para manter a proteção entre cursores e no MRU.
- **Teste que impede a volta:** `tests/test_buffer_pool.py::test_prefetch_orfas_perdem_protecao_lru` e `::test_prefetch_orfas_nao_desligam_prefetch_seguinte`.
- **Nota de 23/11:** a correção por segunda chance foi **substituída** pelo prefetch fixado pelo cursor dono (achado 21). As órfãs continuam impossíveis, agora porque o cursor solta as pré-lidas ao terminar. Os testes foram renomeados ou reescritos: `test_prefetch_orfas_soltas_quando_o_cursor_termina` (LRU puro: 2 misses, 8 hits) e `test_prefetch_orfas_nao_desligam_prefetch_seguinte` (1 miss, 3 pré-lidas nas duas políticas).

---

## 18. Prefetch com vítimas sujas: cada leitura antecipada vira aleatória — Médio

- **Arquivo / Local:** `cbd/storage/buffer_pool.py` (`_prefetch` e o caminho de miss de `pin`).
- **Categoria:** Contagem de I/O (seq × rand) / violação de contrato.
- **Defeito:** o `_prefetch` escolhia a vítima **dentro do laço de leitura**, um bloco de cada vez. Quando a vítima estava suja, `_evict_frame` gravava a página antes da leitura seguinte. A gravação levava a cabeça global para outro arquivo, e cada leitura antecipada era contada como `rand_read`. Isso contradiz a arquitetura §3.2 ("lê os próximos k blocos, contados como leituras sequenciais") e a justificativa da `D-P1-12` ("reduz seeks em até k×").
- **Cenário:** área WORK com 4 frames, todos sujos com os blocos 1..4 de outro arquivo `b`, `prefetch = 3`, `pin(a, 1, WORK, seq=True)`. Ordem real: grava b1, lê a1, grava b2, lê a2, grava b3, lê a3, grava b4, lê a4. Resultado: `seq_reads = 0`, `rand_reads = 4`, 8 seeks. O esperado pelo contrato é `seq_reads = 3`.
- **Detecção:** revisor independente de aderência a contratos (AGENTS §8.2), em 22/11/2026, com teste de sondagem reproduzido pela IA principal.
- **Alcance nas medições:** na matriz atual, nenhum. O prefetch só é ligado em scans de leitura na área DATA, onde as vítimas estão limpas, e as linhas de sort/hash (área WORK suja) rodam com `prefetch = 0`.
- **Correção aplicada (`D-P1-12`, revista em 22/11, opção B2):** no miss com `seq=True`, o `pin` escolhe e expulsa **todas** as vítimas (a do bloco pedido e as do lote) antes de qualquer leitura, gravando as sujas nessa fase. Só depois lê o bloco pedido e o lote em sequência. As regras de parada do prefetch passam a ser avaliadas antes de expulsar.
- **Teste que impede a volta:** `tests/test_buffer_pool.py::test_prefetch_com_vitimas_sujas_le_lote_sequencial`.

---

## 19. Gravações de `flush` sem contador no buffer — Baixo (decidido documentar)

- **Arquivo / Local:** `cbd/storage/buffer_pool.py` (`flush_file`, `flush_all`, e por consequência `close_file` e `clear`).
- **Categoria:** Contagem de I/O / instrumentação.
- **Achado:** `io.reads` se reconcilia com o buffer (`misses + prefetched`), mas `io.writes` não: as gravações de flush não incrementam nenhum campo de `BufferCounters`. Com 3 páginas sujas e `close_file`, `io.writes = 3` e `dirty_evictions + forced_writes = 0`.
- **Detecção:** revisor independente de contagem de I/O (AGENTS §8.2), em 22/11/2026.
- **Resolução (`D-P1-05`, revista em 22/11, opção C0):** sem contador novo. A identidade `gravações de flush = io.writes − dirty_evictions − forced_writes` vale exatamente, porque só o `BufferPool` chama `fm.write` (regra 1, verificada por `grep`) e o cabeçalho fica fora de `io` (`D-P1-03`). A identidade foi documentada na arquitetura §8.1 e no guia §3.4. Não é defeito de corretude: nenhuma gravação deixa de ser contada em `io.writes`. Falta só nomear a parcela de flush.
- **Teste:** `tests/test_buffer_pool.py::test_identidade_das_gravacoes_com_flush`.

---

## 20. Gravar ou ler o cabeçalho (bloco 0) de outro arquivo não move a cabeça — Baixo (decidido manter)

- **Arquivo / Local:** `cbd/storage/file_manager.py` (`_write_header`, `_read_header`; `_head` só muda em `_count`).
- **Categoria:** Modelo de contagem de seeks.
- **Achado:** `create`, `open` e `close` transferem o bloco 0 de verdade, mas sem contar em `io` (por decisão, `D-P1-03`) e sem mexer em `_head`. Se isso acontece entre duas leituras consecutivas de outro arquivo, a segunda conta como sequencial. Cenário: lê X1, cria e fecha Y, lê X2. Resultado: 1 seek, quando um braço físico faria 2.
- **Detecção:** revisor independente de contagem de I/O (AGENTS §8.2), em 22/11/2026, com teste de sondagem reproduzido pela IA principal.
- **Resolução (`D-P1-03`, revista em 22/11, opção D0):** comportamento mantido e documentado. A cabeça global conta os acessos aleatórios do modelo de custo do SKS §15.2 (`b·t_T + S·t_S`), que considera só os blocos de dados pedidos pelo algoritmo. O cabeçalho é metadado do simulador e fica fora das duas medidas. Isso evita seeks que nenhuma fórmula do cap. 15 prevê nos operadores com arquivos temporários (sort e hash).
- **Teste que fixa o comportamento:** `tests/test_file_manager.py::test_cabecalho_de_outro_arquivo_nao_move_cabeca`.

---

## 21. A proteção das páginas pré-lidas era uma heurística sem dono, fora da semântica de pin dos livros — Médio (revisão de projeto)

- **Arquivo / Local:** `cbd/storage/buffer_pool.py` (`Frame.prefetched`, `_prefetch_*`, `_victim_or_none(for_prefetch)`) e `cbd/storage/policies.py` (`_second_chance_victim`), código das revisões de 12/11 e 22/11 da `D-P1-12`, ambos gerados pela IA.
- **Categoria:** Projeto do buffer / aderência à teoria (SKS §13.5.1.2).
- **Problema:** as páginas pré-lidas entravam com `pin_count = 0` e uma marca `prefetched`. Como ninguém era dono delas, cada efeito colateral pedia uma regra nova: a proteção entre cursores (12/11), a preferência do MRU (12/11), as órfãs protegidas para sempre (achado 17) e a segunda chance (22/11). O resultado foram LRU e MRU "modificados", e não os da literatura. Com área apertada e dois cursores, uma pré-lida ainda podia ser expulsa antes do uso. A própria justificativa da IA contra pins nas pré-lidas (estudo P1, pergunta 10: "um scan interrompido deixaria pins presos") supunha que o pin não tinha dono.
- **Detecção:** discussão com o professor sobre prefetch e políticas, trazida pela integrante em 23/11/2026: "a transação coloca o pin no prefetch e, quando acaba, retira; se mais de uma colocou, todas precisam retirar".
- **Alcance nas medições:** na matriz atual, nenhum (ninguém chama `allow_prefetch` ainda, #83).
- **Correção aplicada (`D-P1-12`, revista em 23/11, opção S2):**
  - `bp.seq_scan(fid, ptype)` abre um cursor dono das pré-lidas, que entram fixadas (`pin_count = 1`) em nome dele;
  - `cur.page(bno)` assume o pin da pré-lida em vez de somar outro, e a saída do cursor solta as não usadas;
  - teto: o prefetch nunca deixa a área com menos frames sem pin do que cursores abertos;
  - o prefetch só roda nas áreas DATA e INDEX;
  - `pin(seq=True)` fora de cursor não pré-lê;
  - saíram `Frame.prefetched`, `for_prefetch` e a segunda chance, e as políticas voltaram a LRU/MRU puros.
  - Primeira versão salva em `ai/P1/raw/buffer_pool_seq_scan_v1.py` e `ai/P1/raw/policies_puras_v1.py`.
- **Registro tardio:** este achado foi escrito depois da implementação, na mesma sessão, e não antes, como pede o AGENTS §6. Os testes novos foram escritos depois do código, e não antes. Para compensar, o teste do teto foi conferido por mutação: com a regra do teto desligada, ele e mais dois testes falham.
- **Testes que impedem a volta:** `tests/test_buffer_pool.py::test_prefetch_dois_donos_so_sai_quando_os_dois_soltam`, `::test_cursor_solta_pre_lidas_em_break_e_excecao`, `::test_teto_do_prefetch_garante_frame_para_outro_cursor`, `::test_allow_prefetch_so_em_data_e_index`, `::test_pin_seq_fora_de_cursor_nao_pre_le`, e o estresse `tests/stress/test_buffer_stress.py::test_buffer_random_stress_with_oracle` (cursores curtos com saída antecipada aleatória).

---

## 22. Script da IA apagou testes ao reescrever o arquivo de testes — Alto (processo, sem efeito no código entregue)

- **Arquivo / Local:** script auxiliar da sessão (fora do repositório) que trocava funções inteiras de `tests/test_buffer_pool.py`.
- **Categoria:** Ferramenta da IA / perda de trabalho não commitado.
- **Defeito:** o regex que localizava uma função aceitava um decorador `@pytest.mark.parametrize(` opcional com casamento preguiçoso de várias linhas. Ele começou num decorador anterior e "casou" por cima de várias funções até a função-alvo, e a substituição apagou 20 testes (o arquivo caiu de 28 para 10 funções). As mudanças do arquivo ainda não estavam commitadas.
- **Detecção:** a execução seguinte da suíte acusou `NameError: _orfas`; a contagem de funções (`grep "^def "`) confirmou a perda.
- **Correção:** o arquivo foi reconstruído a partir da versão `HEAD`, aplicando de novo, em ordem, as edições registradas no histórico da sessão. A reconstrução foi conferida contra as 400 linhas lidas logo antes da reescrita: todas idênticas e nas mesmas posições. O regex passou a aceitar só decoradores colados à função. Depois disso, a lista de funções antes × depois foi comparada, e só as funções previstas mudaram.
- **Lição:** antes de reescrever um arquivo não commitado por script, guardar uma cópia e comparar a lista de funções antes e depois.

---

## 23. Cursor que salta acumula pré-lidas e fixa quase a área inteira — Médio

- **Arquivo / Local:** `cbd/storage/buffer_pool.py`, `SeqScan.page` e `_pin(cursor=...)` (revisão de 23/11 da `D-P1-12`).
- **Categoria:** Memória do buffer (páginas fixadas) / contagem.
- **Defeito:** num pedido fora das pré-lidas, o cursor faz um miss e pré-lê mais k blocos, sem soltar as pré-lidas que sobraram do lote anterior. Elas só saem quando o cursor termina, então o limite "1 + k por cursor" do cartão não vale.
- **Cenário:** DATA = 128, k = 8, scan de 400 blocos lendo 1 a cada 10: **126 de 128 frames fixados** e 166 leituras para 40 páginas úteis. Com DATA = 40, k = 4 e as páginas 1, 10, 20 e 30: 16 fixadas, contra o máximo de 5 documentado.
- **Detecção:** revisor independente de contagem de I/O e memória (AGENTS §8.2), em 23/11/2026, com sonda reproduzida pela IA principal.
- **Correção (`D-P1-12`, revisão de 23/11, opção S2, escolhida por Vivian):** um pedido fora das pré-lidas significa que o cursor saiu da sequência que pré-leu, por isso ele solta **todas** as pré-lidas restantes antes do pin. Assim o cursor segura no máximo 1 + k páginas sempre. A opção S1, que soltaria só as que ficaram para trás, foi preterida: ela só ganha quando o cursor volta para trás, padrão que nenhum dos nossos scans usa, e nesse caso perde o limite.
- **Teste:** `tests/test_buffer_pool.py::test_cursor_que_salta_solta_pre_lidas`.

---

## 24. O cartão e o código prometiam que pré-leitura nunca causa `BufferFullError` — Médio (documentação falsa)

- **Arquivo / Local:** `cbd/storage/buffer_pool.py`, comentário do teto em `_prefetch_victims`; cartão `D-P1-12`, revisão de 23/11, item "Teto".
- **Categoria:** Documentação contra comportamento / memória.
- **Defeito:** o teto só reserva 1 frame sem pin para cada cursor aberto **no momento do prefetch**. Dois pins comuns simultâneos dentro de um cursor, ou um cursor aberto depois do prefetch, podem levar `BufferFullError` em área pequena, onde sem prefetch não levariam.
- **Cenário:** DATA = 3 e k = 8. Dentro de `cur.page(1)` sobre `a`, fixar `b:1` e `b:2` ao mesmo tempo: com k = 0 passa; com k = 8, o segundo pin dá `BufferFullError`.
- **Detecção:** três revisores independentes (corretude, contratos, I/O), em 23/11/2026, cada um com a própria sonda.
- **Resolução (opção T0, escolhida por Vivian):** comportamento mantido; a garantia foi reescrita com a condição exata: `pins simultâneos fora dos cursores ≤ capacidade − cursores × (1 + k)`. Com DATA = 128, k = 8 e 1 cursor, isso dá até 119 pins, muito acima dos ~2 que os operadores usam. A opção T2 (o dono cede a pré-lida quando falta frame) foi preterida: com o achado 23 corrigido, o caso só aparece em área pequena, fora da matriz, e T2 acrescentaria um caminho novo à escolha de vítima. Marcado como `# LIMITAÇÃO:` no código.
- **Teste que fixa o limite:** `tests/test_buffer_pool.py::test_limite_do_teto_documentado`.

---

## 25. Cursor usado depois do `with` vaza pins — Alto

- **Arquivo / Local:** `cbd/storage/buffer_pool.py`, `SeqScan.page` e `BufferPool.seq_scan`.
- **Categoria:** Pin sem unpin.
- **Defeito:** depois do `with bp.seq_scan(...) as cur`, o objeto `cur` continua funcionando. Um `cur.page(bno)` pré-lê e guarda as páginas em `_held`, mas ninguém mais chama `_release`. Como `area.cursors` já voltou a 0, o teto nem limita o lote.
- **Cenário:** DATA = 8, k = 4: `with bp.seq_scan(a) as cur: pass` e depois `with cur.page(1): pass` deixa `pinned() == 4`.
- **Detecção:** revisores de corretude e de qualidade dos testes, em 23/11/2026.
- **Correção:** o cursor guarda uma marca de fechado, e `cur.page` depois do `with` levanta `RuntimeError`.
- **Teste:** `tests/test_buffer_pool.py::test_cursor_fechado_levanta_erro`.

---

## 26. Erro de E/S no meio do lote de prefetch perde frames ou vaza pins — Baixo

- **Arquivo / Local:** `cbd/storage/buffer_pool.py`, `_pin` (chamada de `_prefetch_victims`) e `_prefetch_read`.
- **Categoria:** Estado inconsistente em caminho de erro.
- **Defeito:** há três casos.
  1. Uma falha de `fm.write` ao expulsar uma vítima suja do lote acontecia fora do `try`. A vítima do bloco pedido e as do lote já expulsas não voltavam para `free`, e a área encolhia para sempre.
  2. `_prefetch_read` capturava **qualquer** exceção (por exemplo, `KeyError` ou `OSError`) e só encerrava o lote, escondendo erros reais.
  3. Ao propagar uma dessas exceções, as pré-lidas já instaladas ficariam fixadas sem dono, e o bloco pedido também.
- **Cenário (caso 1):** DATA = 4 com 4 páginas sujas e a 2ª chamada de `fm.write` levantando `OSError`. Depois da exceção, a área tem 3 frames utilizáveis, e não 4.
- **Detecção:** revisores de corretude, contratos e qualidade dos testes (mutações M26 e M27 sobreviviam), em 23/11/2026.
- **Correção:**
  - a 1ª fase devolve a `free` os frames já escolhidos se uma gravação falhar;
  - `_prefetch_read` só trata como fim normal do lote o `ValueError` de bloco alocado mas nunca gravado;
  - qualquer outra exceção devolve os frames restantes a `free`, deixa as pré-lidas já instaladas com o cursor (que as solta ao terminar), solta o pin do bloco pedido e propaga o erro.
- **Testes:** `tests/test_buffer_pool.py::test_falha_de_gravacao_no_lote_nao_perde_frame`, `::test_falha_de_leitura_no_lote_propaga_sem_vazar`, `::test_lote_para_no_fim_fisico_sem_perder_frame`.

---

## 27. Lacunas de teste no prefetch com cursor — Médio

- **Arquivo / Local:** `tests/test_buffer_pool.py`, `tests/stress/test_buffer_stress.py`.
- **Categoria:** Qualidade dos testes.
- **Defeito:** mutações que passavam pelos 43 testes unitários (conferidas pela IA principal):
  - **M19:** apagar a parada do lote em bloco já residente. É corrupção: um segundo frame para o mesmo `(fid, bno)`. Só o estresse de 1M de operações pegava, depois de ~16 s.
  - **M16:** `cur.page` ignorando `toss`. O `toss=True` dos testes rodava com `toss_immediate=False`, onde não tem efeito.
  - **M25:** hit da pré-lida assumida contado só no contador global, e não em `stats_by_area`, que vai para o CSV como `bp.DATA.*`.
  - **M32:** a pré-lida assumida sem `policy.accessed`, ou seja, recência errada.
  - **M26, M27, M28:** caminhos de erro e fim do arquivo.
  - **M29, M30:** prefetch na área INDEX nunca exercitado.
  - **Estresse:** não exercitava o teto nem dois cursores. O comentário "convive com até 2 pins avulsos" estava errado: o cursor só abria com até 1.
- **Detecção:** revisor independente de qualidade dos testes, com teste de mutação, em 23/11/2026.
- **Correção:** um teste para cada mutação; contadores por área em `test_prefetch`; estresse novo com dois cursores aninhados e pins avulsos dentro da condição do achado 24. Também foram corrigidos três comentários de testes com contas imprecisas.

---

## 28. Pontos de medição documentados (sem defeito de código)

- **`hit_rate` com prefetch:** a pré-lida que o cursor assume conta 1 request e 1 hit. Num scan frio de 90 blocos com k = 8, o `hit_rate` dá 0,89, embora os 90 blocos tenham saído do disco. A contagem é coerente (`io.reads == misses + prefetched`), mas, com prefetch, "hit" inclui pré-leitura. O reaproveitamento real é `hits − (prefetched − pré-lidas soltas sem uso)`. Registrado no cartão D-P1-12, para o relatório e para o P6 (`bench/compare_cost.py:71`).
- **MRU com prefetch no laço interno:** sobe os seeks (12 contra 5 em 5 passadas de 200 blocos, DATA = 128). O lote precisa de k + 1 vítimas, e o MRU as tira das páginas mais recentes, fragmentando o que fica retido. A matriz usa LRU em todos os algoritmos (`bench/matrix.py:62`), então não muda. Marcado como `# LIMITAÇÃO:` no código.
- **`free_block`, `drop_file`, `close_file` e `clear` com cursor aberto:** levantam `RuntimeError` se a página é uma pré-lida ainda não usada, porque ela está fixada. É coerente com a regra de pin; entra no aviso a P2 e P3.

---

## 29. Revisor de confirmação sobre o código final: S2 solta pré-lidas num hit e limite "1 + k" impreciso — Baixo

- **Arquivo / Local:** `cbd/storage/buffer_pool.py`, `SeqScan.page` e docstrings de `seq_scan` e `SeqScan.page`.
- **Detecção:** revisor independente de confirmação (AGENTS §8.2), em 23/11/2026, depois das correções dos achados 23 a 27. A sonda de falhas injetadas passou: 40 seeds, cerca de 92 mil exceções em `fm.read` e `fm.write`, com todas as invariantes de frames, pins e contagem mantidas. Dois pontos baixos, cada um com teste que falha, reproduzidos pela IA principal.
- **(a) Defeito: o código não seguia a decisão S2.** O cartão diz "**num miss**, soltar todas as pré-lidas restantes", mas o código soltava em **qualquer** pedido fora das pré-lidas, inclusive num hit. Por exemplo, pedir de novo a página atual (`cur.page(1)` aninhado, ou repetido em sequência) soltava 2..9: as páginas perdiam a proteção do pin e, sob MRU ou disputa da área, podiam sair antes do uso e ser relidas.
  - **Correção:** soltar só quando o pedido vai ser um miss, porque só então o cursor pré-lê de novo. O limite de `k` pré-lidas por cursor continua valendo, já que um hit não pré-lê nada.
  - **Teste:** `tests/test_buffer_pool.py::test_repetir_a_pagina_atual_nao_descarta_pre_lidas`.
- **(b) Texto impreciso: "no máximo 1 + k páginas por cursor".** Com `cur.page` aninhado no mesmo cursor, cada página em uso soma 1. O limite real é `k` pré-lidas mais as páginas em uso pelo cursor (`d + k`, com `d` = profundidade de aninhamento). Os scans da camada 3 usam `d = 1`. O revisor verificou que a garantia T0 não piora: o teto conta os frames sem pin reais.
  - **Correção:** textos ajustados no código, no cartão e na arquitetura.
  - **Teste:** `tests/test_buffer_pool.py::test_cursor_aninhado_segura_k_pre_lidas_mais_paginas_em_uso`.
- **(c) Observação mínima, sem correção:** se a gravação de um candidato vindo da fila `tossed` falha, a página continua residente e correta, mas sai da fila e perde a prioridade de descarte (D-P1-09). Não afeta corretude, pins nem contagem.
