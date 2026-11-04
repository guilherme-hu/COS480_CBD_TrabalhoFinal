# P2 — Registros, Slotted Page, Heap, Hash Estático, Tabela e Catálogo

> Leia junto com [`00_ARQUITETURA_GERAL.md`](00_ARQUITETURA_GERAL.md). As assinaturas públicas estão no documento geral (§4, §5, §6) e **não podem mudar** sem PR do grupo. Aqui está o *como* implementar.

**Peso:** ~17%. Você **consome** a camada 4 (P1: `BufferPool`, `FileManager`) e **fornece** a camada 3 para P4/P5/P6. O P3 usa seu `RecordCodec`, `SlottedPage` e `encode_key`, e o seu catálogo instancia os arquivos e índices B+ dele.

**Marcos:** até **09/11**: codec, slotted page, hashing, `HeapFile`, `TempHeap`, `Table` e `Catalog` (só com HEAP). Até **14/11**: `HashFile` e `HashIndex`.

---

## Em resumo (leia primeiro)

- **Você define como os dados viram bytes:** como um registro (uma linha da tabela) é codificado, inclusive campos vazios (nulos) e campos com lista de valores (ex.: vários e-mails).
- **Slotted page:** o formato de um bloco por dentro. Há uma "lista de posições" no começo e os registros no fim. Ela permite apagar e reaproveitar espaço sem mudar o endereço dos outros registros. O P3 também usa.
- **Heap:** a tabela sem ordem nenhuma. Insere onde houver espaço, controlado por um mapa de espaço livre.
- **Hash estático:** a tabela dividida em "baldes" (buckets) por uma função de hash. Buscar por igualdade custa ~1 bloco. Quando um balde enche, cria uma corrente de blocos extras (**overflow**), e toda busca e remoção **tem que** percorrer essa corrente.
- **Índice hash secundário:** a mesma ideia, mas guardando só "chave → onde está o registro".
- **Função de hash própria e determinística:** **nunca** use o `hash()` do Python, que muda a cada execução.
- **`Table`:** junta o arquivo com seus índices e mantém tudo sincronizado. **Catálogo:** lembra quais tabelas e índices existem e guarda as estatísticas (nº de registros, blocos etc.) usadas nas fórmulas de custo.
- **Arquivos temporários:** você também fornece os temporários que P4 e P5 usam para ordenar e particionar.
- **Entregue cedo** o codec e a slotted page, porque o P3 depende deles.

---

## 1. O que você entrega

| Arquivo | Conteúdo |
| :--- | :--- |
| `cbd/records/codec.py` | `RecordCodec`, `key_size`, `encode_key`, `decode_key` |
| `cbd/records/slotted_page.py` | `SlottedPage` |
| `cbd/records/hashing.py` | `hash_value` |
| `cbd/files/heap.py` | `HeapFile` (DataFile HEAP), `TempHeap` (TempFile) |
| `cbd/files/hash_file.py` | `HashFile` (DataFile HASH, unique e não-unique) |
| `cbd/files/hash_index.py` | `HashIndex` (índice secundário hash) |
| `cbd/files/table.py` | `Table` |
| `cbd/files/catalog.py` | `Catalog` |
| `tests/test_codec.py`, `test_slotted_page.py`, `test_hashing.py`, `test_heap.py`, `test_hash_file.py`, `test_hash_index.py`, `test_catalog.py` | testes |

Até o P1 entregar (05/11), use a fixture `bp` de `tests/conftest.py`. Até o P3 entregar, o catálogo importa `BTreeFile`/`BTreeIndex` de forma **preguiçosa** (dentro da função), para não quebrar quando o módulo ainda não existe.

---

## 2. Formato de registro (`codec.py`)

### 2.1 Decisões (enunciado §3.1). Coloque no relatório

| Decisão | Escolha |
| :--- | :--- |
| Campos fixos × variáveis | **parte fixa primeiro** (na ordem do esquema), depois um **diretório de campos variáveis** (offset, tamanho) e então os dados variáveis |
| Nulos | **null bitmap** no início (`ceil(nf/8)` bytes). Campo fixo nulo ocupa o espaço zerado |
| Opcionais | = `nullable=True` (bitmap) |
| Multivalorado (repeating field) | `FType.LIST`: `u16 count` + itens `(u16 len, bytes)`; máximo de **16 itens** (`MAX_LIST`) |
| Estruturado (ex.: endereço) | `VARCHAR` com subcampos separados por `\|` (`"rua\|num\|bairro\|cidade\|uf"`); o gerador monta, o codec não interpreta |
| Derivado (ex.: idade) | **não armazenado**; calculado de `nascimento` quando necessário |
| Blocagem | **não estendida (unspanned)**. `encode` levanta `RecordTooLarge` se `len > SlottedPage.max_record(B)` |

