# P2 — Registros, Slotted Page, Heap, Hash Estático, Tabela e Catálogo

> Leia junto com [`00_ARQUITETURA_GERAL.md`](00_ARQUITETURA_GERAL.md). As assinaturas públicas estão no documento geral (§4, §5, §6) e só mudam combinando com quem usa a interface. Aqui está uma proposta de *como* implementar. **As tabelas de decisão trazem recomendações, não decisões fechadas:** o agente debate cada uma com você antes de codar ([`AGENTS.md`](../../AGENTS.md) §3), e este arquivo pode ser atualizado com o que for decidido.

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
| Campos fixos × variáveis | **parte fixa primeiro** (na ordem do esquema), depois os campos variáveis em ordem do esquema, **cada um precedido do seu tamanho** (`u16`). D-P2-01 (E&N §16.4.2); o diretório (offset, tamanho) do SKS Fig. 13.5 foi preterido |
| Nulos | **sentinela por tipo, sem null bitmap** (D-P2-02, E&N §16.4.2). O NULL ocupa o espaço do campo; o bitmap do SKS Fig. 13.5 foi preterido |
| Comuns e opcionais | comum = obrigatório, gravado direto; opcional = `nullable=True`, NULL vira a sentinela do tipo. `None` em campo obrigatório levanta `ValueError` |
| Multivalorado (repeating field) | `FType.LIST` dentro do registro: depois do tamanho total (`u16`), itens `(u8 len, bytes)` até o fim do campo, **sem contagem**; máximo de **16 itens** (`MAX_LIST`). D-P2-03 |
| Estruturado (ex.: endereço) | `VARCHAR` com subcampos separados por `\|` (`"rua\|num\|bairro\|cidade\|uf"`); o gerador monta, o codec não interpreta. D-P2-04 |
| Derivado (ex.: idade) | **não armazenado**; calculado de `nascimento` quando necessário. O `cr` é armazenado: não é derivável na base e é coluna de acesso. D-P2-04 |
| Blocagem | **não estendida (unspanned)**. O codec não conhece o bloco: quem insere na página (heap, hash) levanta `RecordTooLarge` se `len(encode(r)) > SlottedPage.max_record(B)`, antes de tocar na página. D-P2-06 |

### 2.2 Layout em bytes

```
[parte fixa, campos fixos em ordem do esquema]            (sem null bitmap: D-P2-02)
    INT   -> 4  bytes  '<i'               NULL = -2**31   (E&N §16.4.1, p. 560: integer = 4 B)
    FLOAT -> 8  bytes  '<d'               NULL = NaN
    DATE  -> 4  bytes  '<i'  (date.toordinal())   NULL = 0
    CHAR  -> size bytes (utf-8, preenchido com b'\x00'; maior que size -> ValueError)   NULL = 0xFF no 1º byte
[campos variáveis, em ordem do esquema: u16 len, depois len bytes]   (D-P2-01)
    NULL    -> u16 len = 0xFFFF e nenhum byte depois
    VARCHAR -> utf-8 (len <= size)
    LIST    -> itens (u8 len, utf-8) até gastar o len do campo; sem contagem (D-P2-03)
```

- Pré-compile um `struct.Struct` para a parte fixa no `__init__` (um `pack`/`unpack` por registro). É o caminho quente do sistema inteiro. As sentinelas cabem no mesmo `struct`.
- `encode` **recusa** os valores reservados como dado (`-2**31` em INT, NaN em FLOAT) e `None` em campo obrigatório, com `ValueError`. `decode` testa o NaN com `math.isnan` (em Python, `nan == nan` é falso). LIST vazia (count 0) e VARCHAR vazio (len 0) não são NULL.
- `decode` **valida**: posição + len ≤ len(b) em cada prefixo, nenhum byte sobrando no fim, na LIST, cada item cabe no que resta do campo e há no máximo `MAX_LIST` itens. Violação levanta `ValueError("registro corrompido")`. É o item "acesso fora da faixa" do §7.1 do enunciado.
- `decode` devolve `tuple`; LIST vira `tuple[str, ...]`; CHAR remove os `\x00` finais.
- `max_size`: soma com todos os variáveis no máximo (LIST com 16 itens de `size`: `2 + 16 × (1 + size)`).

