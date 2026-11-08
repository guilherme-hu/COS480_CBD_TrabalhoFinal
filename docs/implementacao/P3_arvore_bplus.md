# P3 — Árvore B+: Arquivo Primário (UNIQUE e NOT UNIQUE) e Índice Secundário

> Leia junto com [`00_ARQUITETURA_GERAL.md`](00_ARQUITETURA_GERAL.md). As assinaturas públicas estão no documento geral (§4, §5, §6) e só mudam combinando com quem usa a interface. Aqui está uma proposta de *como* implementar. **As tabelas de decisão trazem recomendações, não decisões fechadas:** o agente debate cada uma com você antes de codar ([`AGENTS.md`](../../AGENTS.md) §3), e este arquivo pode ser atualizado com o que for decidido.

**Peso:** ~18%, a parte de algoritmo mais difícil. Você **consome** a camada 4 (P1) e utilitários do P2 (`encode_key`, `key_size`, `RecordCodec`, `SlottedPage`). Você **fornece** `BTreeFile` (organização primária) e `BTreeIndex` (índice secundário), que o catálogo do P2 instancia e que P4/P5 usam.

**Marcos:** até **09/11**: `BTreeFile` com `insert`/`search`/`range`/`scan`/`fetch` e split propagando até a raiz. Até **14/11**: `delete` com redistribuição e fusão, `bulk_load`, `BTreeIndex` não-unique, `reorganize`.

---

## Em resumo (leia primeiro)

- **Você implementa a árvore B+ em disco**, usada de **dois jeitos**:
  1. **como organização da tabela:** os registros ficam nas folhas, em ordem da chave;
  2. **como índice secundário:** as folhas guardam só "chave → onde está o registro".
- **Cada nó da árvore é um bloco.** Buscar uma chave custa ler um bloco por nível (a **altura** da árvore). Os testes verificam exatamente isso.
- **Chaves repetidas:** internamente cada chave ganha um número de sequência escondido, `(chave, seq)`. Isso deixa toda entrada única e dá um "endereço" do registro que não muda quando a árvore se reorganiza.
- **Inserir:** se o nó enche, ele **divide** em dois (split), e a divisão pode subir até criar uma raiz nova.
- **Remover:** se o nó fica com menos de 50% ocupado, ele **pega emprestado** do vizinho ou **funde** com ele. A árvore pode diminuir de altura.
- **Carga em massa (bulk load):** monta a árvore de baixo para cima a partir de dados ordenados. É mais rápido e deixa a árvore mais compacta que inserir um por um, e a bancada compara os dois.
- **Índice NOT UNIQUE:** cada chave aparece **uma vez só** na folha, com a lista dos registros que têm essa chave, como o enunciado pede.
- **Dependências:** você usa o buffer do P1 e o codificador e a slotted page do P2. Combine com o P2 para ele entregar esses dois primeiro.

---

## 1. O que você entrega

| Arquivo | Conteúdo |
| :--- | :--- |
| `cbd/files/btree_node.py` | layout dos nós internos e das folhas, busca binária no nó, split/merge de nó (sem I/O) |
| `cbd/files/btree_core.py` | `_BPlusTree`: descida, inserção, remoção, bulk load, iteração de folhas. Parametrizado pelo "formato de folha" |
| `cbd/files/btree_file.py` | `BTreeFile` (DataFile BTREE) |
| `cbd/files/btree_index.py` | `BTreeIndex` (Index BTREE NOT UNIQUE) |
| `tests/test_btree_*.py` | testes (§8) |

Até o P1 entregar (05/11), use a fixture `bp` do `tests/conftest.py`. Até o P2 entregar o `codec`/`SlottedPage` (09/11), escreva os nós com `struct` puro e só a parte de folha do `BTreeFile` depende do `SlottedPage`. Combine com o P2 para ele entregar `slotted_page.py` e `encode_key` primeiro (são pequenos).

---

## 2. Decisões (enunciado §3.4). Coloque esta tabela no relatório

