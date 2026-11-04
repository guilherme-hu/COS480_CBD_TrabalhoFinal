# P4 — Seleções, Ordenação Externa e Nested-Loop Join (em blocos e indexado)

> Leia junto com [`00_ARQUITETURA_GERAL.md`](00_ARQUITETURA_GERAL.md). As assinaturas públicas estão no documento geral (§7.1) e **não podem mudar** sem PR do grupo. Aqui está o *como* implementar.

**Peso:** ~15%. Você **consome** a camada 3 (P2/P3: `Table`, `DataFile`, `Index`, `TempFile`, `Catalog.temp`) e o `BufferPool` (P1: `reserve_work`, `capacity`). Você **fornece** os operadores de seleção e o NLJ para P6, e `access.py` + `external_sort` para o P5, que depende deles.

**Marcos:** até **09/11**: `access.py` (é pequeno; destrava o P5) e `select_*` com `SCAN`/`PRIMARY` sobre heap. Até **12/11**: `external_sort` (o P5 precisa dele para o sort-merge). Até **14/11**: `SECONDARY`, `nl_join` nas duas variantes e todas as funções `*_cost`.

---

## Em resumo (leia primeiro)

- **Seleção** (o `WHERE` do SQL): buscar registros por igualdade (`dre = 123`) ou por faixa (`cr > 9`), de três jeitos:
  - **varrendo** a tabela inteira;
  - **pela organização** da tabela (B+ ou hash);
  - **por um índice secundário**.

  Comparar o custo desses três caminhos é parte do resultado do trabalho.
- **Ordenação externa:** ordenar uma tabela maior que a memória, criando pedaços ordenados ("runs") e intercalando-os. O P5 precisa dela para o sort-merge join, então **entregue cedo**.
- **Nested-loop join**, em duas versões:
  - **em blocos:** carrega um pedaço da tabela de fora e varre a de dentro inteira;
  - **indexado:** para cada registro de fora, busca o par pelo índice da de dentro.
- **`access.py`:** funções pequenas que dizem "como ler este lado do join" (varrendo, em ordem ou por busca). O P5 usa as mesmas.
- **Funções de custo:** cada operador tem uma função irmã que calcula o custo pela **fórmula do livro**. A bancada compara esse número com o que foi medido.
- **Regra principal:** nunca carregue uma tabela inteira numa lista do Python. Você só pode guardar o que cabe nas páginas reservadas (M).
- **Resultado interessante para o relatório:** no nested-loop, a política LRU pode errar quase tudo e a MRU acertar quase tudo.

---

## 1. O que você entrega

| Arquivo | Conteúdo |
| :--- | :--- |
| `cbd/exec/access.py` | `read_all`, `read_ordered`, `lookup`, `can_order`, `can_lookup`, `fetch_cost` |
| `cbd/exec/selection.py` | `select_eq`, `select_range` e `*_cost` |
| `cbd/exec/external_sort.py` | `external_sort`, `external_sort_cost` |
| `cbd/exec/nested_loop.py` | `nl_join`, `nl_join_cost` |
| `tests/test_access.py`, `test_selection.py`, `test_external_sort.py`, `test_nested_loop.py` | testes |

Enquanto a camada 3 não chega, teste a **lógica** com um dublê (`FakeTable` sobre uma lista Python que implementa `TableLike`). As **contagens** só podem ser testadas com a pilha real.

---

## 2. Regras de memória (geral §3.4). Valem para todo o seu código

1. Qualquer lista, dict ou heap Python que acumule **registros** precisa estar dentro de um `with eng.bp.reserve_work(k)` e ter no máximo `k · bfr` registros.
2. `bfr` da entrada: `table.stats().bfr` quando a entrada é uma tabela. Quando é um fluxo (ex.: a saída de outro operador), estime com `_bfr_from_sample(schema, amostra)`: média de `len(RecordCodec(schema).encode(r))` em até 100 registros, então `SlottedPage.max_record(B) // (média + 4)`.
3. **Ponteiros** (para `sort_ptrs`) também ocupam memória: no máximo `k · (B // ptr_size)` por lote.
4. Iteradores de entrada fixam 1 página cada (DATA/INDEX), e temporários fixam 1 página cada (WORK). Planeje: **reserva + temporários abertos ≤ M**.
5. Feche os geradores que abandonar (`it.close()`) ou use `contextlib.closing`. Pin vazado aparece na fixture como `pin sem unpin`.