### 2.3 Chaves de tamanho fixo (para índices e B+)

Decisão D-P2-05 (`docs/decisoes/P2.md`, revista em 23/11): bytes que preservam a ordem, INT em 4 bytes e FLOAT em 8, o mesmo tamanho que têm no registro.

| FType | `key_size` | Codificação |
| :--- | :--- | :--- |
| INT | 4 | `(v + 2**31).to_bytes(4, "big")`, que preserva a ordem em bytes |
| FLOAT | 8 | `struct.pack('>d')` com ajuste de sinal (inverte todos os bits se negativo, senão inverte só o bit de sinal), também preservando a ordem. **−0,0 vira 0,0** antes de codificar (são iguais, mas os bytes diferem); **NaN levanta `ValueError`** (não tem ordem e é a sentinela de NULL) |
| DATE | 4 | `(ordinal).to_bytes(4, "big")` |
| CHAR/VARCHAR | `size` | utf-8 preenchido com `\x00` à direita |
| LIST | — | não pode ser chave (`TypeError`) |

- **Chave NULL é recusada:** `encode_key(f, None)` levanta `ValueError`, assim como as sentinelas de NULL da D-P2-02 (INT `−2**31`, FLOAT NaN, DATE ordinal 0). Nenhuma coluna-chave da bancada é nula (D-P2-05, opção N1).
- `decode_key(encode_key(v)) == v` e `len(encode_key(f, v)) == key_size(f)`.

**A preservação de ordem é obrigatória:** o P3 compara chaves como `bytes` dentro dos nós da B+ (`a < b ⇔ encode_key(a) < encode_key(b)`). O `test_key_order` garante isso.

---

## 3. Slotted page (`slotted_page.py`)

Usada pelo heap, pelo hash (dados e índice), pelos temporários e pelas folhas do B+ (P3).

```
offset  tam  campo
0       2    n_slots     (u16)
2       2    free_end    (u16)  início da área de dados (cresce para baixo, a partir de B)
4       2    holes       (u16)  bytes livres fragmentados (registros apagados)
6       4    next        (u32)  próximo bloco (overflow do hash; folha seguinte da B+); 0 = nenhum
10           diretório de slots: n_slots × (u16 offset, u16 len); offset 0xFFFF = apagado
```

`HEADER = 10` (D-P2-07 revista em 23/11: saíram o `flags` e os 16 B de `USER`, que não tinham uso; a folha da B+ encadeia pelo `next`). Acessório: `next` como propriedade de leitura/escrita.

| Método | Regra |
| :--- | :--- |
| `insert(data)` | reusa o menor slot apagado se houver (D-P2-08); senão cria um slot novo (+4 bytes). Se o espaço contíguo não bastar mas `free_space()` sim, chama `compact()` e tenta de novo. Senão devolve `None` |
| `insert_at(pos, data)` | sempre cria um slot novo na posição `pos` (desloca o diretório). Para folhas ordenadas do B+; recusa (`AssertionError`) página com slot apagado, onde `free_space()` não desconta o slot novo |
| `delete(slot)` | `offset = 0xFFFF`, `holes += len`. O slot continua existindo (RID estável) |
| `remove_at(pos)` | tira a entrada do diretório (desloca); `holes += len`. Muda os slots seguintes; só para B+ |
| `free_space()` | maior registro que `insert` aceita agora: `free_end − (HEADER + 4·n_slots) + holes − 4`, sem o `− 4` quando há slot apagado para reaproveitar; mínimo 0 (D-P2-08) |
| `compact()` | reempacota os registros vivos no fim do bloco, mantendo os números de slot; `holes = 0` |
| `max_record(B)` (staticmethod) | `B − HEADER − 4` |

Valide tudo: `slot < n_slots`, `offset + len ≤ B`, `free_end ≥ HEADER + 4·n_slots`. Violação levanta `AssertionError` com mensagem. `B ≤ 8192` cabe em u16.