| Decisão | Escolha |
| :--- | :--- |
| Chave de ordenação | **um campo** (`OrgSpec.key`). Internamente a chave é sempre **composta `(chave, seq)`**: `seq` é um u64 oculto, `0` em árvore UNIQUE e crescente (contador no meta) em NOT UNIQUE. Isso torna toda entrada única e dá um ponteiro lógico estável |
| Comparação | sobre **bytes**: `encode_key(f, k) + seq.to_bytes(8, "big")`. O `encode_key` do P2 preserva a ordem, e comparar `bytes` em Python é rápido |
| Tamanho da chave | **fixo** (`key_size(f)`; VARCHAR preenchido até `size`) → fan-out fixo e calculável |
| Nó interno | `p0, (k1, p1), …, (kn, pn)` com chaves de tamanho fixo. **Fan-out** `n = (B − 8) // (ks + 8 + 4) + 1` (ks + 8 da chave composta, 4 do ponteiro) |
| Folha do arquivo primário | **slotted page** (P2) com registros completos, em ordem lógica pelo diretório de slots; encadeamento `prev`/`next` no `user_header` |
| Folha do índice | slotted page com entradas `(chave, carga)`; **sem repetição de chave** no índice NOT UNIQUE (§5) |
| Ocupação mínima | internos: `⌈n/2⌉` filhos. Folhas (registros de tamanho variável): **≥ 50% dos bytes úteis** (`params["min_fill"]`, padrão 0.5). A raiz fica isenta |
| Overflow do nó | **split** (não há redistribuição na inserção). Na remoção: **redistribuição** com o irmão se ele tiver sobra; senão **fusão** |
| Carga | **bulk loading** bottom-up com fator `params["fill"]` (padrão 0.9) quando `Catalog.load_table(bulk=True)`; inserção registro a registro quando `bulk=False`. A bancada compara as duas (ocupação e altura) |
| Ponteiros para pai | **não**: a descida guarda a pilha do caminho (`list[bno]`) |

---

## 3. Layout no disco

### 3.1 Meta (bno 1, `PageType.INDEX`)

```
magic "BPT1" | root u32 | height u32 | n_entries u64 | next_seq u64 | key_size u32 | unique u8 |
fill f32 | min_fill f32 | n_leaves u32 | n_internal u32 | n_posting u32
```

O meta fica **fixado em memória** enquanto o objeto está aberto? **Não**: isso prenderia um frame `INDEX` para sempre. Guarde uma cópia em atributos Python e grave o bloco com `pin` → alterar → `unpin(dirty=True)` quando o meta mudar (root, height, contadores). Contadores como `n_entries` podem ser gravados só em `close()`.

### 3.2 Nó interno (`PageType.INDEX`)

```
0   u8   is_leaf = 0
1   u8   pad
2   u16  n_keys
4   u32  p0
8   n_keys × [ key (CK bytes) | child u32 ]       CK = ks + 8 (chave composta)
```

Busca no nó: `bisect` manual sobre `memoryview` (sem montar uma lista Python das chaves a cada visita). Na descida pela chave `x`, escolha o filho `p_i` com `k_i ≤ x < k_{i+1}` (`p0` se `x < k1`).

### 3.3 Folha (slotted page, `PageType.DATA` no arquivo e `INDEX` no índice)

`user_header()` (16 bytes): `u8 is_leaf = 1 | u8 pad | u16 pad | u32 prev | u32 next | u32 reservado`.

- Folha de `BTreeFile`: cada slot = `CK (chave composta) + RecordCodec.encode(rec)`.
- Folha de `BTreeIndex` (§5).

A ordem lógica é a do diretório de slots: use **`insert_at`/`remove_at`**, nunca `insert`/`delete` (que reusam slots fora de ordem). A busca binária na folha compara os primeiros `CK` bytes de cada slot.

---

## 4. Algoritmos (`btree_core.py`)

### 4.1 Descida

```python
def _descend(self, ck: bytes) -> tuple[list[int], int]:
    path, bno = [], self.root
    for _ in range(self.height - 1):
        with self.bp.page(self.fid, bno, PageType.INDEX) as p:
            path.append(bno)
            bno = node.child_for(p.data, ck)
    return path, bno          # bno = folha
```

Cada nível custa 1 `pin`. A busca custa `height` pins, mais as folhas extras de uma faixa.

### 4.2 `search(key)` / `range(lo, hi, lo_inc, hi_inc)` / `scan()`