---

## 3. `access.py`: adaptador das entradas (usado por você e pelo P5)

```python
def _check(side):
    t, c, p = side.table, side.column, side.path
    if p is Path.PRIMARY and t.data.spec.key != c:
        raise NotApplicable(f"{t.name}.{c} não é a chave da organização ({t.data.spec.key})")
    if p is Path.SECONDARY and c not in t.indexes:
        raise NotApplicable(f"{t.name}.{c} não tem índice secundário")

def read_all(side):                       # ordem física
    for _, rec in side.table.data.scan():
        yield rec

def can_order(side) -> bool:
    _check(side)
    d, ix = side.table.data, side.table.indexes.get(side.column)
    return (side.path is Path.PRIMARY and d.spec.org is Org.BTREE) or \
           (side.path is Path.SECONDARY and ix.spec.kind is IndexKind.BTREE)

def read_ordered(side):                   # NÃO é gerador: devolve None ou um gerador
    if not can_order(side): return None
    d = side.table.data
    if side.path is Path.PRIMARY:
        return (rec for _, rec in d.range(None, None))
    ix = side.table.indexes[side.column]
    return (d.fetch(ptr) for _, ptr in ix.range(None, None))

def can_lookup(side) -> bool:
    _check(side); return side.path is not Path.SCAN

def lookup(side, value):                  # None ou gerador
    if not can_lookup(side): return None
    d = side.table.data
    if side.path is Path.PRIMARY:
        return (rec for _, rec in d.search(value))
    return (d.fetch(p) for p in side.table.indexes[side.column].search(value))

def fetch_cost(table) -> float:           # blocos de um DataFile.fetch (geral §5.4)
    return table.data.height if table.data.spec.org is Org.BTREE else 1
```

`read_ordered` e `lookup` **não podem** ser funções geradoras: um gerador nunca devolve `None`. Elas devolvem `None` ou um gerador criado dentro.

---

## 4. Seleções (`selection.py`), enunciado §5.2

### 4.1 Algoritmos por caminho

| Tipo | `SCAN` (config. a) | `PRIMARY` (config. b) | `SECONDARY` (config. c) |
| :--- | :--- | :--- | :--- |
| Igualdade UNIQUE | `scan` + filtro; **para no 1º** se `schema.get(col).unique` | `data.search(v)` | `index.search(v)` → `fetch` |
| Faixa UNIQUE | `scan` + filtro | BTREE: `data.range(lo, hi)`; HASH: **`NotApplicable`** | índice BTREE: `index.range` → `fetch`; índice HASH: **`NotApplicable`** |
| Faixa NOT UNIQUE | `scan` + filtro | BTREE NOT UNIQUE: `data.range` | índice BTREE: `index.range` → `fetch` |

- `PRIMARY` em HEAP → `NotApplicable` (heap não tem chave de organização).
- `NotApplicable` vira "N/A" no CSV da bancada; não trate como erro.
- **`sort_ptrs=True`** (só `SECONDARY`, só quando o `Ptr` é `RID`): dentro de `reserve_work(k)`, junte ponteiros em lotes de até `k · (B // 8)`, ordene por `(block, slot)` e faça o `fetch` em ordem. Blocos repetidos viram *hit*, e o custo cai de `n` para ~`min(n, b_r)` (fórmula de Cardenas). É a variante "bitmap/RID sort" dos livros (SKS 15.3.2). Para `Ptr` de B+ (`(k, seq)`) ignore, porque ordenar não ajuda a descida.
- Preencha `info`: `{"variant": "scan|scan-stop|primary-hash|primary-btree|secondary-hash|secondary-btree|…", "matches": n}`.

### 4.2 Fórmulas de custo (`select_eq_cost`, `select_range_cost`)

A notação está no geral §12. Para B+ usamos `h` = `stats()["levels"]` (inclui as folhas: os registros **estão** nas folhas do arquivo primário, então o "+1 bloco de dados" do SKS já está dentro de `h`). Para índice secundário, `h_i` = `levels` do índice.