`SlottedPage` **não** faz pin/unpin nem marca dirty. Quem chamou faz `page.mark_dirty()`.

Registro de 0 B é recusado (`AssertionError`), para `free_space() == 0` querer dizer "não cabe nada". `get` devolve uma view da página: vale só enquanto ela estiver fixada, e `insert`/`insert_at`/`compact` podem mover registros (`delete` e `remove_at` não, D-P2-08). `insert` e `insert_at` copiam `data` antes de compactar, então aceitam uma view da própria página.

---

## 4. Hashing (`hashing.py`)

```python
_MASK = (1 << 64) - 1
_FNV_OFFSET, _FNV_PRIME = 0xCBF29CE484222325, 0x100000001B3

def _bytes_of(v) -> bytes:
    # int -> b"i" + 8 bytes; float -> b"f" + '<d' (normalize -0.0 para 0.0);
    # str -> b"s" + tamanho u32 + utf-8; date -> b"d" + ordinal; None -> b"n";
    # tuple -> b"t" + nº de itens u32 + itens (cada item se delimita sozinho: codificação injetiva, #41)
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
- Seeds diferentes dão funções independentes. O P5 usa `seed = 100 + nível` nos níveis de particionamento (guia P5 §4), diferentes da seed do arquivo hash.
- **Escolha da função (enunciado §3.5.3): D-P2-14.** O `bucket_of` do hash usa `(k + seed) % N` para chave INT (chaves densas: n/N por bucket, sem overflow por azar) e o `hash_value` para os outros tipos. O `hash_value` continua nas partições do hash join (P5) e no checksum (P6), onde o mod não serve. Testes em `tests/test_hashing.py`.

---

## 5. Heap (`heap.py`)

### 5.1 Decisões (enunciado §3.2)

| Decisão | Escolha |
| :--- | :--- |
| Registros apagados | slot marcado como apagado na slotted page; RID estável |
| Gerência de espaço livre | **mapa de espaço livre (FSM)** com 1 byte por bloco de dados (`livre·256 // B`, arredondado para baixo; 256 faixas de 16 B em 4 KB, menos que o menor registro), em páginas `DATA` do próprio arquivo gravadas depois dos dados, lidas pelo buffer (contam I/O). D-P2-09 |
| Política de inserção | 1) tenta o **último bloco** (`tail`); 2) se não couber **e** houver apagados (`n_deleted > 0`), consulta o FSM; 3) senão `new_page` |
| Compactação do espaço livre | **dentro do bloco**: automática no `insert` (§3, D-P2-08). **Do arquivo**: só por comando, `reorganize()`, com `stats()["deleted"]` (slots apagados) para quem chama decidir (D-P2-11; sem `compact_at` e sem disparo no `Table.delete`) |

### 5.2 Layout do arquivo

```
bno 1         : meta ('HEP1', n_records, n_deleted, tail, faixas de blocos de dados, blocos do mapa) — PageType.DATA
bno 2         : 1ª página do mapa (criada com a meta, para os dados começarem contíguos)
bno 3..       : blocos de dados (slotted page); novas páginas do mapa só quando o arquivo passa de B blocos
```

O mapa tem 1 byte por bloco, indexado pelo número do bloco: a página k cobre os blocos k·B .. k·B + B − 1 (D-P2-09). A meta guarda as **faixas** de blocos de dados `[(ini, fim), ...]`, e o `scan` percorre só elas (não lê meta nem mapa). `n_deleted` = slots apagados que ainda existem. A meta é regravada a cada mudança (fica no buffer; só suja se os bytes mudam).

### 5.3 Operações

