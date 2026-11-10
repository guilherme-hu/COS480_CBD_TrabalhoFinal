# Post-mortem — P1 (Vivian Maria da Silva e Souza)

## O que foi construído

Implementação completa da **Camada 4 (`cbd/storage/`)** do SGBD didático:
1. **`FileManager` (`cbd/storage/file_manager.py`):** Simulador de disco secundário baseado em arquivos `.dat` fatiados em blocos de $4\text{ KB}$, com cabeçalho descritor e pilha LIFO de blocos livres no Bloco `0` e contadores físicos globais e por arquivo de leituras e gravações sequenciais e aleatórias (`seeks`) controlados por cabeça de leitura/gravação global (`_head`).
2. **`ReplacementPolicy` (`cbd/storage/policies.py`):** Políticas de substituição `LRUPolicy` e `MRUPolicy` em $O(1)$ sobre `OrderedDict`, respeitando `pin_count == 0`.
3. **`BufferPool` (`cbd/storage/buffer_pool.py`):** Gerente de buffer repartido em 4 áreas fixas isoladas (`CATALOG = 8`, `INDEX = 64`, `DATA = 128`, `WORK = 64`), com controle de fixação (`pin`/`unpin`/`page`), marcação `dirty`, gravação forçada (`force`), descarte imediato (`toss_immediate`), leitura antecipada (`prefetch`) protegida contra auto-expulsão e reserva estrita de memória de trabalho (`reserve_work`).

---

## Prompts-chave (até 3) e o código bruto devolvido (link para `ai/P1/raw/`)

1. **Prompt 1 (Análise teórica e escolha de parâmetros da Seção 2):** Definição de tamanho de bloco, técnica de alocação e escolha fundamentada das duas políticas de substituição (`LRU` e `MRU`) com base nas operações do banco (`ai/P1/prompts.md`).
2. **Prompt 2 (Comparação Contígua vs. Extents e fundamentação no SKS e E&N):** Justificativa de todas as opções adotadas e preteridas com cálculo de blocos na nossa base e referências exatas de capítulos e páginas (`docs/decisoes/P1.md`).
3. **Prompt 3 (Implementação da Camada 4 e revisão de PR):**
   - Código bruto da 1ª entrega na branch (commit `ce1221b`): [`ai/P1/raw/buffer_pool_v1_ce1221b.py`](raw/buffer_pool_v1_ce1221b.py).
   - Protótipo ingênuo reconstruído para comparação das armadilhas clássicas do guia P1 §5: [`ai/P1/raw/buffer_pool_inicial.py`](raw/buffer_pool_inicial.py).

---

## Defeitos que a IA cometeu

| Defeito | Categoria | Como foi achado | Correção | Teste que impede a volta |
| :--- | :--- | :--- | :--- | :--- |
| **1. [Real — `ce1221b`] Auto-expulsão no `_prefetch` sob MRU ou área pequena** (no commit `ce1221b`, o prefetch instalava `b+1` com `pin_count=0` na ponta mais recente e o expulsava imediatamente para instalar `b+2`, fazendo um scan de 20 blocos ler 64 blocos no MRU e 85 no LRU) | Distorção grave de contagem de I/O (Bloqueante) | Revisão independente do PR + reprodução com medição de `fm.io.reads` | Guardar frames instalados no lote atual em `batch_frames` e parar o `_prefetch` (`break`) quando `_victim_or_none(area, exclude=batch_frames)` retornar `None` | `tests/test_buffer_pool.py::test_prefetch_nao_expulsa_o_que_acabou_de_ler` |
| **2. [Real — `ce1221b`] Incremento de `misses` antes de `_victim(area)` em `pin()`** (no commit `ce1221b`, se `_victim` levantasse `BufferFullError`, `misses` ficava maior que `io.reads`) | Precisão de contadores | Revisão independente do PR | Mover `area.stats.misses += 1` e `self.stats.misses += 1` para depois de `f = self._victim(area)` e `fm.read` | `tests/test_buffer_pool.py::test_buffer_full_nao_incrementa_misses` |
| **3. [Real — `ce1221b`] `clear()` dentro de `reserve_work` duplicava frames na `free_list`** (`clear()` reconstruía `area.free` com todos os 64 frames de `WORK`, e o `finally` de `reserve_work` devolvia os `k` frames reservados uma segunda vez) | Corrupção de estado interno do Buffer Pool | Revisão do PR #2 pelo colega P2 | Rastrear `area.reserved` em `_Area` e filtrar `[f for f in reversed(area.frames) if f not in area.reserved]` em `clear()` | `tests/test_buffer_pool.py::test_clear_inside_reserve_work` |
| **4. [Real — `ce1221b`] Interação `FileManager.close/create/delete` com `BufferPool` e uso de `assert` em contratos** (`new_page + unpin + fm.close` deixava arquivo físico menor que `high_water`; `fm.delete` não era idempotente; `assert` sumia com `python -O`) | Robustez de ciclo de vida e contratos | Revisão do PR #2 pelo colega P2 | `truncate` no `fm.close`, `bp.close_file(fid)`, `fm.delete` idempotente e troca de `assert` por `ValueError`/`RuntimeError` | `tests/test_buffer_pool.py::test_close_reopen_and_recreate_with_buffer` |
| **5. [Protótipo ingênuo] Sobrescrita `page.dirty = dirty` em `unpin(page, dirty=False)`** (apaga `dirty=True` prévio de `mark_dirty()` ou `new_page()`) | Perda silenciosa de dados (§7.1) | Checklist de armadilhas P1 §5 e teste de estresse com oráculo | `if dirty: page.dirty = True` em `unpin()` | `tests/test_buffer_pool.py::test_dirty_eviction` e `test_naive_vs_refactored_stress` |
| **6. [Protótipo ingênuo] `free_block` sem descartar frame residente e `reset_stats()` sem zerar `_head = None`** | Coerência de cache e precisão metodológica | Revisão do protocolo de medição P1 §2.5 e §3.4 | `_discard_frame(f)` antes de `fm.free(fid, bno)` e `self._head = None` em `reset_stats()` | `tests/test_buffer_pool.py::test_free_block_reuse` e `test_cold_resets_head_counts_seek` |