Registros esperados:
- igualdade em UNIQUE: `n = 1`;
- igualdade em NOT UNIQUE: `n = n_r / V(A,r)`;
- faixa: `n = n_r · (hi − lo) / (max − min)`, com uniformidade e limite aberto = min/max (SKS 16.3). Para CHAR/DATE converta para número (ordinal/código) ou use `sel` passado pela bancada em `info["sel"]` (veja §4.3).

| Caso (SKS 7ª ed., Fig. 15.3) | Leituras estimadas |
| :--- | :--- |
| A1 scan | `b_r` (igualdade UNIQUE: `b_r / 2` em média) |
| A2 B+ primário, igualdade na chave UNIQUE | `h` |
| A3 B+ primário, igualdade NOT UNIQUE | `h − 1 + ⌈n / f_r⌉` |
| Hash primário, igualdade | `1 + overflow_blocks / buckets` (UNIQUE); para NOT UNIQUE, os blocos da cadeia do bucket ≈ `1 + overflow_blocks / buckets` mais `⌈n / f_r⌉ − 1` se os iguais transbordarem |
| A4 secundário, igualdade (B+) | `h_i + (blocos da posting) + n · fetch_cost` |
| A4 secundário, igualdade (Hash) | `1 + overflow/buckets + n · fetch_cost` |
| A5 B+ primário, faixa | `h − 1 + ⌈n / f_r⌉` |
| A6 secundário, faixa | `h_i − 1 + (folhas do índice na faixa) + n · fetch_cost` |
| A6 com `sort_ptrs` | idem, trocando `n · fetch_cost` por `b_r · (1 − (1 − 1/b_r)^n)` (Cardenas) |

Seeks (`Cost.seeks`): scan = 1; descida de árvore = 1 por nível; cada `fetch` aleatório = 1. Preencha `Cost.formula` com o texto e os números (ex.: `"A3: h-1+ceil(n/f) = 3-1+ceil(100/25) = 6"`). O P6 joga isso direto no relatório.

### 4.3 Parâmetro de seletividade

A bancada escolhe `lo`/`hi` para obter 0,1% e 50% (§5.2.2 do enunciado). A estimativa pela uniformidade pode errar se a distribuição não for uniforme (ex.: CR). **Não corrija isso no custo**: o erro vira análise no relatório. Se `info` vier com `"sel"`, preencha **também** `Cost` com o `n` real numa segunda linha da fórmula, para mostrar as duas.

---

## 5. Ordenação externa (`external_sort.py`)

O enunciado não pede para medir a ordenação sozinha, mas o sort-merge join (P5) depende dela.

### 5.1 Algoritmo (SKS 15.4, merge-sort externo)

```python
_END = object()

def external_sort(eng, source, schema, key, n_blocks_hint=None, mem=None, info=None):
    M, bp, cat = (mem or eng.M), eng.bp, eng.catalog   # daqui para baixo, "M" = memória DESTE sort
    if M < 3:
        raise ValueError("external_sort precisa de mem >= 3 (fan-in >= 2)")
    k = schema.idx(key)
    keyf = lambda r: (r[k] is None, r[k])              # None vai para o fim
    it = iter(source)
    sample = list(islice(it, 100))
    bfr = _bfr_from_sample(schema, sample)             # §2
    it = chain(sample, it)
    runs = []
    try:
        # ---- fase 1: run montado em M-1 páginas reservadas; o frame WORK restante é a página de saída
        while True:
            with bp.reserve_work(M - 1) as pages:
                buf = list(islice(it, pages * bfr))
                if not buf:
                    break
                buf.sort(key=keyf)
                peek = next(it, _END)
                if peek is _END and not runs:          # tudo coube: 0 gravações, nenhum temporário
                    yield from buf
                    return
                t = cat.temp(schema)                   # grava o run DENTRO da reserva (1 frame livre)
                for r in buf:
                    t.append(r)
                t.finish()
                runs.append(t)
            if peek is _END:
                break
            it = chain([peek], it)
        # ---- fase 2: fusões com fan-in M-1 (+1 de saída); a ÚLTIMA tem fan-in M e não grava
        while len(runs) > M:
            nxt = []
            for i in range(0, len(runs), M - 1):
                g = runs[i:i + M - 1]
                t = cat.temp(schema)
                for r in heapq.merge(*(x.scan() for x in g), key=keyf):
                    t.append(r)
                t.finish()
                nxt.append(t)
                for x in g:
                    x.drop()
            runs = nxt
        yield from heapq.merge(*(x.scan() for x in runs), key=keyf)
    finally:
        for x in runs:
            x.drop()                                   # também se o consumidor abandonar o gerador
```

