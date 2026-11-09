# Relatório de Testes de Estresse — P1 (Gerente de Arquivos e Buffer Pool)

## 1. Metodologia do Teste de Estresse (`tests/stress/test_buffer_stress.py`)

Conforme especificado no guia de implementação (`docs/implementacao/P1_arquivos_buffer.md`, §6):

- **Cenário de pressão extrema:** Área `DATA` minúscula com apenas **3 frames** (`B = 2048` bytes) operando sobre um arquivo com múltiplos blocos sob milhares de operações aleatórias determinísticas (`random.Random(480)`):
  - `pin` (leituras repetidas e aleatórias forçando *evictions* constantes a cada ~3 acessos);
  - modificações de conteúdo in-place com `mark_dirty()` seguidas de `unpin(page)` (sem passar `dirty=True` no `unpin`, exercitando a preservação do bit `dirty`);
  - `force(page)` (gravações forçadas);
  - `pin(..., seq=True)` com `prefetch=2` e `toss_immediate=True` no `unpin(..., toss=...)`.
- **Oráculo de Referência:** Dicionário Python em memória (`oracle: dict[int, bytearray]`) que espelha exatamente o conteúdo esperado de cada bloco lógico. Ao final (e após `bp.clear()` e reabertura do arquivo no `FileManager`), todos os blocos em disco são comparados byte a byte contra o oráculo, além de verificar `bp.pinned() == 0` e a consistência aritmética dos contadores (`requests == hits + misses`, `reads == seq_reads + rand_reads`).
- **Verificação de Contagem Exata sob Prefetch (`test_prefetch_nao_expulsa_o_que_acabou_de_ler`):** Valida que varreduras sequenciais com `prefetch=4` sobre 20 blocos em áreas restritas (`LRU` com 3 frames e `MRU` com 8 frames) nunca expulsam blocos recém-carregados pelo próprio prefetch, mantendo exatamente `fm.io.reads == 20`.

---

## 2. Comparação Experimental: Código Bruto/Ingênuo (`raw/`) vs. Código Refatorado (`cbd/storage/`)

Os testes comparam tanto o código bruto da 1ª versão (`ai/P1/raw/buffer_pool_v1_ce1221b.py`) quanto o protótipo ingênuo reconstruído (`ai/P1/raw/buffer_pool_inicial.py`) contra a implementação refatorada final (`cbd/storage/`):

| Critério Avaliado | Versão Bruta (`ce1221b`) / Ingênua (`raw/`) | Versão Refatorada (`cbd/storage/`) | Causa da Diferença |
| :--- | :--- | :--- | :--- |
| **Scan de 20 blocos com `prefetch=4` (`LRU` 3 frames / `MRU` 8 frames)** | **FALHA no `ce1221b`** (lê **85 blocos** no LRU e **64 blocos** no MRU, pois o prefetch expulsa os blocos que acabou de trazer) | **PASSA** (lê exatamente **20 blocos** em ambos; cada bloco sai do disco 1 única vez) | `_prefetch` refatorado interrompe o lote se a próxima vítima for um frame instalado na mesma chamada. |
| **Contagem de `misses` quando `pin()` levanta `BufferFullError`** | **FALHA no `ce1221b`** (`misses` é incrementado antes de `_victim`, ficando `misses > io.reads`) | **PASSA** (`misses == 0` se nenhuma leitura ocorreu por falta de frame livre) | Incremento de `misses` movido para depois de `_victim(area)`. |
| **Integridade após `mark_dirty()` + `unpin()` + expulsão** | **FALHA no protótipo ingênuo** (perde $100\%$ das escritas marcadas via `mark_dirty()`) | **PASSA** ($100\%$ dos blocos idênticos ao oráculo em disco) | O `unpin` refatorado faz `if dirty: page.dirty = True`, nunca zerando `dirty` prévio. |
| **Vazamento de `pin` com `prefetch > 0`** | **FALHA no protótipo ingênuo** (`pinned() > 0` após varredura sequencial; esgota o buffer) | **PASSA** (`pinned() == 0`; páginas de prefetch entram com `pin_count = 0`) | `_prefetch` instala páginas sem fixá-las. |
| **Reuso de bloco após `free_block(fid, bno)`** | **FALHA no protótipo ingênuo** (retorna dados antigos que ficaram em `_table`) | **PASSA** (descarta o frame do buffer antes de `fm.free`) | `_discard_frame` remove o bloco liberado do cache. |
| **Contagem do 1º *seek* após `reset_stats()`** | **FALHA no protótipo ingênuo** (conta `seq_reads` se coincidir com `_head` anterior) | **PASSA** (`rand_reads == 1` garantido após `reset_stats()`) | `reset_stats()` reinicia `self._head = None`. |