| Método | Implementação | Custo |
| :--- | :--- | :--- |
| `create(bp, name, schema, spec, ptype=DATA)` | `fm.create(name)`; meta | — |
| `insert(rec)` | `codec.encode` → `RecordTooLarge` se `len > max_record(B)` (D-P2-06) → §5.1 → `SlottedPage.insert` → `mark_dirty`; atualiza o mapa quando a cauda muda ou um bloco antigo recebe o registro; regrava a meta | 1 bloco de dados; 2 pins na cauda (cauda e meta, em geral no buffer) |
| `delete(rid)` | `SlottedPage.delete`, mapa, `n_deleted += 1`, meta | 1 bloco de dados; 3 pins (bloco, mapa e meta) |
| `fetch(rid)` | pin, `get(slot)`, decode, unpin. Slot apagado → `KeyError` | 1 bloco |
| `scan()` | percorre as faixas de dados em ordem pelo cursor `with bp.seq_scan(fid, ptype) as cur` / `cur.page(bno, toss=True)` e devolve `(RID, rec)` dos slots vivos. O `allow_prefetch(fid, ptype)` é chamado ao criar, abrir e reorganizar (fid novo), só em DATA e INDEX. Com `cfg.prefetch = k`, o cursor segura até 1 + k páginas; numa faixa seguida por uma página do mapa, os últimos k blocos são lidos sem cursor, para a pré-leitura não ler o mapa (D-P2-19) | `b_r` leituras com ou sem prefetch; misses ≈ ⌈`b_r` / (k + 1)⌉ |
| `search` / `range` | `NotApplicable` | — |
| `bulk_load(recs)` | append direto, sem consultar o FSM; grava o FSM no fim | `b_r` gravações |
| `reorganize()` | copia os vivos para um `TempHeap`, recria o arquivo com o mesmo nome e recarrega com `bulk_load` (o `FileManager` não tem rename, e renomear o `.dat` por fora furaria a camada 4). Não é atômico. Quando disparar: D-P2-11, em aberto | `b_r + 3·b'` |
| `encode_ptr`/`decode_ptr` | `struct.pack('<IH', block, slot)` (bloco u32, slot u16); `ptr_size = 6`. D-P2-10 | — |

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
    def scan(self): para cada bno: pin(WORK) / yield registros / unpin(toss=True)   # sem prefetch: o P1 o proíbe em WORK (D-P2-19)
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
| Função | INT: `(key + seed) % N`; outros tipos: `hash_value(key, seed) % N` (D-P2-14), com `seed = params.get("seed", 0)` salva no meta. O P5 precisa forçar N e seed iguais em duas tabelas para o *bucket-join* |
| Colisão (overflow) | encadeamento de blocos de overflow, alocados sob demanda; inserção no primeiro bloco da cadeia com espaço |
| Skew | NOT UNIQUE de baixa cardinalidade concentra registros num bucket (todos os iguais caem juntos, independentemente de N). **Caso real da nossa matriz:** `aluno` organizado por hash em `curso` (50 valores, ~1 000 alunos por curso). Com o N padrão (2 084 buckets, D-P2-16) sobrariam 2 034 buckets vazios que o `scan` lê à toa (~3 694 blocos, contra 1 682). Recomendação: em NOT UNIQUE, `N = min(N_padrão, V(A))`, com `V(A)` de `params["distinct"]` (a bancada passa). Relate em `stats()` `max_chain` e `avg_chain_x1000` |
| Fator de carga | `stats()["load_x1000"] = 1000·n_records / (N·bfr_est)`. `reorganize()` refaz o hash com N recalculado para o n atual, **só por comando** (D-P2-17: a reorganização automática não faz parte do escopo, como no heap, D-P2-11) |

### 6.2 `HashFile` (organização primária)

```
bno 1        : meta (N, seed, n_records, n_overflow, bfr_est, max_chain, key_idx, unique); open recusa outro spec
bno 2..N+1   : buckets primários
bno > N+1    : overflow
```