Pontos em que a IA costuma errar:

- O run é gravado **dentro** da reserva: `M − 1` frames reservados + 1 frame da página de saída do `TempFile` = `M`. O teste com `M = 3` comprova.
- Fusão: `M − 1` runs abertos (1 página fixada cada) + 1 de saída = `M`. Na última fusão, `M` runs abertos e nenhuma saída.
- `drop()` num run já descartado na fase 2 não pode falhar: torne o `drop` idempotente, ou tire o run da lista ao descartá-lo.
- `heapq.merge` fica com **um registro por run** em memória. Isso está dentro da página fixada de cada run e não viola a regra.
- `info`: `runs` (iniciais), `passes` (número de passadas de fusão), `in_memory` (bool).

### 5.2 Custo (`external_sort_cost(eng, n_blocks, mem=None)`)

Use `M = mem or eng.M`, igual ao operador. Com `b = n_blocks` e `P = ⌈log_{M−1}(⌈b / (M−1)⌉)⌉` passadas de fusão (o expoente é o número de runs iniciais):

| Item | Valor |
| :--- | :--- |
| `b ≤ M − 1` | `reads = b`, `writes = 0` |
| caso geral | `reads = b·(P + 1)`, `writes = b·P` → total `b(2P + 1)` (SKS, sem gravar a saída final) |
| seeks (SKS) | `2⌈b / M⌉ + b·(2P − 1)` (com b_b = 1) |

**Diferença esperada medido × estimado:** páginas de run que nunca saíram da área `WORK` antes do `drop()` não são gravadas (geral §3.4 / P2 §5.4). Com `b` próximo de `M` o medido fica **abaixo** da fórmula. Documente; não "conserte" gravando à força.

---

## 6. Nested-loop join (`nested_loop.py`), enunciado §5.1

### 6.1 Escolha da variante e do externo (`outer="auto"`)

| Situação | Variante | Externo |
| :--- | :--- | :--- |
| nenhum lado com caminho (config. a) | **block** (BNL) | o de **menos blocos** (`b_r`) |
| só um lado com `can_lookup` (b, d com 1 indexado) | **indexed** (INL) | o lado **sem** caminho; o indexado é o interno |
| os dois com caminho (c, d, e) | **indexed** | o de **menos registros** (`n_r`), porque o custo é `b_ext + n_ext · c` |
| `outer="r"` / `"s"` forçado | indexed se o interno tiver caminho, senão block | o indicado |

Registre em `info`: `variant`, `outer` ("r"/"s"), `outer_blocks`, `passes` (iterações do laço externo no BNL).

### 6.2 BNL

```python
with bp.reserve_work(M - 2) as k:                  # bloco externo: M-2 páginas
    cap = k * bfr_outer
    outer_it = read_all(outer)
    while True:
        chunk = list(islice(outer_it, cap))
        if not chunk: break
        table = defaultdict(list)                   # hash em memória SÓ sobre o bloco externo (CPU)
        for r in chunk: table[r[ko]].append(r)
        for s in read_all(inner):                   # varre o interno inteiro uma vez por bloco
            for r in table.get(s[ki], ()):
                yield join(r, s)                    # sempre r_rec + s_rec na ordem dos ARGUMENTOS (geral §7.3)
```

- O dicionário sobre o bloco externo **não muda o I/O**: o algoritmo continua sendo nested-loop em blocos, porque o interno é varrido `⌈b_ext / (M − 2)⌉` vezes. Sem ele, a CPU do Python inviabiliza tabelas grandes (comparações `n_r × n_s`). Explique isso no relatório.
- O `outer_it` fica suspenso com 1 página `DATA` fixada enquanto o interno é varrido (outra página `DATA`). Na conta: `M − 2` (`WORK`) + 2 (`DATA`) = `M` páginas de trabalho.
- **Interação com o buffer (o resultado mais interessante para o relatório):** o interno é lido pelo `DATA`. Se `b_inner ≤ capacity(DATA)`, as varreduras depois da 1ª viram *hits*, e o medido cai muito abaixo da fórmula. Se `b_inner` for um pouco maior, LRU sofre *sequential flooding* (todo acesso é miss) e MRU acerta quase tudo. Avise o P6 para incluir esse caso na matriz.

