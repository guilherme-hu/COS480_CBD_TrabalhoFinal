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