| Método | Implementação |
| :--- | :--- |
| `search(key)` | pin no bucket, varre os slots e devolve os que batem; segue `next` **até o fim da cadeia** (se `unique`, para no 1º achado) |
| `insert(rec)` | `RecordTooLarge` se `len > max_record(B)` (D-P2-06); se `unique`: `search` antes (`DuplicateKey`); percorre a cadeia buscando espaço; se nenhum bloco servir, `new_page` de overflow ligado ao **último** da cadeia |
| `delete(rid)` | slot apagado no bloco do RID (pode ser overflow). Não desfaz a cadeia |
| `fetch(rid)` | 1 bloco |
| `scan()` | em ordem física: blocos 2 até o último (primários, depois o overflow), 1 seek; a ordem por bucket é do `scan_bucket` (ver D-P2-17). É o único método do hash que lê pelo cursor `bp.seq_scan` (prefetch, D-P2-19); o arquivo termina no último bloco de dados, então a pré-leitura nunca lê bloco que não é pedido |
| `scan_bucket(i)` / `bucket_of(k)` | para o P5 (geral §5.2); `scan_bucket`, `search` e `fetch` sem cursor e sem prefetch (D-P2-19: o bloco seguinte ao primário é de outro bucket) |
| `range` | `NotApplicable` |
| `bulk_load(recs)` | ordena os registros por `bucket_of` em memória (D-P2-17; o N só existe depois de ver os registros) e enche cada cadeia em sequência: a cadeia de cada bucket fica contígua no disco (tempo de carga, não medido) |

### 6.3 `HashIndex` (índice secundário; geral §5.3: campo UNIQUE de heap)

D-P2-18: o índice é um `HashFile` com `PageType.INDEX` cujos registros são `(chave, bloco, slot)` (chave + 4 B + 4 B). É a mesma estrutura do hash primário, só que o bucket guarda o ponteiro em vez do registro (SKS §14.5, p. 659), e herda a cadeia, o UNIQUE, o N e o fator de carga (D-P2-14 a D-P2-17). `search(key)` devolve os `RID`s; `insert(key, ptr)`; `delete(key, ptr)` procura a entrada **exata** (chave + ptr). N vem de `spec.params["buckets"]`, ou de `expected_records`, ou do número de registros do arquivo de dados na criação. A entrada em bytes `encode_key + encode_ptr` (10 B) com uma classe base `_StaticHash` foi preterida: 2 B menor por entrada, mas exigia refatorar o `HashFile`.

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
        self.data.delete(ptr)              # D-P2-11: nada de reorganizar aqui
    def reorganize(self):
        self.data.reorganize()
        for col in list(self.indexes): self.catalog._rebuild_index(self.name, col)
    def stats(self) -> TableStats: return self.catalog._stats[self.name]
```

Avise no docstring: **`Ptr`s ficam inválidos depois de `reorganize()`**. Quem apaga em lote busca de novo a cada remoção.

---

## 8. Catálogo (`catalog.py`)

### 8.1 Persistência (D-P2-12)

O catálogo é guardado **como relações do próprio SGBD** (SKS §13.4, Fig. 13.12): quatro `HeapFile`s em páginas `PageType.CATALOG`, com esquemas fixos no código e nomes fixos (o bootstrap: SKS p. 604).

| Relação (arquivo) | Campos | Uma linha por |
| :--- | :--- | :--- |
| `catalog__relacoes` | `nome, org, chave, unique, arquivo, n_records, n_blocks, bfr, record_size` | tabela |
| `catalog__atributos` | `relacao, posicao, nome, tipo, tamanho, nulo, unique, distinct, min, max` | campo de cada tabela |
| `catalog__indices` | `relacao, coluna, tipo, unique, arquivo` | índice secundário |
| `catalog__params` | `dono, chave, valor` (FLOAT) | parâmetro de `OrgSpec`/`IndexSpec` (`buckets`, `seed`, `fill`…) |

- `min`/`max` ficam como texto e são convertidos com o `tipo` da própria linha (DATE em ISO).
- Ao abrir, um `scan` de cada relação monta o `dict` em memória; as operações só consultam o `dict`.
- Gravação (D-P2-13, enunciado §2.2.7): no fim de cada operação que muda o catálogo, `bp.flush_all` (os arquivos de dados e de índices; as páginas do catálogo estão limpas nesse momento) e `bp.force` das páginas sujas do catálogo, em ordem: detalhes (`atributos`, `params`, `indices`) primeiro e a linha de `relacoes` por último no `create_table`; no `drop`, a linha "ponteiro" primeiro; no `analyze`, a linha nova antes de apagar a velha. Ao abrir, linhas órfãs (sem a tabela ou o índice) são apagadas; de linhas repetidas vale a última lida.

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
| `load_table(name, recs, bulk=True)` | BTREE + bulk: `sorted(recs, key=chave)` e então `data.bulk_load`. HEAP: `bulk_load`. HASH: `bulk_load`, que ordena por bucket (D-P2-17). Com `bulk=False`: `table.insert` um a um (é o que mede a "carga de manutenção"). No fim reconstrói os índices existentes e roda `analyze` |
| `create_index(spec)` | `cls.create(bp, f"{t}__{c}", spec, schema.get(c), table.data)`; preenche com `bulk_load(sorted((k, ptr) for ptr, rec in data.scan()))`, ignorando as chaves `None`; registra; persiste |
| `analyze(name)` | um `scan`: `n_records`, `distinct` (com `set` Python: é tempo de preparação, não medido), `min`/`max` dos INT/FLOAT/DATE/CHAR, `record_size` = média de `len(codec.encode(rec))`. `n_blocks` = `stats["leaf_blocks"]` se BTREE, senão `stats["blocks"] − stats["meta_blocks"]`; `bfr = n_records / n_blocks` |
| `temp(schema)` | `TempHeap(bp, nome_único, schema)`; não entra no catálogo |
| `close()` | `close()` de cada arquivo/índice aberto; persiste |

A ordenação em memória e os `set`s do `load_table`/`analyze` são **preparação da base**, fora da medição. Deixe isso escrito num comentário `# preparação: fora da medição` para a revisão não marcar como violação do §7.1.