- `search(k)`: desce com `ck = encode_key(k) + b"\x00"*8` (menor composta com essa chave), varre a folha a partir do *lower bound* e segue `next` enquanto o prefixo de `ks` bytes for igual. Em árvore UNIQUE, para no 1º.
- `range`: desce por `lo` (ou pela folha mais à esquerda se `lo is None`). Pule os iguais a `lo` se `lo_inc=False`. Pare ao passar de `hi`.
- `scan()`: folha mais à esquerda (desça sempre por `p0`) e siga `next`, com `pin(..., seq=True)` e `unpin(toss=True)`. Depois de bulk load as folhas são contíguas, e as leituras saem sequenciais.
- Todos são **geradores** com uma página fixada por vez, `try/finally`, decodificando e fazendo `yield` enquanto a página está fixada (veja geral §6.1).

### 4.3 Inserção

1. `ck = encode_key(k) + seq` (UNIQUE: `seq=0` e, antes, `search(k)` → `DuplicateKey` se existir; NOT UNIQUE: `seq = next_seq; next_seq += 1`).
2. Desce (§4.1) e acha a posição na folha por busca binária.
3. Se `insert_at(pos, entrada)` couber, faz `mark_dirty` e termina.
4. Senão, **split da folha**: `new_page`, move a metade superior **por bytes** (até equilibrar os bytes usados), insere a entrada na metade certa e acerta `prev`/`next` (incluindo o `prev` da antiga vizinha da direita: 1 pin a mais). O separador que sobe = **CK do 1º slot da nova folha** (cópia; a entrada continua na folha).
5. Insere `(separador, nova)` no pai (o último da `path`). Se o pai estourar, **split interno**: a chave do meio **sobe** (não fica em nenhum dos dois).
6. Repete até a raiz. Se a raiz dividir, cria uma raiz nova com `p0 = antiga` e `(sep, nova)`, e `height += 1`.
7. Devolve `Ptr = (k, seq)`.

### 4.4 Remoção (`delete(ptr)` / `delete(key, ptr)` no índice)

1. Desce pela CK exata, acha o slot e faz `remove_at`. Se não achar: `KeyError`.
2. Se a folha ficou abaixo do mínimo e não é raiz:
   - escolha um irmão **com o mesmo pai** (o da esquerda se existir, senão o da direita);
   - **redistribuição:** se o irmão ficar ≥ mínimo depois de ceder, mova entradas de borda até equilibrar e atualize o separador no pai;
   - **fusão:** senão, junte o da direita no da esquerda, ajuste `next`/`prev` (e o `prev` do vizinho seguinte), remova o separador e o ponteiro do pai e chame **`bp.free_block(fid, bno)`** no nó removido (não `fm.free`: veja geral §3.2).
3. O pai pode ficar abaixo de `⌈n/2⌉` filhos: mesma lógica para nós internos. Na redistribuição interna, a chave do pai **desce** e a do irmão **sobe** (rotação).
4. Raiz interna com 0 chaves: o filho único vira a raiz, `height −= 1`, e `free_block` na raiz antiga.

### 4.5 `bulk_load(itens)` (arquivo vazio)

1. Verifique que a entrada vem **ordenada** pela chave (compare cada CK com a anterior; levante `ValueError` se não vier). Em NOT UNIQUE atribua `seq` crescente.
2. Encha cada folha até `fill × bytes úteis` e então `new_page` para a próxima. Encadeie, e guarde `(primeira CK, bno)` de cada folha numa lista. A lista tem tamanho `n_folhas` (tempo de carga, fora da medição).
3. Construa os níveis internos de baixo para cima com a mesma regra (`fill × n` filhos por nó) até sobrar um nó, que vira a raiz.
4. Grave o meta (`height`, `root`, contadores).
5. Cuidado com a **última** folha ou nó de cada nível: se ficar abaixo do mínimo, redistribua com o anterior.

### 4.6 `reorganize()`

`scan()` em um arquivo novo `<nome>__new` com `bulk_load` **preservando os `seq`** (passe os pares `(ck, rec)` já prontos). Os `Ptr = (key, seq)` continuam válidos, e os índices secundários **não** precisam ser reconstruídos (o `Table` reconstrói mesmo assim; é barato e seguro). Troque os arquivos com `fm.delete` + renomear (`os.replace`) e atualize o `fid`.

### 4.7 `check()`: verificação de invariantes (só para testes)

- todas as folhas na mesma profundidade (= `height`);
- chaves ordenadas dentro de cada nó e entre folhas (pelo `next`);
- cada separador limita corretamente as subárvores;
- ocupação ≥ mínimo em todo nó não-raiz;
- `prev`/`next` consistentes nos dois sentidos;
- a soma das entradas nas folhas é igual a `n_entries`.

---

