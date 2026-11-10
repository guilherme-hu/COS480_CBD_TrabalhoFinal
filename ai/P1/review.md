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
  1. `FileManager.allocate(fid)` materializa o bloco recém-alocado com zeros no arquivo físico quando amplia `high_water` (sem somar em `io.writes`, pois é metadado de alocação do SO), garantindo que o arquivo em disco nunca fique menor que `high_water * block_size`; além disso, `BufferPool.close_file(fid)` foi adicionado para executar `flush_file(fid) + drop_file(fid) + fm.close(fid)` em uma única chamada segura.
  2. `FileManager.create(name)` fecha eventual descritor aberto de mesmo nome antes de recriar, e `FileManager.delete(fid)` tornou-se idempotente para `fid`s já removidos na sessão.
  3. Todas as validações de contrato em `BufferPool` foram convertidas de `assert` para `ValueError`/`RuntimeError` explícitos.

---

## 5. [Protótipo Ingênuo] Perda silenciosa de páginas sujas no `unpin` (`page.dirty = dirty`) — Crítico

- **Arquivo / Local:** `ai/P1/raw/buffer_pool_inicial.py` (`NaiveBufferPool.unpin`) vs. `cbd/storage/buffer_pool.py` (`BufferPool.unpin`).
- **Categoria:** Correção / Integridade de Dados (Enunciado §7.1: *"página alterada sem ligar o dirty bit"*).
- **Defeito:** Na versão ingênua, `unpin(page, dirty=False)` fazia `page.dirty = dirty`. Quando o chamador marcava a página como suja durante o uso (`page.mark_dirty()`) ou a criava via `new_page()` (que nasce com `dirty=True`) e depois saía do bloco chamando `unpin(page)` com o padrão `dirty=False`, o bit `dirty` era sobrescrito para `False`. Na expulsão seguinte, a página não era gravada no disco e todas as alterações eram perdidas silenciosamente.
- **Detecção:** Exercitado em `test_dirty_eviction` (quando se usa `p1.mark_dirty()` dentro de `with pool.page(...)`) e em `test_naive_vs_refactored_stress`.
- **Correção Aplicada:** Em `BufferPool.unpin()`, alterar apenas quando `dirty` for verdadeiro (`if dirty: page.dirty = True`), preservando qualquer marcação prévia de sujeira até que a página seja efetivamente gravada no disco (`_evict_frame`, `force` ou `flush_*`).

---

## 4. [Protótipo Ingênuo] Vazamento de `pin` e inflação de `requests` no *Prefetching* — Crítico

- **Arquivo / Local:** `ai/P1/raw/buffer_pool_inicial.py` (`NaiveBufferPool.pin`) vs. `cbd/storage/buffer_pool.py` (`BufferPool._prefetch`).
- **Categoria:** Vazamento de recursos (*pin leak*) e distorção de métricas (Enunciado §7.1).
- **Defeito:** A implementação ingênua de *prefetch* chamava recursivamente `self.pin(fid, b, ptype)` para os próximos $k$ blocos sem fazer `unpin`. Isso deixava os blocos pré-carregados com `pin_count = 1` (impedindo sua substituição futura e violando a asserção `bp.pinned() == 0` ao fim da consulta), além de somar falsos `requests` e `misses` antes de o operador realmente pedir as páginas.
- **Detecção:** Exercitado por `test_prefetch` (`assert pool.pinned() == 0` e verificação de `pool.stats.requests == 1`).
- **Correção Aplicada:** Criado o método dedicado `_prefetch(area, fid, start, k)`, que verifica `fm.is_valid_block(fid, b)`, instala o bloco com **`pin_count = 0`** e incrementa exclusivamente `stats.prefetched += 1`.

---

## 5. [Protótipo Ingênuo] Cabeça do disco (`_head`) não reiniciada em `reset_stats()` — Metodológico

- **Arquivo / Local:** `ai/P1/raw/buffer_pool_inicial.py` (`NaiveFileManager.reset_stats`) vs. `cbd/storage/file_manager.py` (`FileManager.reset_stats`).
- **Categoria:** Precisão dos contadores experimentais (P1 §2.5 e §5).
- **Defeito:** `reset_stats()` zerava `self.io`, mas mantinha `self._head` apontando para o último bloco acessado antes de `Engine.cold()`. Se a operação anterior terminasse no bloco $b-1$ e a medição começasse lendo o bloco $b$, a primeira leitura física contava como `seq_reads` em vez de `rand_reads` (faltando 1 *seek* em relação à fórmula teórica do Silberschatz).
- **Detecção:** Exercitado em `test_seq_vs_rand`.
- **Correção Aplicada:** `reset_stats()` define explicitamente `self._head = None`, garantindo que o primeiro acesso físico após `cold()` sempre contabilize 1 *seek* inicial (`rand_reads = 1`).

---

## 6. [Protótipo Ingênuo] Frame fantasma após `free_block` e leitura silenciosa além do EOF — Correção

- **Arquivo / Local:** `ai/P1/raw/buffer_pool_inicial.py` (`NaiveBufferPool.free_block` e `NaiveFileManager.read`).
- **Categoria:** Coerência de cache e detecção de falhas.
- **Defeito:**
  1. `free_block(fid, bno)` devolvia `bno` à pilha de livres do `FileManager`, mas deixava o frame antigo em `_table[(fid, bno)]`. Quando `allocate()` reutilizava o mesmo `bno`, um acesso subsequente podia encontrar o frame antigo no buffer.
  2. `read(fid, bno)` preenchia com bytes zero leituras além do fim físico do arquivo, mascarando bugs de acesso a blocos não gravados.
- **Detecção:** Exercitado por `test_free_block_reuse` e `test_invalid_reads_and_deletes`.
- **Correção Aplicada:** `BufferPool.free_block` descarta o frame residente via `_discard_frame(f)` antes de chamar `fm.free(fid, bno)`, e `FileManager.read` valida `1 <= bno <= high_water`, `bno not in free_set` e `len(raw) == block_size`, levantando `ValueError` imediatamente em caso de acesso inválido.
