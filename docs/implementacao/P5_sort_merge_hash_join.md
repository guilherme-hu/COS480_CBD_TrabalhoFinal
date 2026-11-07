# P5 — Sort-Merge Join e Partition-Hash Join

> Leia junto com [`00_ARQUITETURA_GERAL.md`](00_ARQUITETURA_GERAL.md). As assinaturas públicas estão no documento geral (§7.2) e só mudam combinando com quem usa a interface. Aqui está uma proposta de *como* implementar. **As tabelas de decisão trazem recomendações, não decisões fechadas:** o agente debate cada uma com você antes de codar ([`AGENTS.md`](../../AGENTS.md) §3), e este arquivo pode ser atualizado com o que for decidido.

**Peso:** ~16%. Você **consome** o P4 (`access.py`, `external_sort`, `select_eq_cost`), a camada 3 (P2/P3: `Table`, `HashFile.bucket_of/scan_bucket`, `Catalog.temp`, `hash_value`) e o P1 (`reserve_work`). Você **fornece** os dois joins e suas funções de custo para o P6.

**Dependências e marcos:** o `access.py` do P4 sai até **09/11** e o `external_sort` até **12/11**. Até lá, escreva o hash join, que não depende do sort, e teste o sort-merge com um `external_sort` provisório (`sorted()`, só em teste e marcado como tal). Até **14/11**: os dois joins sobre heap, com testes. Até **16/11**: as variantes que aproveitam índices e as funções de custo.

---

## Em resumo (leia primeiro)

- **Você faz dois dos três algoritmos de join** (o terceiro, nested-loop, é do P4).
- **Sort-merge join:** ordena as duas tabelas pela coluna do join e as percorre lado a lado, como num zíper.
  - Se uma tabela já está em ordem (organizada por B+ nessa coluna), não precisa ordenar.
  - Como só fazemos o join PK→FK, as chaves só repetem no lado detalhe: o registro do mestre fica parado enquanto passam os detalhes com a mesma chave.
- **Hash join (partition-hash / GRACE):** divide as duas tabelas em **partições** pequenas com uma função de hash, de forma que cada par de partições caiba na memória, e junta par a par.
  - Se a partição ainda não cabe, divide de novo (**recursão**).
  - Se todas as chaves são iguais (**skew**), cai para nested-loop.
- **Aproveitar índices:** se uma tabela já é organizada por hash na coluna do join, ela "já vem particionada" e economiza leituras e gravações. Índices B+ não ajudam o hash join; isso é uma **conclusão válida** para o relatório.
- **Funções de custo:** cada join tem a função que calcula o custo pela fórmula do livro, para comparar com o medido.
- **A memória é limitada (M páginas):** nada de carregar uma tabela inteira num dicionário do Python.
- **Dependência:** você usa a ordenação externa e o `access.py` do P4. Até eles chegarem, comece pelo hash join.

---

## 1. O que você entrega

| Arquivo | Conteúdo |
| :--- | :--- |
| `cbd/exec/sort_merge.py` | `sort_merge_join`, `sort_merge_join_cost` |
| `cbd/exec/hash_join.py` | `hash_join`, `hash_join_cost` |
| `tests/test_sort_merge.py`, `tests/test_hash_join.py` | testes (§6) |

---

## 2. Regras que valem para os dois joins

- **Memória:** geral §3.4 e P4 §2. Toda estrutura Python que acumula registros fica dentro de `eng.bp.reserve_work(k)`, com no máximo `k · bfr` registros. O `bfr` de uma tabela vem de `table.stats().bfr`; o de uma partição ou fluxo vem da amostra (`_bfr_from_sample` do P4: importe de `cbd.exec.external_sort`).
- **Saída:** `r_rec + s_rec` na ordem dos **argumentos**, mesmo quando o algoritmo inverte os lados (geral §7.3).
- **Chave `None`** nunca casa: descarte esses registros na entrada.
- **`info`:** `variant`, `build` ("r"/"s"), `partitions`, `levels` (profundidade de recursão), `sorted_r`/`sorted_s` (bool: precisou ordenar?), `index_used` (bool).
- **Temporários:** `eng.catalog.temp(schema)`. Sempre `drop()` num `finally`, inclusive quando o consumidor abandona o gerador.
- **Mesma regra no operador e no custo:** extraia a decisão de variante e de lados para uma função `_plan(eng, r, s, ...)` usada pelo operador e pelo `*_cost`.