## 5. `BTreeIndex` (índice secundário)

`BTreeIndex.create(bp, name, spec, key_field, data)` guarda `data.ptr_size`, `data.encode_ptr` e `data.decode_ptr`. Todas as páginas são `PageType.INDEX`.

### 5.1 UNIQUE (`spec.unique=True`): fora do escopo

A tabela de métodos de acesso (geral §5.3) só usa índice B+ secundário em campo NOT UNIQUE. Não implemente um modo UNIQUE separado. Se `spec.unique=True` chegar, levante `NotApplicable`, ou trate como NOT UNIQUE com 1 ponteiro por chave se o grupo decidir que precisa.

### 5.2 NOT UNIQUE: "sem repetição de chave nas folhas" (enunciado §4.2)

**Cada chave aparece uma única vez** numa folha. A entrada carrega a lista dos ponteiros (E&N chama de "bloco de ponteiros de registro"; o grupo chama de *posting list*):

```
entrada da folha = key (ks) | u32 count | u32 overflow_head | count_inline × ptr
```

- Enquanto a entrada couber em `B // 4` bytes, os ponteiros ficam **inline**.
- Ao passar desse limite, **todos** os ponteiros vão para uma **cadeia de blocos de ponteiros** (`overflow_head`, páginas com `u16 n | u32 next | n × ptr`, `PageType.INDEX`) e o inline fica vazio. É uma única regra, fácil de verificar.
- `count` é sempre o total de ponteiros da chave.
- Aqui a CK de comparação é **só `encode_key`** (sem `seq`), porque a chave já é única no nível da folha. Os nós internos usam CK = `ks` bytes.
- `insert(k, ptr)`: acha a entrada de `k`. Se existir, acrescenta o ptr (cresce a entrada com `remove_at` + `insert_at` da versão maior, ou faz split se não couber, ou move para a cadeia). Se não existir, cria a entrada com 1 ptr.
- `delete(k, ptr)`: remove o ptr exato (inline ou na cadeia, libertando com `free_block` os blocos vazios). Se `count` chegar a 0, remove a entrada (e pode haver underflow).
- `search(k)`: devolve os ponteiros inline e depois os da cadeia. Custo = `height` + blocos da cadeia.
- `range(lo, hi)`: devolve `(k, ptr)` para cada ptr de cada chave na faixa, em ordem de chave.
- `bulk_load(pares)`: os pares vêm ordenados por chave; agrupe as chaves iguais (grupo a grupo, em streaming) e monte as entradas.

**"Clustering":** veja geral §5.3. É este índice NOT UNIQUE sobre arquivo HEAP, HASH ou BTREE. Se a tabela é BTREE, o `Ptr` guardado é `(chave_da_org, seq)`, e cada `fetch` desce a árvore primária (`height` blocos). Esse custo duplo é esperado (SKS 14.4.1.2) e deve aparecer nos resultados.

### 5.3 Caso `BTreeFile` organizado por campo NOT UNIQUE

O arquivo primário NOT UNIQUE **repete** a chave nas folhas, com `seq` diferente, porque as folhas guardam os registros. A regra "sem repetição" vale para o **índice** secundário. Escreva isso no relatório para não parecer contradição.

---

## 6. `BTreeFile`: mapeamento para o protocolo `DataFile`

| Método | Implementação |
| :--- | :--- |
| `create/open(bp, name, schema, spec)` | `spec.org == BTREE`, `spec.key` obrigatório; meta no bno 1; raiz inicial = folha vazia (bno 2), `height = 1` |
| `scan()` | §4.2, em ordem de chave; devolve `((k, seq), rec)` |
| `search(k)` | §4.2 |
| `range(lo, hi, …)` | §4.2 |
| `fetch(ptr)` | desce pela CK exata (`height` pins) e decodifica; `KeyError` se não existir |
| `insert(rec)` | §4.3 com `k = rec[idx(spec.key)]`. Chave `None` → `ValueError` |
| `delete(ptr)` | §4.4 |
| `bulk_load(recs)` | §4.5 (o catálogo já entrega ordenado) |
| `reorganize()` | §4.6 |
| `encode_ptr((k, seq))` | `encode_key(f, k) + seq.to_bytes(8, "big")`; `ptr_size = ks + 8`; `decode_ptr` inverso |
| `height` | atributo público |
| `stats()` | `blocks = fm.n_blocks`, `records = n_entries`, `levels = height`, `leaf_blocks = n_leaves`, `overflow_blocks = 0`, `buckets = 0`, `meta_blocks = 1` e, a mais, `internal_blocks = n_internal` |

