# Relatório de Testes de Estresse — P1 (Gerente de Arquivos e Buffer Pool)

Atualizado em 23/11, depois da revisão da D-P1-12 (prefetch com cursor dono). A suíte de estresse (`python -m pytest -q -m stress`) tem 3 testes e roda em cerca de 45 s.

## 1. Metodologia (`tests/stress/test_buffer_stress.py`)

### 1.1 `test_buffer_random_stress_with_oracle`

- **Cenário:** área `DATA` com **3 frames** (`B = 2048`), arquivo de **10.000 blocos** e **1.000.000 de operações** determinísticas (`random.Random(480)`), para LRU e MRU, com `toss_immediate=True` e `prefetch=2`.
- **Operações:**
  - `pin` e `unpin` aleatórios (até 2 páginas fixadas), com `toss` em 30% dos `unpin`;
  - alterações com `mark_dirty()` seguidas de `unpin(page)`, sem `dirty=True`, para exercitar a preservação do `dirty`;
  - `force(page)`;
  - em 20% dos passos, uma varredura curta (de 1 a 6 blocos) num cursor `bp.seq_scan`, com `toss` aleatório, alterações, e saída antecipada (`break`) aleatória. Depois de cada cursor, `pinned()` tem de voltar ao número de pins avulsos.
- **Oráculo e invariantes:** um `dict[int, bytearray]` espelha cada bloco. Ao fim, o teste confere:
  - `pinned() == 0`;
  - `prefetched > 0`, ou seja, o caminho do prefetch foi de fato exercitado;
  - `requests == hits + misses`;
  - `io.reads == misses + prefetched`;
  - `io.writes == dirty_evictions + forced_writes`;
  - `reads = seq + rand` e `writes = seq + rand`.

  Depois, `clear()`, fechamento e reabertura: os 10.000 blocos no disco são comparados byte a byte com o oráculo.

### 1.2 `test_cursores_e_pins_avulsos_stress` (novo em 23/11)

- **Cenário:** `DATA = 10`, `k = 2`, arquivo de 2.000 blocos, **200.000 operações** para LRU e MRU.
  - Até **2 cursores** ficam abertos ao mesmo tempo, abertos e fechados em qualquer ordem. Cada um anda em sequência, com saltos aleatórios.
  - Os pins avulsos respeitam a condição de memória documentada (T0): `pins fora dos cursores ≤ capacidade − cursores·(1 + k)`.
- **Invariantes a cada passo:**
  - nenhum `BufferFullError`;
  - soma dos `pin_count` = pins avulsos + pré-lidas seguradas pelos cursores;
  - cada cursor segura no máximo `k` pré-lidas (regra S2);
  - conteúdo igual ao oráculo.

  Ao fim, as mesmas identidades de contagem e a persistência em disco.
- **Prova por mutação:** sem a regra S2, este teste falha logo no início (`len(_held) > k`).

### 1.3 `test_naive_vs_refactored_stress`

Compara o código bruto real (`ai/P1/raw/buffer_pool_v1_ce1221b.py`) e o protótipo ingênuo (`ai/P1/raw/buffer_pool_inicial.py`) com `cbd/storage/` (ver §2).

## 2. Ingênuo × refatorado

| Critério | Versão bruta (`ce1221b`) ou protótipo ingênuo | Versão refatorada (`cbd/storage/`) | Causa da diferença |
| :--- | :--- | :--- | :--- |
| **Scan de 20 blocos, `prefetch=4`, LRU, `DATA=3`** | **85 leituras** (`ce1221b`: o prefetch expulsava o que acabara de trazer) | **20 leituras** (10 misses + 10 pré-lidas: o teto, com 1 cursor e 3 frames, faz lotes de 2) | Pré-lidas fixadas pelo cursor dono: nunca são vítimas antes do uso |
| **Scan de 20 blocos, `prefetch=4`, MRU, `DATA=8`** | **64 leituras** (`ce1221b`) | **20 leituras** (4 misses + 16 pré-lidas, lotes de 5) | Idem: sob MRU, a pré-lida fixada não pode ser "a mais recente" a sair |
| **Pré-lidas de um cursor que parou cedo (órfãs)** | **Revisão de 12/11:** protegidas para sempre (`DATA=4`, `k=3`, LRU alternando 5/6: 10 misses, 0 hits) | **2 misses, 8 hits**, igual ao LRU puro (`test_prefetch_orfas_soltas_quando_o_cursor_termina`: `DATA=5`, `k=3`; com 4 frames o teto cortaria o lote) | O fim do cursor solta os pins das pré-lidas não usadas |
| **Cursor que salta (1 bloco a cada 10, `DATA=128`, `k=8`)** | **1ª versão do cursor (23/11):** até 126 de 128 frames fixados | No máximo `1 + k` = 9 por cursor (`d + k` com `cur.page` aninhado) | S2: um miss fora das pré-lidas solta as restantes |
| **Prefetch com vítimas sujas (`DATA=5`, `k=3`)** | **Antes de 22/11:** 0 leituras sequenciais, 8 seeks | Leituras 1 rand + 3 seq, gravações 1 rand + 3 seq, **2 seeks** | Lote em duas fases (B2): grava todas as vítimas, depois lê |
| **`misses` quando `pin()` levanta `BufferFullError`** | `ce1221b`: `misses = 3`, `io.reads = 2` | `misses = 2`, `io.reads = 2` | `misses += 1` só depois de `_victim` e `fm.read` |
| **`clear()` dentro de `reserve_work(k)`** | Antes da revisão do PR #2: `k` frames duplicados em `WORK.free` | Sem duplicatas | `_Area.reserved` |
| **`mark_dirty()` + `unpin()` + expulsão** | Protótipo ingênuo: perde 100% dessas escritas | 100% dos blocos iguais ao oráculo após 1M de operações | `unpin` faz `if dirty: page.dirty = True` (OU lógico) |
| **Pins depois de varreduras com prefetch** | Protótipo ingênuo: pré-lidas fixadas sem dono, `pinned() > 0` | `pinned() == 0` depois de todo cursor, inclusive com `break` e exceção | O pin da pré-lida pertence ao cursor, que o solta |
| **Cursor usado depois do `with`** | 1ª versão do cursor (23/11): 4 páginas fixadas para sempre | `RuntimeError`, 0 pins | Marca `_closed` |
| **Reuso de bloco após `free_block` e mais de 1016 livres** | Protótipo ingênuo: dados antigos e `lost_free` | Frame descartado; excedentes encadeados | `_discard_frame`, hooks e extensão encadeada |
| **1º seek depois de `reset_stats()`** | Protótipo ingênuo: contava como `seq_read` | `rand_reads == 1` | `reset_stats()` zera `_head` |

## 3. O que o estresse não cobre

- **Áreas pequenas fora da condição T0:** o `BufferFullError` nesse caso é esperado e documentado. `test_limite_do_teto_documentado` fixa o comportamento.
- **Integração com as camadas 3 e 2** (heap, B+ e operadores): fica para a #70, depois das entregas de P2 a P5.