### 6.3 INL (indexed nested-loop)

```python
for r in read_all(outer):
    for s in lookup(inner, r[ko]):
        yield join(r, s)
```

O `lookup` cria um gerador por registro externo, e cada um faz pin/unpin. Feche-o ao terminar (o `for` esgota). Chaves `None` não casam (pule-as).

### 6.4 Custo (`nl_join_cost`)

| Variante | Leituras (SKS 15.5) |
| :--- | :--- |
| block | `⌈b_ext / (M − 2)⌉ · b_int + b_ext`; seeks `2⌈b_ext / (M − 2)⌉` |
| indexed | `b_ext + n_ext · c`, com `c` = custo de uma busca de igualdade no interno pelo caminho dele (use `select_eq_cost(..., path)` do interno, com `n` = registros por chave = `n_int / V(col, int)`) |

O custo usa a **mesma** regra de escolha do externo que o operador (§6.1). Extraia a regra para uma função `_plan(eng, r, s, outer)` usada pelos dois, para que não divirjam.

---

## 7. Testes obrigatórios

| Teste | Verifica |
| :--- | :--- |
| `test_select_matrix` | para HEAP, BTREE (U e NU) e HASH (U e NU) × SCAN/PRIMARY/SECONDARY × eq/faixa: resultado = filtro Python sobre os dados; combinações inválidas → `NotApplicable` |
| `test_select_costs` | cache frio, área `DATA` grande: o medido (`io.reads`) bate com `*_cost` em ±1 bloco para A1, A2, A3, A5 e hash eq |
| `test_sort_ptrs` | `SECONDARY` NOT UNIQUE com 50% de seletividade: com `sort_ptrs` as leituras ≤ `b_r`; sem ele, ≈ `n` |
| `test_external_sort_small_M` | `M = 3`, 50 blocos: saída ordenada, igual a `sorted()`, `passes` = fórmula; `bp.pinned() == 0` |
| `test_external_sort_in_memory` | `b < M`: 0 gravações, nenhum temporário criado |
| `test_external_sort_dups_nulls` | chaves repetidas e `None` (vão para o fim) |
| `test_bnl_exact_cost` | área `DATA` = 2 frames (nada fica em cache), cache frio: `io.reads == ⌈b_r/(M−2)⌉·b_s + b_r` |
| `test_bnl_flooding` | `DATA` = `b_s − 1` frames: LRU → misses ≈ fórmula; MRU → bem menos. Guarde os números para o relatório |
| `test_inl` | resultado igual ao BNL; leituras ≈ `b_r + n_r·c` |
| `test_outer_choice` | `outer="auto"` escolhe o menor; resultado na orientação `r + s` mesmo invertido |
| `test_memory_bound` | monkeypatch para contar o maior `len(chunk)`: nunca passa de `k·bfr` |

---

## 8. Armadilhas da IA (enunciado §7.1). Procure ativamente

- **`list(read_all(outer))` inteiro** antes do laço: estrutura Python maior que a área reservada (o enunciado cita exatamente isso).
- Ordenação "externa" que chama `sorted(source)` direto.
- Fan-in maior que `M − 1` (abre runs demais ao mesmo tempo → `BufferFullError` ou, pior, contagem errada).
- A última fusão gravando a saída num temporário (custo a mais que a fórmula não tem).
- Externo escolhido por número de **registros** no BNL (o certo é por blocos) ou o contrário no INL.
- Saída do join na ordem invertida quando o algoritmo troca externo e interno.
- `read_ordered`/`lookup` escritas como geradores (nunca devolvem `None`; o P5 cai no ramo errado sem erro).
- Busca binária "sobre registros" (ex.: carregar o arquivo B+ numa lista para achar a faixa) em vez de usar `data.range`.
- Iterador abandonado no meio (`break`) sem `close()`: pin vazado.

Registre em `ai/P4/review.md`. Guarde uma versão **executável** do BNL ingênuo da IA (que carrega o externo inteiro) em `ai/P4/raw/` para o teste ingênuo × refatorado: com `M` pequeno a versão ingênua viola a memória, e a refatorada não.