---

## 3. Sort-merge join (`sort_merge.py`), SKS 15.5.4

### 3.1 Entradas ordenadas

Para cada lado:

| Caminho do lado | Como obter o fluxo ordenado | `index_used` |
| :--- | :--- | :--- |
| `PRIMARY` em BTREE | `read_ordered(side)` → `data.range(None, None)`, sem ordenar | sim |
| `SECONDARY` com índice BTREE | `read_ordered(side)` → `index.range` + `fetch` (acesso aleatório por registro). **Este é o ponto a medir nas configurações d/e** | sim |
| `PRIMARY` em HASH, `SECONDARY` HASH, `SCAN` | `external_sort(eng, read_all(side), schema, col, n_blocks_hint=b, mem=m)` | não |

**Divisão da memória:** as duas fusões finais rodam **ao mesmo tempo** (cada uma com até `mem` runs abertos). Como a fusão não guarda grupo de duplicatas (§3.2), toda a área `WORK` fica para os sorts. Portanto:

```python
need = [side for side in (r, s) if read_ordered(side) is None]
mem_each = eng.M // max(1, len(need))
if need and mem_each < 3:
    raise ValueError(f"sort-merge precisa de M >= {3 * len(need)}")
```

Isso aumenta o número de passadas de cada sort em relação à fórmula com `M` inteiro. O `*_cost` usa o **mesmo** `mem_each`, então estimado e medido continuam comparáveis. Explique no relatório.

### 3.2 Fusão (um lado UNIQUE)

Só fazemos o join PK→FK (geral §0): o lado mestre é UNIQUE na coluna do join, então cada chave aparece **no máximo uma vez** nele. A fusão fica simples e não precisa guardar grupo de duplicatas. O SKS 15.5.4 trata o caso geral, com repetição dos dois lados, que está fora do escopo.

```python
# u = lado UNIQUE (mestre), d = detalhe; uit/dit = fluxos ordenados de cada um
ue, de = next(uit, END), next(dit, END)
while ue is not END and de is not END:
    ku, kd = ue[iu], de[id_]
    if ku < kd:
        ue = next(uit, END)
    elif ku > kd:
        de = next(dit, END)
    else:
        yield orient(ue, de)        # r_rec + s_rec na ordem dos argumentos (geral §7.3)
        de = next(dit, END)         # o mestre fica: o próximo detalhe pode ter a mesma chave
```

- O lado UNIQUE vem de `schema.get(col).unique`. Se **nenhum** lado for UNIQUE, levante `NotApplicable("sort-merge: só PK→FK no escopo")`.
- Memória: um registro de cada lado, dentro das páginas já fixadas dos fluxos. Nenhuma reserva extra.
- Os `None` já foram filtrados (§2).

### 3.3 Custo (`sort_merge_join_cost`)

A fusão do join **é** a última passada de cada sort (pipeline), então:

```
custo = Σ_lados  C(lado)
C(lado) = external_sort_cost(eng, b_lado, mem=mem_each)           se precisa ordenar  (já inclui a leitura da fusão final)
        = h − 1 + b_lado                                          se PRIMARY BTREE (folhas)
        = h_i − 1 + folhas_índice + n_lado · fetch_cost(tabela)   se SECONDARY BTREE
```

Compare com a fórmula do livro `b_r + b_s + custo dos sorts` (SKS), que materializa a saída do sort. Explique a diferença de `b_r + b_s` no relatório: o pipeline economiza uma gravação e uma leitura de cada lado.

---

## 4. Partition-hash join (`hash_join.py`), SKS 15.5.5 (GRACE)

### 4.1 Plano (`_plan`)

1. **Construtor (build):** o lado de **menos blocos**, salvo `build="r"/"s"`. O outro é o *probe*.
2. **Variante**, na ordem:

| Variante | Condição | Fases |
| :--- | :--- | :--- |
| `in-memory` | `b_build ≤ M − 2` | sem partição: carrega o build num dict e varre o probe |
| `bucket-join` | **os dois** lados `PRIMARY` em HASH, com mesmos `n_buckets` e `seed` e mesmo tipo de chave | sem partição: bucket `i` de r × bucket `i` de s |
| `prepartitioned` | **um** lado `PRIMARY` em HASH | só o outro lado é particionado, alinhado aos buckets (§4.3) |
| `grace` | demais casos (inclusive índices B+ e secundários, que não ajudam o hash join) | particiona os dois |