### 2.2 Layout em bytes

```
[null bitmap: ceil(nf/8)]
[parte fixa, campos fixos em ordem do esquema]
    INT   -> 8  bytes  '<q'
    FLOAT -> 8  bytes  '<d'
    DATE  -> 4  bytes  '<i'  (date.toordinal())
    CHAR  -> size bytes (utf-8, preenchido com b'\x00'; maior que size -> ValueError)
[diretório variável: para cada VARCHAR/LIST em ordem do esquema: u16 offset, u16 len]   (offset relativo ao início do registro)
[dados variáveis]
    VARCHAR -> utf-8 (len <= size)
    LIST    -> u16 count, depois count × (u16 len, utf-8)
```

- Pré-compile um `struct.Struct` para a parte fixa no `__init__` (um `pack`/`unpack` por registro). É o caminho quente do sistema inteiro.
- `decode` **valida**: offset + len ≤ len(b), count ≤ `MAX_LIST`, len de cada item dentro da faixa. Violação levanta `ValueError("registro corrompido")`. É o item "acesso fora da faixa" do §7.1 do enunciado.
- `decode` devolve `tuple`; LIST vira `tuple[str, ...]`; CHAR remove os `\x00` finais.
- `max_size`: soma com todos os variáveis no máximo (LIST com 16 itens de `size`).

### 2.3 Chaves de tamanho fixo (para índices e B+)

| FType | `key_size` | Codificação |
| :--- | :--- | :--- |
| INT | 8 | `(v + 2**63).to_bytes(8, "big")`, que preserva a ordem em bytes |
| FLOAT | 8 | `struct.pack('>d')` com ajuste de sinal (inverte todos os bits se negativo, senão inverte só o bit de sinal), também preservando a ordem |
| DATE | 4 | `(ordinal).to_bytes(4, "big")` |
| CHAR/VARCHAR | `size` | utf-8 preenchido com `\x00` à direita |
| LIST | — | não pode ser chave (`TypeError`) |

**A preservação de ordem é obrigatória:** o P3 compara chaves como `bytes` dentro dos nós da B+ (`a < b ⇔ encode_key(a) < encode_key(b)`). O `test_key_order` garante isso.

---

## 3. Slotted page (`slotted_page.py`)

Usada pelo heap, pelo hash (dados e índice), pelos temporários e pelas folhas do B+ (P3).

```
offset  tam  campo
0       2    n_slots     (u16)
2       2    free_end    (u16)  início da área de dados (cresce para baixo, a partir de B)
4       2    holes       (u16)  bytes livres fragmentados (registros apagados)
6       2    flags       (u16)  livre para o dono do arquivo
8       4    next        (u32)  próximo bloco (cadeia de overflow); 0 = nenhum
12      16   USER               reservado para quem usa (P3: is_leaf, prev, next_leaf...)
28           diretório de slots: n_slots × (u16 offset, u16 len); offset 0xFFFF = apagado
```

`HEADER = 28`, `USER = 16`. Acessórios: `next` como propriedade de leitura/escrita e `user_header()` devolvendo `memoryview(buf)[12:28]`.

| Método | Regra |
| :--- | :--- |
| `insert(data)` | reusa um slot apagado se houver; senão cria um slot novo (+4 bytes). Se o espaço contíguo não bastar mas `free_space()` sim, chama `compact()` e tenta de novo. Senão devolve `None` |
| `insert_at(pos, data)` | sempre cria um slot novo na posição `pos` (desloca o diretório). Para folhas ordenadas do B+ |
| `delete(slot)` | `offset = 0xFFFF`, `holes += len`. O slot continua existindo (RID estável) |
| `remove_at(pos)` | tira a entrada do diretório (desloca); `holes += len`. Muda os slots seguintes; só para B+ |
| `free_space()` | bytes de dados que ainda cabem num **novo** slot: `free_end − (HEADER + 4·n_slots) + holes − 4`, mínimo 0 |
| `compact()` | reempacota os registros vivos no fim do bloco, mantendo os números de slot; `holes = 0` |
| `max_record(B)` (staticmethod) | `B − HEADER − 4` |