---

## Refatorações e por quê (desempenho, corretude, estresse)

1. **Proteção de lote (`batch_frames`) em `_prefetch`:** Impede que a política de substituição (especialmente o MRU, que descarta o mais recente) confunda uma página que acabou de ser pré-carregada e ainda não foi lida pelo operador com uma página que já foi processada pelo scan.
2. **Desacoplamento entre inspeção de vítima e expulsão em `_victim_or_none(area, exclude)`:** Antes da correção, `_victim_or_none` já executava `_evict_frame` (gravando se sujo e removendo de `_table`) antes de devolver o frame. Com o parâmetro `exclude=batch_frames`, se o candidato pertencer ao lote atual de prefetch, `_victim_or_none` retorna `None` **sem** expulsar o candidato.
3. **Otimização do caminho quente (`pin` com *hit*):** Uso de `__slots__` em `Frame`, `bytearray` pré-alocado por frame (`f.data[:] = raw` sem realocar buffers) e conjunto `free_set: set[int]` no `_FileDesc` para validar `bno not in d.free_set` em $O(1)$.

---

## Evidência: ingênuo × refatorado (tabela de `ai/P1/stress.md`)

| Cenário de Teste | Versão Bruta (`ce1221b`) / Ingênua (`raw/`) | Versão Refatorada (`cbd/storage/`) |
| :--- | :--- | :--- |
| **Scan sequencial de 20 blocos (`prefetch=4`, `LRU`, `DATA=3`)** | **85 leituras físicas** (auto-expulsão no prefetch) | **20 leituras físicas** ($1\text{ rand\_read} + 19\text{ seq\_reads}$) |
| **Scan sequencial de 20 blocos (`prefetch=4`, `MRU`, `DATA=8`)** | **64 leituras físicas** (auto-expulsão no prefetch) | **20 leituras físicas** ($1\text{ rand\_read} + 19\text{ seq\_reads}$) |
| **`pin()` quando todos os frames estão fixados (`BufferFullError`)** | `misses = 3`, `io.reads = 2` (inconsistente) | `misses = 2`, `io.reads = 2` (exato) |
| **20.000 ops aleatórias em 3 frames (`mark_dirty` + `unpin` + `force`)** | Corrupção de dados no protótipo ingênuo (`lost updates`) | $100\%$ idêntico ao oráculo em disco (`pinned() == 0`) |

---

## Estimado × medido: onde bateu, onde não bateu e por quê

- **Varredura sequencial de 10 blocos (`test_seq_vs_rand`):**
  - *Estimado pela fórmula (SKS Cap. 13):* $1\text{ seek} + 10\text{ transferências}$ ($1\text{ rand\_read} + 9\text{ seq\_reads}$).
  - *Medido:* `rand_reads == 1`, `seq_reads == 9`, `reads == 10`, `seeks == 1`. **Bateu exatamente.**
- **Inundação sequencial (*Sequential Flooding*, `test_sequential_flooding`):**
  - *Cenário:* 4 frames na área `DATA`, varrendo 5 blocos repetidamente por 3 passadas ($15$ solicitações `pin`).
  - *Estimado pela teoria (SKS §13.5.2, Fig. 13.13):* No **LRU**, todas as $15$ solicitações sofrem *miss* ($0$ hits). No **MRU**, a 1ª passada tem $5$ misses (deixando 4 blocos residentes) e cada uma das $2$ passadas seguintes sofre apenas $1$ miss e obtém $4$ hits ($4 \times 2 = 8$ hits e $7$ misses no total).
  - *Medido:* LRU $\to$ `misses == 15, hits == 0`; MRU $\to$ `misses == 7, hits == 8`. **Bateu exatamente.**

---

## Decisões que mudaríamos hoje

- Nenhuma decisão arquitetural precisou ser revertida. A única mudança em relação ao código inicial foi na mecânica interna do `_prefetch` (`D-P1-12`), onde percebemos que instalar páginas com `pin_count = 0` exige protegê-las de serem escolhidas como vítimas dentro da própria chamada de `_prefetch`.

---

## Lições sobre usar IA nesta parte

1. **Testes que só checam se o resultado final está certo deixam passar bugs graves de contagem de I/O:** O nosso teste de estresse original rodava 20.000 operações com `prefetch=2` em 3 frames sob `LRU` e `MRU` e passava em $100\%$ das vezes porque o conteúdo dos blocos estava certo — mas o prefetch estava lendo blocos do disco e jogando fora imediatamente sem ninguém notar. Só um teste com **contagem exata calculada à mão** (`assert fm.io.reads == 20`) expôs o bug.
2. **Interação sutil entre políticas de substituição e recursos auxiliares:** A IA implementou corretamente o `MRU` isolado e o `_prefetch` isolado, mas não previu como `policy.loaded(f)` do MRU interagiria com o `pin_count = 0` do `_prefetch`. Revisão independente com foco em contagem de blocos foi decisiva para encontrar o defeito antes de afetar as medições do grupo.
