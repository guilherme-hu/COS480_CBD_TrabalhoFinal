# Relatório de Testes de Estresse — P1 (Gerente de Arquivos e Buffer Pool)

## 1. Metodologia do Teste de Estresse (`tests/stress/test_buffer_stress.py`)

Conforme especificado no guia de implementação (`docs/implementacao/P1_arquivos_buffer.md`, §6):

- **Cenário de pressão extrema:** Área `DATA` minúscula com apenas **3 frames** (`B = 2048` bytes) operando sobre um arquivo de **10.000 blocos** sob **1.000.000 operações aleatórias** determinísticas (`random.Random(480)`) para `LRU` e `MRU` (suíte completa executada em `21.37s`):
  - `pin` (leituras repetidas e aleatórias forçando *evictions* constantes a cada ~3 acessos);
  - modificações de conteúdo in-place com `mark_dirty()` seguidas de `unpin(page)` (sem passar `dirty=True` no `unpin`, exercitando a preservação do bit `dirty`);
  - `force(page)` (gravações forçadas);
  - `pin(..., seq=True)` com `prefetch=2`, `allow_prefetch(fid, PageType.DATA)` e `toss_immediate=True` no `unpin(..., toss=...)`.
- **Oráculo de Referência e Invariantes de I/O:** Dicionário Python em memória (`oracle: dict[int, bytearray]`) que espelha exatamente o conteúdo esperado de cada bloco lógico. Ao final, verifica-se:
  1. `bp.pinned() == 0`;
  2. consistência interna: `requests == hits + misses` e `reads == seq_reads + rand_reads`;
  3. **consistência física entre Buffer Pool e FileManager:** `fm.io.reads == pool.stats.misses + pool.stats.prefetched` e `fm.io.writes == pool.stats.dirty_evictions + pool.stats.forced_writes`;
  4. após `bp.clear()` e reabertura do arquivo no `FileManager`, todos os 10.000 blocos em disco são comparados byte a byte contra o oráculo.
- **Verificação de Contagem Exata sob Prefetch (`test_prefetch_nao_expulsa_o_que_acabou_de_ler`, `test_prefetch_com_mru_nao_expulsa_antes_do_uso` e `test_prefetch_entre_cursores_nao_expulsa_nao_referenciadas`):** Valida que varreduras sequenciais com `prefetch=4` sobre 20 blocos (`LRU` 3 frames e `MRU` 8 frames, com e sem `toss=True`) nunca expulsam blocos pré-lidos antes do uso, mantendo `fm.io.reads == 20`.

---

## 2. Comparação Experimental: Código Bruto (`buffer_pool_v1_ce1221b.py`) e Protótipo Ilustrativo (`buffer_pool_inicial.py`) vs. Código Refatorado (`cbd/storage/`)

Os testes em `tests/stress/test_buffer_stress.py::test_naive_vs_refactored_stress` e `tests/test_buffer_pool.py` comparam tanto o **código bruto real da 1ª versão gerada pela IA** (`ai/P1/raw/buffer_pool_v1_ce1221b.py`) quanto o **protótipo ingênuo reconstruído para fins didáticos** (`ai/P1/raw/buffer_pool_inicial.py`) contra a implementação refatorada final (`cbd/storage/`):

| Critério Avaliado | Versão Bruta Real (`ce1221b`) / Protótipo Ilustrativo | Versão Refatorada (`cbd/storage/`) | Causa da Diferença |
| :--- | :--- | :--- | :--- |
| **Scan de 20 blocos com `prefetch=4` (`LRU` 3 frames / `MRU` 8 frames)** | **FALHA no código bruto `ce1221b`** (lê **85 blocos** no LRU e **64 blocos** no MRU, pois o prefetch expulsa os blocos que acabou de trazer) | **PASSA** (lê exatamente **20 blocos** em ambos; cada bloco sai do disco 1 única vez) | `_prefetch` refatorado usa `Frame.prefetched`, `for_prefetch=True` e `MRUPolicy` prefere frames `not prefetched`. |
| **Prefetch entre 2 cursores e sob `MRU` sem `toss=True`** | **FALHA antes da 2ª revisão** (`exclude=batch_frames` só protegia a chamada atual e `MRU` expulsava prefetch antes do 1º `pin()`) | **PASSA** (Cursor A obtém 100% hit nos blocos pré-lidos; scan MRU sem `toss` lê exatos 20 blocos) | `Frame.prefetched` protege globalmente até o primeiro `pin()`. |
| **Contagem de `misses` quando `pin()` levanta `BufferFullError`** | **FALHA no código bruto `ce1221b`** (`misses` é incrementado antes de `_victim`, ficando `misses > io.reads`) | **PASSA** (`misses == 0` se nenhuma leitura ocorreu por falta de frame livre) | Incremento de `misses` movido para depois de `_victim(area)` e `fm.read()`. |
| **`bp.clear()` chamado dentro de `with bp.reserve_work(k)`** | **FALHA antes da revisão do PR #2** (duplicava `k` frames em `WORK.free`, permitindo que dois `pin` recebessem o mesmo `Frame`) | **PASSA** (`len(set(WORK.free)) == len(WORK.free) == 64`; frames em `area.reserved` não entram em `free` durante `clear()`) | `_Area.reserved` rastreia frames emprestados a `reserve_work`. |
| **Integridade após `mark_dirty()` + `unpin()` + expulsão** | **FALHA no protótipo ilustrativo** (perde $100\%$ das escritas marcadas via `mark_dirty()`) | **PASSA** ($100\%$ dos 10.000 blocos idênticos ao oráculo em disco após 1M ops) | O `unpin` refatorado faz `if dirty: page.dirty = True`, nunca zerando `dirty` prévio. |
| **Vazamento de `pin` com `prefetch > 0`** | **FALHA no protótipo ilustrativo** (`pinned() > 0` após varredura sequencial; esgota o buffer) | **PASSA** (`pinned() == 0`; páginas de prefetch entram com `pin_count = 0`) | `_prefetch` instala páginas sem fixá-las. |
| **Reuso de bloco após `free_block(fid, bno)` e overflow > `_max_free`** | **FALHA no protótipo ilustrativo / `lost_free`** (retorna dados antigos ou deixa bloco perdido legível) | **PASSA** (descarta frame do buffer; blocos excedentes são encadeados sem perda) | `_discard_frame` + hooks no `FileManager` + extensão encadeada. |
| **Contagem do 1º *seek* após `reset_stats()`** | **FALHA no protótipo ilustrativo** (conta `seq_reads` se coincidir com `_head` anterior) | **PASSA** (`rand_reads == 1` garantido após `reset_stats()`) | `reset_stats()` reinicia `self._head = None`. |