Valide tudo: `slot < n_slots`, `offset + len ≤ B`, `free_end ≥ HEADER + 4·n_slots`. Violação levanta `AssertionError` com mensagem. `B ≤ 8192` cabe em u16.

`SlottedPage` **não** faz pin/unpin nem marca dirty. Quem chamou faz `page.mark_dirty()`.

---

## 4. Hashing (`hashing.py`)

```python
_MASK = (1 << 64) - 1
_FNV_OFFSET, _FNV_PRIME = 0xCBF29CE484222325, 0x100000001B3

def _bytes_of(v) -> bytes:
    # int -> b"i" + 8 bytes; float -> b"f" + '<d' (normalize -0.0 para 0.0);
    # str -> b"s" + utf-8; date -> b"d" + ordinal; None -> b"n"; tuple -> concat de cada item com b"\x1f"
    ...

def hash_value(v, seed: int = 0) -> int:
    h = (_FNV_OFFSET ^ ((seed * 0x9E3779B97F4A7C15) & _MASK)) & _MASK
    for byte in _bytes_of(v):
        h = ((h ^ byte) * _FNV_PRIME) & _MASK
    # finalizador splitmix64: espalha os bits baixos (usamos h % N)
    h ^= h >> 30; h = (h * 0xBF58476D1CE4E5B9) & _MASK
    h ^= h >> 27; h = (h * 0x94D049BB133111EB) & _MASK
    return h ^ (h >> 31)
```

- **Nunca** `hash()` do Python: para `str` ele muda a cada processo (PYTHONHASHSEED). Teste obrigatório: rodar `hash_value("abc")` em dois subprocessos dá o mesmo valor.
- Seeds diferentes dão funções independentes. O P5 usa `seed=1, 2, ...` nos níveis de particionamento, diferentes da seed do arquivo hash.
- **Escolha da função (enunciado §3.5.3):** meça a dispersão. Teste `test_dispersion` com qui-quadrado dos tamanhos de bucket para DRE (inteiros sequenciais), CPF (strings) e `orientador` (com repetição), e coloque os números no relatório. Para inteiros sequenciais, `k mod N` também dispersa perfeitamente; discuta por que o FNV + splitmix é mais robusto para domínios gerais.

---

## 5. Heap (`heap.py`)

### 5.1 Decisões (enunciado §3.2)

| Decisão | Escolha |
| :--- | :--- |
| Registros apagados | slot marcado como apagado na slotted page; RID estável |
| Gerência de espaço livre | **mapa de espaço livre (FSM)** com 1 byte por bloco de dados (`livre·255 // B`), em páginas `DATA` do próprio arquivo, lidas pelo buffer (contam I/O) |
| Política de inserção | 1) tenta o **último bloco** (`tail`); 2) se não couber **e** houver apagados (`n_deleted > 0`), consulta o FSM; 3) senão `new_page` |
| Compactação do espaço livre | **dentro do bloco**: automática no `insert` (§3). **Do arquivo**: `reorganize()` quando `n_deleted / (n_records + n_deleted) > compact_at` (padrão 0.2), disparada por `Table.delete` |

### 5.2 Layout do arquivo

```
bno 1         : meta (n_records, n_deleted, tail, fsm_bnos[...]) — PageType.DATA
bno 2..       : blocos de dados (slotted page) intercalados com páginas FSM conforme alocadas
```

Guarde em meta a **lista de bnos de dados** como faixas `[(ini, fim), ...]`. Assim o `scan` não lê páginas FSM. Como o heap só aloca com `new_page`, quase tudo é uma faixa só.

### 5.3 Operações

| Método | Implementação | Custo |
| :--- | :--- | :--- |
| `create(bp, name, schema, spec, ptype=DATA)` | `fm.create(name)`; meta | — |
| `insert(rec)` | `codec.encode` → §5.1 → `SlottedPage.insert` → `mark_dirty`; atualiza FSM só quando a categoria muda | ~1 bloco (tail no buffer) |
| `delete(rid)` | `SlottedPage.delete`, FSM, `n_deleted += 1` | 1 bloco |
| `fetch(rid)` | pin, `get(slot)`, decode, unpin. Slot apagado → `KeyError` | 1 bloco |
| `scan()` | percorre as faixas de dados em ordem, `pin(seq=True)`, devolve `(RID, rec)` dos slots vivos, `unpin(toss=True)` | `b_r` |
| `search` / `range` | `NotApplicable` | — |
| `bulk_load(recs)` | append direto, sem consultar o FSM; grava o FSM no fim | `b_r` gravações |
| `reorganize()` | cria `<name>__new`, `bulk_load` com os registros vivos, apaga o antigo, renomeia (`os.replace` + atualizar o fid) | `2·b_r` |
| `encode_ptr`/`decode_ptr` | `struct.pack('<II', block, slot)`; `ptr_size = 8` | — |