3. **Número de partições:** `n_h = ⌈1.2 · b_build / (M − 2)⌉` (fator de folga do SKS para partições desiguais), limitado a `M − 1`. Se o cálculo der mais que `M − 1`, use `M − 1` e conte com a recursão (§4.4).

### 4.2 GRACE

```python
def _partition(eng, side_iter, schema, col, n_h, seed):
    parts = [eng.catalog.temp(schema) for _ in range(n_h)]     # n_h páginas WORK fixadas (uma por saída)
    k = schema.idx(col)
    for rec in side_iter:
        if rec[k] is not None:
            parts[hash_value(rec[k], seed) % n_h].append(rec)
    for p in parts: p.finish()
    return parts
```

- A **seed** do particionamento é `100 + nível`, sempre **diferente** da seed de um `HashFile` e da do nível anterior. Com a mesma função, a recursão não separaria nada.
- **Build/probe** da partição `i`:

```python
with bp.reserve_work(M - 2) as k:            # sobra 1 frame para ler o probe e 1 de folga
    cap = k * bfr_build
    table = defaultdict(list); n = 0
    for rec in build_parts[i].scan():
        n += 1
        if n > cap: raise _Overflow            # partição não coube -> §4.4
        table[rec[kb]].append(rec)
    for rec in probe_parts[i].scan():
        for b in table.get(rec[kp], ()): yield orient(b, rec)
```

O `_Overflow` precisa ser tratado **antes** de qualquer `yield` daquela partição, senão haveria resultados duplicados. Por isso a construção é completa antes da sondagem.

### 4.3 Aproveitando o hash primário (`prepartitioned` e `bucket-join`)

O `HashFile` já divide os dados por `bucket_of(k) = hash_value(k, seed) % N`. Agrupe os buckets em `n_h` partições: **partição `j` = buckets `{i : i % n_h == j}`**.

- O lado HASH **não é particionado**: a partição `j` dele é lida com `scan_bucket(i)` para cada `i ≡ j (mod n_h)`. Economiza `2·b` (gravar e reler as partições desse lado).
- O outro lado é particionado com a **mesma** regra: `hf.bucket_of(rec[k]) % n_h`, usando o `bucket_of` do arquivo hash e não o `hash_value` com seed própria.
- Se o lado HASH for o build e a partição `j` não couber, materialize-a num temporário e caia na recursão do §4.4 (rara: só com skew).
- **`bucket-join`:** com N e seed iguais nos dois lados, o bucket `i` de r só casa com o bucket `i` de s. Cost = `b_r + b_s` (sem gravações). O P6 configura esse caso forçando `OrgSpec.params["buckets"]` e `params["seed"]` iguais nas duas tabelas. **Avise o P2** para aceitar `params["seed"]`.

Índices B+, índices secundários HASH e B+ primário **não** são usados pelo hash join (ler pelo índice só acrescentaria acessos aleatórios). Rode `grace` e marque `index_used=False`. Essa conclusão vai para o relatório e é legítima: o enunciado pede a comparação, não que todo índice ajude.

### 4.4 Recursão e skew

Com o build no lado mestre (UNIQUE), não há skew: chaves distintas se espalham entre as partições. O skew só aparece se o build for forçado para o lado detalhe (`build=` explícito), como no experimento do lado construtor (P6 §4.2).

Se uma partição de build não couber (`_Overflow`):

1. reparticione **o par** (build_i, probe_i) com `seed = 100 + nível + 1` e `n_h' = min(M − 1, ⌈1.2 · b_i / (M − 2)⌉)`;
2. no máximo **3 níveis**;
3. se ainda assim não couber (skew: muitas linhas com a **mesma** chave, que nenhum hash separa), faça **nested-loop em blocos** desse par: blocos de `M − 2` páginas do build × varredura do probe. Marque `info["skew_fallback"] += 1`.

Teste obrigatório: todas as chaves iguais.

### 4.5 Custo (`hash_join_cost`)

| Variante | Leituras + gravações (SKS 15.5.5) |
| :--- | :--- |
| `in-memory` | `b_r + b_s` |
| `bucket-join` | `b_r + b_s` |
| `prepartitioned` | `b_hash + 3·b_outro + 2·n_h` (o outro é lido, gravado e relido) |
| `grace` sem recursão | `3(b_r + b_s) + 4·n_h` |
| `grace` com recursão | `2(b_r + b_s)·⌈log_{M−1}(b_build) − 1⌉ + b_r + b_s` |