`BTreeIndex.stats()`: o mesmo, com `overflow_blocks = n_posting` e `records =` total de ponteiros.

---

## 7. Custos que seus testes devem confirmar (cache frio, `eng.cold()`)

| Operação | Leituras esperadas |
| :--- | :--- |
| `search(k)` UNIQUE | `height` |
| `search(k)` NOT UNIQUE com `m` registros | `height + ⌈m / f_folha⌉ − 1` (aprox.) |
| `range` com `L` folhas na faixa | `height − 1 + L` |
| `scan()` após bulk load | `n_leaves + height − 1`, com `seeks` ≈ `height` (folhas contíguas) |
| `BTreeIndex.search(k)` NOT UNIQUE na cadeia | `height + blocos da cadeia` |

Fórmulas de referência: SKS 14.3 (altura `⌈log_{⌈n/2⌉}(N)⌉` no pior caso) e E&N 17.3 (altura com ocupação média). Sua função auxiliar `expected_height(n_entries, fanout, fill)` em `btree_core.py` é usada pelo P4 e pelo P6 para comparar a altura estimada com a medida.

---

## 8. Testes obrigatórios

| Teste | Verifica |
| :--- | :--- |
| `test_insert_search_small` | 1..1000 em ordem aleatória, todas achadas; `check()` |
| `test_split_to_root` | bloco de 2 KB e chave CHAR(200) (fan-out pequeno): altura ≥ 4 com poucas chaves; `check()` |
| `test_nonunique_file` | 10 mil registros com 100 chaves distintas: `search(k)` devolve exatamente os 100 de cada |
| `test_range_bounds` | todas as combinações de `lo_inc`/`hi_inc` e `None`; oráculo `bisect` |
| `test_delete_merge` | inserir 5000 e apagar 4900 aleatórios; `check()` a cada 100; a altura diminui |
| `test_delete_redistribute` | caso montado em que o irmão tem sobra: nenhum bloco é liberado |
| `test_bulk_vs_incremental` | mesmos dados: bulk dá menos folhas e altura ≤ incremental; `scan` igual |
| `test_bulk_unsorted` | entrada fora de ordem → `ValueError` |
| `test_ptr_stable` | guarda `ptr`, força 50 splits, `fetch(ptr)` continua certo |
| `test_index_nonunique_no_repeat` | nenhuma chave repetida nas folhas do índice (percorrendo as folhas); posting longa vai para a cadeia |
| `test_index_delete_chain` | apagar ponteiros da cadeia libera blocos (`overflow_blocks` cai) |
| `test_costs` | §7, com `io.reads` do `Engine` |
| `test_no_pin_leak` | todos os testes terminam com `bp.pinned() == 0` (a fixture já checa) |
| `test_varchar_key` | chave VARCHAR com acentos: ordem igual à de `sorted()` sobre `encode_key` |

---

## 9. Armadilhas da IA (enunciado §7.1). Procure ativamente

- Árvore que **não mantém a ocupação mínima** (delete só remove e nunca funde): a altura não cai e as folhas ficam quase vazias.
- **Folhas não encadeadas**, ou `prev` da vizinha da direita esquecido no split.
- Split que **não propaga até a raiz**, ou raiz nova sem `height += 1`.
- Split interno que **copia** a chave do meio em vez de subi-la (fica duplicada) e split de folha que a **move** em vez de copiar.
- **Busca binária sobre uma lista Python montada com todos os registros** do arquivo em vez de busca no nó. O enunciado cita isso.
- Nó dividido por **número de entradas** em vez de bytes, quando as folhas têm registros de tamanho variável (uma metade pode não caber).
- `fm.free` em vez de `bp.free_block` na fusão.
- Pin do nó pai mantido durante toda a descida (vazamento de frames `INDEX` → `BufferFullError` em árvores altas).
- Bulk load que deixa a última folha com 1 registro (viola a ocupação mínima).
- Comparação de chaves decodificadas misturando tipos (`int` × `str`), ou chave composta comparando `seq` em little-endian.

Registre em `ai/P3/review.md`. Guarde uma versão **executável** da B+ ingênua da IA (ex.: sem fusão) em `ai/P3/raw/` para o teste ingênuo × refatorado (ocupação e altura depois de 90% de remoções).