O `scan` é um **gerador** com `try/finally` garantindo o `unpin`. Ele lê os registros da página para uma lista local antes do `yield`? **Não**: decodifique slot a slot enquanto a página está fixada e faça o `yield` de cada registro. O `finally` faz o `unpin` se o consumidor abandonar o gerador.

### 5.4 `TempHeap` (TempFile para P4/P5)

Versão enxuta, sem meta e sem FSM. Os bnos ficam numa lista em memória, e o `PageType` é sempre `WORK`.

```python
class TempHeap:
    def __init__(self, bp, name, schema): ...       # fm.create(name)
    def append(self, rec):                          # mantém UMA página de saída fixada
        if self._out is None or SlottedPage(self._out.data).insert(b) is None:
            if self._out: self.bp.unpin(self._out, dirty=True)
            self._out = self.bp.new_page(self.fid, PageType.WORK); SlottedPage(self._out.data).init()
            self._bnos.append(self._out.bno); ...insert
    def finish(self): unpin da página de saída (dirty=True)
    def scan(self): para cada bno: pin(WORK, seq=True) / yield registros / unpin(toss=True)
    def drop(self): bp.drop_file(fid); fm.delete(fid)   # IDEMPOTENTE: 2ª chamada não faz nada
```

Nomes únicos: `f"__tmp_{os.getpid()}_{contador}"`. As páginas de temporário só são gravadas em disco quando expulsas do buffer. Esse comportamento é correto e deve ser documentado (quando tudo cabe em `WORK`, o custo medido fica abaixo da fórmula).

---

## 6. Hash estático (`hash_file.py`, `hash_index.py`)

### 6.1 Decisões (enunciado §3.5)

| Decisão | Escolha |
| :--- | :--- |
| Tipo | hashing externo **estático** |
| Nº de buckets N | `params["buckets"]` se dado; senão `ceil(params["expected_records"] / (bfr_est · load))` com `load = params.get("load", 0.8)` e `bfr_est = max_record(B) // (avg + 4)`, em que `avg` é o tamanho médio dos primeiros 100 registros codificados (`load_table` passa os registros) |
| Bucket | **1 bloco primário** (slotted page) + **cadeia de overflow** pelo campo `next` |
| Endereçamento | o bucket `i` está no bno `2 + i` (pré-alocados em sequência no `create`); sem diretório |
| Função | `hash_value(key, seed) % N`, com `seed = params.get("seed", 0)` salva no meta. O P5 precisa forçar N e seed iguais em duas tabelas para o *bucket-join* |
| Colisão (overflow) | encadeamento de blocos de overflow, alocados sob demanda; inserção no primeiro bloco da cadeia com espaço |
| Skew | NOT UNIQUE de baixa cardinalidade concentra registros num bucket (todos os iguais caem juntos, independentemente de N). Relate em `stats()` `max_chain` e `avg_chain`, e recomende no relatório usar hash NOT UNIQUE só em campos de cardinalidade média/alta (ex.: `inscricao.dre`, com ~10 por chave) |
| Fator de carga | `stats()["load_x1000"] = 1000·n_records / (N·bfr_est)`. `reorganize()` refaz o hash com N recalculado para o n atual |

### 6.2 `HashFile` (organização primária)

```
bno 1        : meta (N, seed, key_idx, unique, n_records, n_overflow, bfr_est)
bno 2..N+1   : buckets primários
bno > N+1    : overflow
```