Seeks (SKS): `2(⌈b_r / b_b⌉ + ⌈b_s / b_b⌉) + 2·n_h` com `b_b = 1`. Preencha `Cost.formula` com o texto e os valores.

**Diferença esperada:** como no sort (P4 §5.2), páginas de partição que nunca saem da área `WORK` antes do `drop()` não são gravadas. Com partições pequenas, o medido fica abaixo da fórmula. Documente.

---

## 5. Matriz que você precisa suportar (enunciado §5.1)

1 tipo de join (NATURAL PK→FK) × 2 algoritmos (os seus) × configurações a–e × 3 combinações de tamanho, com b e d nas duas orientações (geral §7.4). O NLJ é do P4. Para cada configuração, a variante que o seu código escolhe:

| Config | Sort-merge | Hash join |
| :--- | :--- | :--- |
| a (nenhum índice) | ordena os dois | grace (ou in-memory) |
| b (r PRIMARY) | r BTREE: sem sort em r; r HASH: ordena r | r HASH: prepartitioned; r BTREE: grace |
| c (ambos PRIMARY) | BTREE: nenhum sort | HASH: bucket-join (N/seed iguais) ou prepartitioned |
| d (r PRIMARY, s SECONDARY) | r BTREE: sem sort; s índice BTREE: ordem pelo índice | r HASH: prepartitioned; resto grace |
| e (ambos SECONDARY) | ordem pelos índices BTREE (ou sort, se HASH) | grace (`index_used=False`) |

Deixe esta tabela, preenchida com o que o código realmente faz, em `ai/P5/variantes.md` para o P6 citar no relatório.

---

## 6. Testes obrigatórios

| Teste | Verifica |
| :--- | :--- |
| `test_smj_vs_bruteforce` | tabelas pequenas aleatórias (mestre UNIQUE, detalhe com repetições e `None`): multiconjunto da saída = laço duplo Python |
| `test_hj_vs_bruteforce` | idem para hash join |
| `test_orientation` | `build="s"` e `build="r"` dão a mesma saída `r + s` |
| `test_smj_btree_no_sort` | r e s BTREE PRIMARY na coluna: nenhum temporário criado (`info["sorted_r"] is False`) |
| `test_smj_no_unique` | nenhum lado UNIQUE na coluna → `NotApplicable` |
| `test_hj_recursion` | `M = 4`, build 20× maior que M: `levels ≥ 1`, resultado correto |
| `test_hj_skew` | build forçado no lado detalhe, todas as chaves iguais: `skew_fallback ≥ 1`, resultado = produto cartesiano |
| `test_hj_prepartitioned` | r HASH na coluna: só s gera temporários; leituras ≈ fórmula |
| `test_hj_bucket_join` | r e s HASH com N/seed iguais: `io.writes == 0` |
| `test_costs` | área `WORK` pequena (força as gravações): medido ≈ estimado, ±10%, para grace e smj com sort |
| `test_abandon` | consumir só 5 tuplas e `close()`: nenhum temporário sobra em `root_dir` e `pinned() == 0` |

O P6 compara tudo também com o SQLite. Os testes acima pegam os erros antes, em escala pequena.

---

## 7. Armadilhas da IA (enunciado §7.1). Procure ativamente

- **Dict de build com a tabela inteira** ("partition" de mentira): estrutura Python maior que a área reservada. É o erro que o enunciado cita.
- Particionamento recursivo com a **mesma** função de hash (não separa nada; laço infinito ou estouro).
- `hash()` do Python nas partições (muda entre execuções; os resultados ficam não reprodutíveis).
- Sort-merge que avança o mestre junto com o detalhe quando as chaves batem: perde os demais detalhes da mesma chave.
- Duas fusões finais abertas ao mesmo tempo, cada uma com `M` runs (passa de M; `BufferFullError` ou, sem a checagem, contagem irreal).
- Partições abertas (`n_h`) maiores que `M − 1`.
- Temporários não apagados quando o gerador é abandonado.
- Saída invertida quando o build é `r`.

Registre em `ai/P5/review.md`. Guarde uma versão **executável** do hash join ingênuo da IA (build com a tabela inteira em memória) em `ai/P5/raw/` para o teste ingênuo × refatorado do P1.
