# Relatório de Testes de Estresse — P1 (Gerente de Arquivos e Buffer Pool)

## 1. Metodologia do Teste de Estresse (`tests/stress/test_buffer_stress.py`)

Conforme especificado no guia de implementação (`docs/implementacao/P1_arquivos_buffer.md`, §6):

- **Cenário de pressão extrema:** Área `DATA` minúscula com apenas **3 frames** (`B = 2048` bytes) operando sobre um arquivo com múltiplos blocos sob milhares de operações aleatórias determinísticas (`random.Random(480)`):
  - `pin` (leituras repetidas e aleatórias forçando *evictions* constantes a cada ~3 acessos);
  - modificações de conteúdo in-place com `mark_dirty()` seguidas de `unpin(page)` (sem passar `dirty=True` no `unpin`, exercitando a preservação do bit `dirty`);
  - `force(page)` (gravações forçadas);
  - `free_block` e realocação via `new_page`;
  - `pin(..., seq=True)` com `prefetch=2` e `toss_immediate=True`.
- **Oráculo de Referência:** Dicionário Python em memória (`oracle: dict[int, bytearray]`) que espelha exatamente o conteúdo esperado de cada bloco lógico. Ao final (e após `bp.clear()` e reabertura do arquivo no `FileManager`), todos os blocos em disco são comparados byte a byte contra o oráculo, além de verificar `bp.pinned() == 0` e a consistência aritmética dos contadores (`requests == hits + misses`, `reads == seq_reads + rand_reads`).

---

## 2. Comparação Experimental: Código Ingênuo (`raw/`) vs. Código Refatorado (`cbd/storage/`)

O teste `test_naive_vs_refactored_stress` submete a mesma carga de operações à implementação inicial bruta (`ai/P1/raw/buffer_pool_inicial.py`) e à implementação refatorada (`cbd/storage/`):

| Critério Avaliado | Versão Ingênua (`ai/P1/raw/`) | Versão Refatorada (`cbd/storage/`) | Causa da Diferença |
| :--- | :--- | :--- | :--- |
| **Integridade após `mark_dirty()` + `unpin()` + expulsão** | **FALHA** (perde $100\%$ das escritas marcadas via `mark_dirty()`) | **PASSA** ($100\%$ dos blocos idênticos ao oráculo em disco) | O `unpin` ingênuo sobrescrevia `page.dirty = False`. |
| **Todos os frames fixados na política `CLOCK`** | **FALHA** (laço infinito em `victim()`) | **PASSA** (levanta `BufferFullError` em $\le 2N$ passos) | O `ClockPolicy` refatorado limita a varredura a $2N$ inspeções. |
| **Vazamento de `pin` com `prefetch > 0`** | **FALHA** (`pinned() > 0` após varredura sequencial; esgota o buffer) | **PASSA** (`pinned() == 0`; páginas de prefetch entram com `pin_count = 0`) | `_prefetch` instala páginas sem fixá-las. |
| **Reuso de bloco após `free_block(fid, bno)`** | **FALHA** (retorna dados antigos que ficaram em `_table`) | **PASSA** (descarta o frame do buffer antes de `fm.free`) | `_discard_frame` remove o bloco liberado do cache. |
| **Contagem do 1º *seek* após `reset_stats()`** | **FALHA** (conta `seq_reads` se coincidir com `_head` anterior) | **PASSA** (`rand_reads == 1` garantido após `reset_stats()`) | `reset_stats()` reinicia `self._head = None`. |