| Método | Implementação |
| :--- | :--- |
| `search(key)` | pin no bucket, varre os slots e devolve os que batem; segue `next` **até o fim da cadeia** (se `unique`, para no 1º achado) |
| `insert(rec)` | se `unique`: `search` antes (`DuplicateKey`); percorre a cadeia buscando espaço; se nenhum bloco servir, `new_page` de overflow ligado ao **último** da cadeia |
| `delete(rid)` | slot apagado no bloco do RID (pode ser overflow). Não desfaz a cadeia |
| `fetch(rid)` | 1 bloco |
| `scan()` | bucket 0..N−1, cada um com sua cadeia (`seq=True` no primário) |
| `scan_bucket(i)` / `bucket_of(k)` | para o P5 (geral §5.2) |
| `range` | `NotApplicable` |
| `bulk_load(recs)` | agrupa os registros por bucket em memória **por lotes** de no máximo `capacity(DATA)·bfr_est` registros e insere bucket a bucket (tempo de carga, não medido) |

### 6.3 `HashIndex` (índice secundário; geral §5.3: campo UNIQUE de heap)

Mesma estrutura do `HashFile`, só que cada entrada é `encode_key(f, key) + data.encode_ptr(ptr)`, de tamanho fixo, guardada em slotted page com `PageType.INDEX`. `search(key)` devolve `Ptr`s. N vem de `spec.params["buckets"]` ou de `n_records` da tabela (o catálogo passa no `create_index`). `insert`/`delete(key, ptr)` percorrem a cadeia; `delete` procura a entrada **exata** (chave + ptr).

**Reaproveite:** o `HashFile` e o `HashIndex` podem compartilhar uma classe base `_StaticHash`, que cuida de buckets, cadeia e meta, e recebe a função de "o que guardar no slot". É uma abstração justificada: duas implementações reais.

---

## 7. `Table` (`table.py`)

```python
class Table:
    def __init__(self, catalog, name, schema, data, indexes): ...
    def insert(self, rec):
        ptr = self.data.insert(rec)
        for col, idx in self.indexes.items():
            k = rec[self.schema.idx(col)]
            if k is not None: idx.insert(k, ptr)
        return ptr
    def delete(self, ptr):
        rec = self.data.fetch(ptr)
        for col, idx in self.indexes.items():
            k = rec[self.schema.idx(col)]
            if k is not None: idx.delete(k, ptr)
        self.data.delete(ptr)
        if self._should_reorganize(): self.reorganize()
    def reorganize(self):
        self.data.reorganize()
        for col in list(self.indexes): self.catalog._rebuild_index(self.name, col)
    def stats(self) -> TableStats: return self.catalog._stats[self.name]
```

Avise no docstring: **`Ptr`s ficam inválidos depois de `reorganize()`**. Quem apaga em lote busca de novo a cada remoção.

---

## 8. Catálogo (`catalog.py`)

### 8.1 Persistência

Arquivo `catalog`, com páginas `PageType.CATALOG` em cadeia (`next`). O conteúdo é **JSON** (`utf-8`) espalhado pelas páginas: `u32 len` + bytes. A cada mudança (criar ou apagar tabela/índice, `analyze`), reescreva tudo e faça `bp.force(page)` em cada página. Isso é a **gravação forçada** do enunciado §2.2.7, e conta em `forced_writes`.

Conteúdo:

```json
{"tables": {"aluno": {
   "schema": {"name": "aluno", "fields": [["dre","INT",0,false,true], ...]},
   "spec": {"org": "HEAP", "key": null, "unique": false, "params": {}},
   "file": "aluno",
   "indexes": [{"column": "cpf", "kind": "HASH", "unique": true, "params": {}, "file": "aluno__cpf"}],
   "stats": {"n_records": 100000, "n_blocks": 4000, "bfr": 25.0, "record_size": 150.2,
             "distinct": {...}, "min": {...}, "max": {...}}
}}}
```

Datas em `min`/`max`: `{"$date": "2001-05-03"}`; converta na leitura.

### 8.2 Fábricas

```python
def _datafile_cls(org):
    if org is Org.HEAP: from .heap import HeapFile; return HeapFile
    if org is Org.HASH: from .hash_file import HashFile; return HashFile
    from .btree_file import BTreeFile; return BTreeFile          # P3 (import preguiçoso)

def _index_cls(kind):
    if kind is IndexKind.HASH: from .hash_index import HashIndex; return HashIndex
    from .btree_index import BTreeIndex; return BTreeIndex       # P3
```

Nomes dos arquivos: tabela `"<tabela>"`, índice `"<tabela>__<coluna>"`.

### 8.3 Operações