---

## 9. Testes obrigatórios

| Teste | Verifica |
| :--- | :--- |
| `test_codec_roundtrip` | todos os tipos, nulos, LIST vazia/cheia, VARCHAR no limite (o `RecordTooLarge` é testado no heap, D-P2-06) |
| `test_codec_corrupt` | bytes truncados ou offset fora → `ValueError` (não `IndexError` nem lixo) |
| `test_key_order` | para INT/FLOAT/DATE/CHAR: `a < b ⇔ encode_key(a) < encode_key(b)` em 10 mil pares aleatórios |
| `test_slotted_*` | insert até encher; delete + insert reusa o slot; `compact` mantém os slots; `insert_at`/`remove_at` mantêm a ordem; `free_space` exato |
| `test_hash_stable` | `hash_value` igual em dois subprocessos |
| `test_dispersion` | qui-quadrado (§4) |
| `test_heap_scan_cost` | heap de b blocos, `eng.cold()`, scan completo: `io.reads == b` e `io.seeks == 1` |
| `test_heap_record_too_large` | registro de `max_record(B)` bytes entra; com `max_record(B) + 1` levanta `RecordTooLarge` sem sujar página nenhuma (D-P2-06) |
| `test_heap_delete_reuse` | apagar 30% e inserir de novo não cresce o arquivo (usa o FSM) |
| `test_heap_reorganize` | após `reorganize`, `blocks` diminui e os índices continuam corretos |
| `test_hash_unique` | `DuplicateKey`; `search` acha registro em overflow; `delete` em overflow |
| `test_hash_overflow_chain` | N=1, 10 blocos de registros: o `search` de cada chave acha todas |
| `test_hash_search_cost` | sem overflow, cold: `search(k)` = 1 leitura |
| `test_hash_index` | heap + `HashIndex` em `dre`: busca = 1 bloco de índice + 1 de dados |
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
- Catálogo gravado fora do buffer (`json.dump`/`pickle` direto num arquivo): viola a regra 1 e não conta em `forced_writes`; o catálogo são as relações da D-P2-12, gravadas pelo `BufferPool`.

Registre o que achar em `ai/P2/review.md` e guarde o código bruto em `ai/P2/raw/`. Guarde uma versão **executável** do hash ingênuo, sem overflow na busca, para o teste ingênuo × refatorado do P1.
