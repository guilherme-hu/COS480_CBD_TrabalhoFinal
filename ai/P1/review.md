# Code Review Crítico — P1 (Gerente de Arquivos e Gerente de Buffer Pool)

Este documento registra os defeitos identificados na versão bruta inicial (`ai/P1/raw/buffer_pool_inicial.py`) e as refatorações aplicadas na versão final (`cbd/storage/file_manager.py`, `cbd/storage/policies.py` e `cbd/storage/buffer_pool.py`).

---

## 1. Perda silenciosa de páginas sujas no `unpin` (`page.dirty = dirty`) — Crítico

- **Arquivo / Local:** `ai/P1/raw/buffer_pool_inicial.py` (`NaiveBufferPool.unpin`) vs. `cbd/storage/buffer_pool.py` (`BufferPool.unpin`).
- **Categoria:** Correção / Integridade de Dados (Enunciado §7.1: *"página alterada sem ligar o dirty bit"*).
- **Defeito:** Na versão ingênua, `unpin(page, dirty=False)` fazia `page.dirty = dirty`. Quando o chamador marcava a página como suja durante o uso (`page.mark_dirty()`) ou a criava via `new_page()` (que nasce com `dirty=True`) e depois saía do bloco chamando `unpin(page)` com o padrão `dirty=False`, o bit `dirty` era sobrescrito para `False`. Na expulsão seguinte, a página não era gravada no disco e todas as alterações eram perdidas silenciosamente.
- **Detecção:** Detectado em `test_dirty_eviction` (quando se usa `p1.mark_dirty()` dentro de `with pool.page(...)`) e no teste de estresse com oráculo de referência.
- **Correção Aplicada:** Em `BufferPool.unpin()`, alterar apenas quando `dirty` for verdadeiro (`if dirty: page.dirty = True`), preservando qualquer marcação prévia de sujeira até que a página seja efetivamente gravada no disco (`_evict_frame`, `force` ou `flush_*`).

---

## 2. Laço infinito na política Clock quando todos os frames estão fixados — Crítico

- **Arquivo / Local:** `ai/P1/raw/buffer_pool_inicial.py` (`NaiveClockPolicy.victim`) vs. `cbd/storage/policies.py` (`ClockPolicy.victim`).
- **Categoria:** Complexidade / Travamento (Enunciado §7.1 e P1 §5).
- **Defeito:** O algoritmo Clock ingênuo utilizava `while True:` avançando o ponteiro circular e dando `continue` sempre que `f.pin_count > 0`. Quando todos os frames residentes da área estavam fixados (`pin_count > 0`), o método entrava em laço infinito em vez de retornar `None` para disparar `BufferFullError`.
- **Detecção:** Detectado por `test_buffer_full` ao testar a política `Policy.CLOCK` com os 2 frames da área fixados.
- **Correção Aplicada:** Limitar a varredura do ponteiro do relógio a no máximo `2 * len(frames)` passos (1ª volta para limpar `ref = 1 -> 0` nos frames não fixados; 2ª volta para selecionar o primeiro com `ref == 0`). Se após `2 * n` passos nenhum frame elegível for encontrado, retorna `None` e o `BufferPool` levanta `BufferFullError`.

---

## 3. Vazamento de `pin` e inflação de `requests` no *Prefetching* — Crítico

- **Arquivo / Local:** `ai/P1/raw/buffer_pool_inicial.py` (`NaiveBufferPool.pin`) vs. `cbd/storage/buffer_pool.py` (`BufferPool._prefetch`).
- **Categoria:** Vazamento de recursos (*pin leak*) e distorção de métricas (Enunciado §7.1).
- **Defeito:** A implementação ingênua de *prefetch* chamava recursivamente `self.pin(fid, b, ptype)` para os próximos $k$ blocos sem fazer `unpin`. Isso deixava os blocos pré-carregados com `pin_count = 1` (impedindo sua substituição futura e violando a asserção `bp.pinned() == 0` ao fim da consulta), além de somar falsos `requests` e `misses` antes de o operador realmente pedir as páginas.
- **Detecção:** Detectado por `test_prefetch` (`assert pool.pinned() == 0` e verificação de `pool.stats.requests == 1`).
- **Correção Aplicada:** Criado o método dedicado `_prefetch(area, fid, start, k)`, que verifica `fm.is_valid_block(fid, b)`, obtém vítima com `_victim_or_none(area)`, lê via `fm.read(fid, b)`, instala o bloco com **`pin_count = 0`** e incrementa exclusivamente `stats.prefetched += 1`.

---

## 4. Cabeça do disco (`_head`) não reiniciada em `reset_stats()` — Metodológico

- **Arquivo / Local:** `ai/P1/raw/buffer_pool_inicial.py` (`NaiveFileManager.reset_stats`) vs. `cbd/storage/file_manager.py` (`FileManager.reset_stats`).
- **Categoria:** Precisão dos contadores experimentais (P1 §2.5 e §5).
- **Defeito:** `reset_stats()` zerava `self.io`, mas mantinha `self._head` apontando para o último bloco acessado antes de `Engine.cold()`. Se a operação anterior terminasse no bloco $b-1$ e a medição começasse lendo o bloco $b$, a primeira leitura física contava como `seq_reads` em vez de `rand_reads` (faltando 1 *seek* em relação à fórmula teórica do Silberschatz).
- **Detecção:** Detectado em `test_seq_vs_rand` e na revisão do protocolo de medição.
- **Correção Aplicada:** `reset_stats()` define explicitamente `self._head = None`, garantindo que o primeiro acesso físico após `cold()` sempre contabilize 1 *seek* inicial (`rand_reads = 1`).

---

## 5. Frame fantasma após `free_block` e leitura silenciosa além do EOF — Correção

- **Arquivo / Local:** `ai/P1/raw/buffer_pool_inicial.py` (`NaiveBufferPool.free_block` e `NaiveFileManager.read`).
- **Categoria:** Coerência de cache e detecção de falhas.
- **Defeito:**
  1. `free_block(fid, bno)` devolvia `bno` à pilha de livres do `FileManager`, mas deixava o frame antigo em `_table[(fid, bno)]`. Quando `allocate()` reutilizava o mesmo `bno`, um acesso subsequente podia encontrar o frame antigo no buffer.
  2. `read(fid, bno)` preenchia com bytes zero leituras além do fim físico do arquivo, mascarando bugs de acesso a blocos não gravados.
- **Detecção:** Detectado por `test_free_block_reuse` e `test_invalid_reads_and_deletes`.
- **Correção Aplicada:** `BufferPool.free_block` descarta o frame residente via `_discard_frame(f)` antes de chamar `fm.free(fid, bno)`, e `FileManager.read` valida `1 <= bno <= high_water`, `bno not in free_set` e `len(raw) == block_size`, levantando `ValueError` imediatamente em caso de acesso inválido.