| Método | Faz |
| :--- | :--- |
| `create_table(schema, spec)` | valida `spec.key` no esquema; `cls.create(...)`; registra; persiste |
| `load_table(name, recs, bulk=True)` | BTREE + bulk: `sorted(recs, key=chave)` e então `data.bulk_load`. HEAP: `bulk_load`. HASH: `bulk_load`. Com `bulk=False`: `table.insert` um a um (é o que mede a "carga de manutenção"). No fim reconstrói os índices existentes e roda `analyze` |
| `create_index(spec)` | `cls.create(bp, f"{t}__{c}", spec, schema.get(c), table.data)`; preenche com `bulk_load(sorted((k, ptr) for ptr, rec in data.scan()))`, ignorando as chaves `None`; registra; persiste |
| `analyze(name)` | um `scan`: `n_records`, `distinct` (com `set` Python: é tempo de preparação, não medido), `min`/`max` dos INT/FLOAT/DATE/CHAR, `record_size` = média de `len(codec.encode(rec))`. `n_blocks` = `stats["leaf_blocks"]` se BTREE, senão `stats["blocks"] − stats["meta_blocks"]`; `bfr = n_records / n_blocks` |
| `temp(schema)` | `TempHeap(bp, nome_único, schema)`; não entra no catálogo |
| `close()` | `close()` de cada arquivo/índice aberto; persiste |

A ordenação em memória e os `set`s do `load_table`/`analyze` são **preparação da base**, fora da medição. Deixe isso escrito num comentário `# preparação: fora da medição` para a revisão não marcar como violação do §7.1.

---

## 9. Testes obrigatórios

| Teste | Verifica |
| :--- | :--- |
| `test_codec_roundtrip` | todos os tipos, nulos, LIST vazia/cheia, VARCHAR no limite, `RecordTooLarge` |
| `test_codec_corrupt` | bytes truncados ou offset fora → `ValueError` (não `IndexError` nem lixo) |
| `test_key_order` | para INT/FLOAT/DATE/CHAR: `a < b ⇔ encode_key(a) < encode_key(b)` em 10 mil pares aleatórios |
| `test_slotted_*` | insert até encher; delete + insert reusa o slot; `compact` mantém os slots; `insert_at`/`remove_at` mantêm a ordem; `free_space` exato |
| `test_hash_stable` | `hash_value` igual em dois subprocessos |
| `test_dispersion` | qui-quadrado (§4) |
| `test_heap_scan_cost` | heap de b blocos, `eng.cold()`, scan completo: `io.reads == b` e `io.seeks == 1` |
| `test_heap_delete_reuse` | apagar 30% e inserir de novo não cresce o arquivo (usa o FSM) |
| `test_heap_reorganize` | após `reorganize`, `blocks` diminui e os índices continuam corretos |
| `test_hash_unique` | `DuplicateKey`; `search` acha registro em overflow; `delete` em overflow |
| `test_hash_overflow_chain` | N=1, 10 blocos de registros: o `search` de cada chave acha todas |
| `test_hash_search_cost` | sem overflow, cold: `search(k)` = 1 leitura |
| `test_hash_index` | heap + `HashIndex` em CPF: busca = 1 bloco de índice + 1 de dados |
| `test_catalog_persist` | criar, fechar o `Engine`, reabrir: tabelas, índices e stats iguais |
| `test_temp` | `append` de 1000 registros, `finish`, `scan` devolve os mesmos; `drop` remove o arquivo; `bp.pinned() == 0` |

---

## 10. Armadilhas da IA (enunciado §7.1). Procure ativamente

- `search` do hash que olha só o bloco primário e **ignora a cadeia de overflow** (o enunciado cita esse caso). O mesmo vale para `delete`.
- `hash()` do Python em vez de `hash_value`.
- `insert` que altera a página e esquece `mark_dirty()`.
- `scan` que monta uma **lista com o arquivo todo** antes de devolver: estoura a memória e é exatamente o que o §7.1 proíbe.
- `free_space` sem os 4 bytes do slot novo: o insert "cabe" e corrompe o diretório.
- `decode` sem checar faixa: lê lixo do registro vizinho.
- Reuso de slot apagado que deixa o índice apontando para o registro novo. O `Table.delete` tem que tirar a entrada do índice **antes**.
- Catálogo que serializa com `pickle`: não é legível nem estável; use JSON.

Registre o que achar em `ai/P2/review.md` e guarde o código bruto em `ai/P2/raw/`. Guarde uma versão **executável** do hash ingênuo, sem overflow na busca, para o teste ingênuo × refatorado do P1.
