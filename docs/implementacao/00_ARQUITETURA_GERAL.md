# Arquitetura geral e contratos entre módulos — Grupo 3

> **Como usar este documento.** Cada integrante entrega à IA **este arquivo + o arquivo da sua parte** (`P1_...md` a `P6_...md`). Com os dois, a parte deve poder ser implementada por inteiro e integrar com as demais sem conversa extra.
>
> Este arquivo é a **referência comum** para tudo o que cruza a fronteira entre módulos: nomes de pacotes, assinaturas, tipos, unidades, contadores e convenções. Quando o arquivo da sua parte e este discordarem, **vale este**. Detalhes internos de cada módulo ficam no arquivo da parte.
>
> **Referência, não decisão fechada.** As escolhas daqui e dos guias P1–P6 são recomendações iniciais. Cada integrante debate as da sua parte com o agente ([`AGENTS.md`](../../AGENTS.md) §3) e pode mudar o que decidir. Quando a mudança afeta outra parte (assinatura, tipo, convenção), atualize este documento e avise quem consome a interface.

Enunciado completo: [`proposta.md`](../../proposta.md). A pergunta do trabalho é: **qual configuração do SBD processa com mais eficiência uma dada carga de trabalho sobre uma dada base?** A medida principal é o **número de blocos transferidos entre disco e buffer pool**.

---

## Em resumo (leia primeiro)

- **O que é este documento:** o mapa do sistema inteiro e as "tomadas" entre as partes. Ele diz *o que* cada parte oferece às outras (nomes de funções, parâmetros, tipos), e não *como* cada uma funciona por dentro.
- **O sistema tem 4 camadas**, e cada uma só conversa com a de baixo:
  - **P1** faz a base: um disco simulado e um cache de páginas (buffer pool) que **conta toda leitura e gravação**;
  - **P2 e P3** guardam as tabelas em arquivos: heap, hash e árvore B+, além dos índices e do catálogo;
  - **P4 e P5** fazem os algoritmos: seleção, ordenação e os três tipos de join;
  - **P6** monta o laboratório: gera os dados, roda tudo, mede, valida e escreve o relatório.
- **Regra de ouro:** só o buffer pool lê ou grava no disco. Se alguém ler por fora, a contagem de blocos (o resultado do trabalho) fica errada.
- **`contracts.py` (§4):** um arquivo de tipos que todos usam igual. O P1 cria no dia 1 e ninguém altera sem combinar com o grupo.
- **Memória limitada:** os algoritmos só podem usar M páginas (a área de trabalho). Não vale carregar uma tabela inteira numa lista do Python.
- **Onde está a sua interface:** a de P1 no §3, a de P2/P3 nos §5 e §6, a de P4/P5 no §7, e o que o P6 usa no §8. Datas e Git no §10. Log de IA e checklist de revisão no §11.
- **Escopo:** só o que o enunciado novo marca como obrigatório (§0). Os itens opcionais ficam de fora.
- **Como ler:** §0, §1 e §2 todo mundo. Depois, a seção da sua camada e a da camada logo abaixo, que é a que você vai usar.

---

## 0. Escopo (revisado com a versão nova do enunciado)

O enunciado foi atualizado e passou a marcar alguns itens como opcionais. **O grupo não fará os opcionais.**

| Item do enunciado | Situação | O que fazemos |
| :--- | :--- | :--- |
| Organizações primárias (§3) | obrigatório | 5: heap, B+ UNIQUE, B+ NOT UNIQUE, hash UNIQUE, hash NOT UNIQUE |
| Métodos de acesso (§4.2) | obrigatório | os da tabela do §5.3 |
| NATURAL JOIN mestre-detalhe, PK→FK (§5.1.1) | obrigatório | curso ⋈ disciplina, curso ⋈ aluno, aluno ⋈ inscricao (as 3 combinações de tamanho) |
| Equijoin em campo UNIQUE fora da organização (§5.1.2) | **opcional: não faremos** | — |
| Equijoin em campo NOT UNIQUE fora da organização (§5.1.3) | **opcional: não faremos** | — |
| Algoritmos de join | obrigatório | nested-loop em blocos, sort-merge, partition-hash |
| Configurações a–e do join | obrigatório | todas (§7.4) |
| Seleção por igualdade em campo UNIQUE (§5.2.1) | obrigatório | `aluno.dre` |
| Seleção por faixa em campo NOT UNIQUE (§5.2.2) | obrigatório | `aluno.cr` |
| Seleção por faixa em campo UNIQUE (§5.2.3) | **opcional: não faremos** | — |
| Seletividades | obrigatório | **1%** e 50% (era 0,1% na versão antiga) |
| Política de substituição (§2.2.4, §6.2) | mudou | deixou de ser um fator a comparar em toda a matriz: o grupo escolhe, **por tipo de operação**, a política mais adequada e justifica (§8.2) |
| Justificativa das escolhas (§7.2.2.a) | reforçado | o relatório justifica **todas** as opções, comparando com as preteridas quando pertinente |

Consequências práticas:

- não há subconjuntos (`aluno_p`, `detalhes_p`, `turma_p`): as tabelas reais já cobrem as três combinações de tamanho (§8.3);
- `turma` e `detalhes` continuam na base lógica (integridade e SQLite), mas não ganham variantes físicas;
- o índice B+ secundário UNIQUE deixa de ser necessário (§5.3);
- o sort-merge só precisa tratar chaves repetidas de um lado (§7.2).

---

## 1. Visão geral

```
┌─────────────────────────────────────────────────────────────────────┐
│ Camada 1 — Bancada de Teste (P6)                    bench/          │
│ gerador da base · matriz de experimentos · orquestrador · CSV ·     │
│ validação SQLite · custo estimado × medido · gráficos               │
└──────────────┬──────────────────────────────────────────────────────┘
               │ Engine, operadores, *_cost, configs, snapshot()
┌──────────────▼──────────────────────────────────────────────────────┐
│ Camada 2 — Mini Monitor de Execução                cbd/exec/        │
│ P4: seleções, ordenação externa, nested-loop (bloco/indexado),      │
│     adaptador de entradas (access.py)                               │
│ P5: sort-merge join, partition-hash join                            │
└──────────────┬──────────────────────────────────────────────────────┘
               │ Table, DataFile, Index, TempFile, hashing
┌──────────────▼──────────────────────────────────────────────────────┐
│ Camada 3 — Gerente de Armazenamento                cbd/records/     │
│ P2: formato de registro, slotted page, heap, hash estático          │  cbd/files/
│     (primário e índice), Table, Catálogo                            │
│ P3: árvore B+ (arquivo primário unique/não-unique, índice           │
│     secundário), split/merge, bulk load                             │
└──────────────┬──────────────────────────────────────────────────────┘
               │ BufferPool.pin/unpin/new_page, FileManager.create/...
┌──────────────▼──────────────────────────────────────────────────────┐
│ Camada 4 — Gerente de Arquivos + Gerente de Buffer Pool (P1)        │
│ disco simulado em blocos · contadores seq/aleatório · áreas do      │  cbd/storage/
│ buffer · LRU, Clock, MRU · pin/dirty · forced output · prefetch     │
└─────────────────────────────────────────────────────────────────────┘
```

**Regra de ouro (enunciado §1 e §7.1):** só o `BufferPool` chama `FileManager.read/write`. Qualquer outra camada que leia ou grave bytes de disco por fora subestima as contagens e invalida o experimento.

**Interfaces por pessoa:**

| De → Para | O que passa | Seção |
| :--- | :--- | :--- |
| P6 → todos | `Engine`, configs (`StorageConfig`, `BufferConfig`, `OrgSpec`, `IndexSpec`), operadores, funções `*_cost`, `snapshot()` | §4, §7, §8 |
| P4/P5 → P2/P3 | `Table`, `DataFile`, `Index`, `TempFile`, `HashFile` (buckets), `hash_value` | §5, §6 |
| P5 → P4 | `external_sort`, `access.py` (leitura das entradas do join) | §7 |
| P2/P3 → P1 | `BufferPool`, `Page`, `PageType`, `FileManager` (criar/alocar/liberar) | §3 |
| P3 → P2 | `RecordCodec`, `SlottedPage`, constantes de layout | §5.1 |
| P2 → P3 | o catálogo instancia `BTreeFile`/`BTreeIndex` | §5.3 |

---

## 2. Decisões globais (valem para todos)

| Item | Decisão |
| :--- | :--- |
| Linguagem | **Python 3.11+**. Só biblioteca padrão no núcleo (`cbd/`). `matplotlib` só no `bench/`. `pytest` para testes. |
| Página × bloco | **1 página = 1 bloco.** O tamanho do bloco é configurável: 2048, 4096 (padrão) ou 8192. |
| Endereço de bloco | `(fid, bno)`: `fid` é um `int` dado pelo `FileManager`; `bno` vai de **1** em diante. O bloco físico 0 de cada arquivo é o cabeçalho, e só o P1 mexe nele. |
| Registro em memória | `tuple` Python na ordem do esquema (§4). Nulo = `None`. Campo multivalorado = `tuple[str, ...]`. Data = `datetime.date`. |
| Ponteiro para registro | **Opaco** (`Ptr`). Heap e Hash usam `RID(block, slot)`. Arquivo B+ usa `(chave, seq)`. Quem guarda ponteiro (índices) usa `DataFile.encode_ptr/decode_ptr`, com tamanho fixo `ptr_size`. |
| Hashing | **Proibido usar `hash()` do Python** em qualquer coisa que vá para o disco ou que defina partição: o hash de `str` muda a cada execução. Use `cbd.records.hashing.hash_value(v, seed)` (§5.1). |
| Blocagem | **Não estendida (unspanned).** Um registro maior que o espaço útil do bloco gera `RecordTooLarge`. |
| Saída dos operadores | Iteradores Python (`yield`). O resultado **não** é gravado em disco, nem contado, como nos livros-texto. |
| Memória dos operadores | Limitada à área `WORK` do buffer (M páginas). Estrutura Python (lista, dict) que guarde registros só pode crescer até o que cabe nas páginas reservadas (§3.4). |
| Determinismo | Toda aleatoriedade usa `random.Random(seed)` com seed explícita. |

---

## 3. Camada 4 — interface fornecida por P1 (`cbd/storage/`)

### 3.1 FileManager (`cbd/storage/file_manager.py`)

```python
class FileManager:
    def __init__(self, cfg: StorageConfig): ...
    block_size: int

    def create(self, name: str) -> int            # cria <root_dir>/<name>.dat e devolve fid
    def open(self, name: str) -> int              # abre um arquivo existente e devolve fid
    def exists(self, name: str) -> bool
    def close(self, fid: int) -> None             # persiste o cabeçalho
    def delete(self, fid: int) -> None            # apaga o arquivo do SO
    def close_all(self) -> None

    def allocate(self, fid: int) -> int           # devolve um bno livre (reusa livres; senão estende o arquivo)
    def free(self, fid: int, bno: int) -> None
    def n_blocks(self, fid: int) -> int           # blocos de usuário em uso (sem cabeçalho e sem livres)

    # USO EXCLUSIVO DO BufferPool:
    def read(self, fid: int, bno: int) -> bytearray
    def write(self, fid: int, bno: int, data: bytes | bytearray) -> None

    io: IOCounters                                # global
    io_by_file: dict[int, IOCounters]             # por fid
    def name_of(self, fid: int) -> str
    def reset_stats(self) -> None
```

**Sequencial × aleatório (seek).** O disco simulado tem **uma cabeça global**. Um acesso a `(fid, bno)` é **sequencial** quando o acesso físico anterior, leitura ou gravação, foi a `(fid, bno-1)`. Caso contrário é **aleatório**, e conta 1 seek. A regra vale igual para leituras e gravações.

O cabeçalho do arquivo fica em memória enquanto o arquivo está aberto, e sua leitura e gravação **não** entram em `io`.

### 3.2 BufferPool (`cbd/storage/buffer_pool.py`)

```python
class Page:
    fid: int
    bno: int
    ptype: PageType
    data: bytearray          # len == block_size; altere IN-PLACE (nunca reatribua page.data)
    def mark_dirty(self) -> None

class BufferPool:
    def __init__(self, fm: FileManager, cfg: BufferConfig): ...
    fm: FileManager
    block_size: int
    cfg: BufferConfig

    def pin(self, fid: int, bno: int, ptype: PageType, seq: bool = False) -> Page
    def new_page(self, fid: int, ptype: PageType) -> Page    # fm.allocate + pin + zera + dirty; NÃO conta leitura
    def unpin(self, page: Page, dirty: bool = False, toss: bool = False) -> None
    def free_block(self, fid: int, bno: int) -> None         # descarta o frame (deve estar desafixado) SEM gravar + fm.free
                                                             # use em vez de fm.free (ex.: merge da B+), senão fica frame velho no buffer

    @contextmanager
    def page(self, fid: int, bno: int, ptype: PageType, seq: bool = False) -> Iterator[Page]
        # pin no __enter__, unpin no __exit__ (mesmo com exceção). Use SEMPRE que der.

    def force(self, page: Page) -> None          # forced output: grava já; conta forced_writes
    def flush_file(self, fid: int) -> None       # grava os sujos do arquivo
    def flush_all(self) -> None
    def drop_file(self, fid: int) -> None        # descarta os frames do arquivo SEM gravar (temporário que será apagado)
    def clear(self) -> None                      # flush_all + esvazia todos os frames (cache frio)

    def capacity(self, ptype: PageType) -> int   # nº de frames da área
    def pinned(self, ptype: PageType | None = None) -> int
    @contextmanager
    def reserve_work(self, n: int) -> Iterator[int]   # ver §3.4

    stats: BufferCounters                        # global
    stats_by_area: dict[PageType, BufferCounters]
    def reset_stats(self) -> None
```

Semântica que todos precisam saber:

- **Contagem.** Cada `pin` é 1 *request*. Se a página está no buffer é *hit*; se não está é *miss*, que gera 1 leitura física. `new_page` não conta request nem leitura.
- **Dirty.** Quem altera `page.data` **tem que** chamar `page.mark_dirty()` ou `unpin(page, dirty=True)`. Página suja expulsa é gravada (`dirty_evictions`).
- **Pin.** Todo `pin` precisa do `unpin` correspondente. Prefira `with bp.page(...) as p:`. Se não houver frame livre nem frame não fixado na área, `pin` levanta `BufferFullError`.
- **`seq=True`** indica leitura sequencial (scan). Com `cfg.prefetch = k > 0`, um *miss* com `seq=True` lê também os próximos `k` blocos do arquivo (contados como leituras sequenciais e em `prefetched`).
- **`toss=True`** no `unpin`: se `cfg.toss_immediate` estiver ligado, o frame vira a próxima vítima (toss-immediate). Scans devem passar `toss=True`; a configuração decide se tem efeito.
- **Áreas fixas por tipo de página.** Cada `PageType` tem frames próprios e sua própria instância da política. Uma página de `INDEX` nunca expulsa uma de `DATA`. Isso dá retenção preferencial de índice e catálogo sem precisar de prioridade dinâmica.

### 3.3 Que `PageType` usar

| Quem | Páginas | `PageType` |
| :--- | :--- | :--- |
| Catálogo (P2) | arquivo do catálogo | `CATALOG` |
| Heap/Hash primário (P2) | blocos de dados, overflow, FSM, meta | `DATA` |
| Arquivo B+ primário (P3) | **folhas** (contêm registros) | `DATA` |
| Arquivo B+ primário (P3) | nós internos e meta | `INDEX` |
| Índices secundários (P2 hash, P3 B+) | todos os blocos, incluindo listas de ponteiros | `INDEX` |
| Temporários dos operadores (P4/P5) | runs, partições | `WORK` |

### 3.4 Memória de trabalho dos operadores (M)

`M = bp.capacity(PageType.WORK)`. A "tabela menor" do enunciado (§5.1.2) é a que cabe inteira em `M − 2` páginas.

```python
with bp.reserve_work(k) as k:     # retira k frames da área WORK (BufferFullError se não houver)
    limite = k * bfr               # bfr = registros por bloco da tabela (TableStats.bfr)
    ...                            # lista/dict Python com no máximo `limite` registros
```

`reserve_work` tira os frames da área `WORK`. Enquanto a reserva durar, os temporários (runs e partições) só usam os frames restantes. Portanto **reserva + páginas fixadas de temporários ≤ M**. Exemplo do nested-loop em blocos: reserva de `M−2` para o bloco externo, 1 frame para ler o interno e 1 para a saída ou um temporário.

As leituras das tabelas-base por um operador usam as áreas `DATA` e `INDEX`, via `Table`/`DataFile`/`Index`, sempre **uma página fixada por vez** por cursor.

---

## 4. Tipos compartilhados — `cbd/contracts.py`

O P1 cria este arquivo **no dia 1, exatamente como abaixo**. Mudanças só por PR aprovado por todos (§10).

```python
"""Contratos compartilhados do Grupo 3. Não altere sem PR aprovado pelo grupo."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Iterator, NamedTuple, Protocol


# ------------------------------------------------------------------ erros
class CBDError(Exception): ...
class BufferFullError(CBDError): """Nenhum frame livre ou não fixado na área pedida."""
class NotApplicable(CBDError): """Caminho de acesso inexistente para a organização (ex.: faixa em hash)."""
class DuplicateKey(CBDError): ...
class RecordTooLarge(CBDError): ...


# ------------------------------------------------------------------ configuração
class Policy(str, Enum):
    LRU = "LRU"
    MRU = "MRU"
    CLOCK = "CLOCK"
    FIFO = "FIFO"


class PageType(str, Enum):
    CATALOG = "CATALOG"
    INDEX = "INDEX"
    DATA = "DATA"
    WORK = "WORK"


def _default_areas() -> dict[PageType, int]:
    return {PageType.CATALOG: 8, PageType.INDEX: 64, PageType.DATA: 128, PageType.WORK: 64}


@dataclass(frozen=True)
class StorageConfig:
    root_dir: str = "data/db"
    block_size: int = 4096                # 2048 | 4096 | 8192


@dataclass(frozen=True)
class BufferConfig:
    areas: dict[PageType, int] = field(default_factory=_default_areas)
    policy: Policy = Policy.LRU
    toss_immediate: bool = False          # unpin(toss=True) torna o frame a próxima vítima
    prefetch: int = 0                     # blocos lidos à frente num miss com seq=True; 0 = desligado


# ------------------------------------------------------------------ contadores
@dataclass
class IOCounters:
    seq_reads: int = 0
    rand_reads: int = 0
    seq_writes: int = 0
    rand_writes: int = 0

    @property
    def reads(self) -> int: return self.seq_reads + self.rand_reads
    @property
    def writes(self) -> int: return self.seq_writes + self.rand_writes
    @property
    def seeks(self) -> int: return self.rand_reads + self.rand_writes


@dataclass
class BufferCounters:
    requests: int = 0
    hits: int = 0
    misses: int = 0
    dirty_evictions: int = 0
    forced_writes: int = 0
    prefetched: int = 0

    @property
    def hit_rate(self) -> float: return self.hits / self.requests if self.requests else 0.0


# ------------------------------------------------------------------ esquema e registros
class FType(str, Enum):
    INT = "INT"          # int (64 bits com sinal)
    FLOAT = "FLOAT"      # float (64 bits)
    DATE = "DATE"        # datetime.date
    CHAR = "CHAR"        # str de tamanho fixo `size` bytes (utf-8, preenchido)
    VARCHAR = "VARCHAR"  # str até `size` bytes
    LIST = "LIST"        # multivalorado: tuple[str, ...], cada item até `size` bytes


@dataclass(frozen=True)
class Field:
    name: str
    ftype: FType
    size: int = 0
    nullable: bool = False
    unique: bool = False


@dataclass(frozen=True)
class Schema:
    name: str
    fields: tuple[Field, ...]

    def idx(self, name: str) -> int:
        for i, f in enumerate(self.fields):
            if f.name == name:
                return i
        raise KeyError(name)

    def get(self, name: str) -> Field:
        return self.fields[self.idx(name)]


Record = tuple
Ptr = Any                                 # opaco: RID (heap/hash) ou (chave, seq) (B+)


class RID(NamedTuple):
    block: int
    slot: int


# ------------------------------------------------------------------ organização e índices
class Org(str, Enum):
    HEAP = "HEAP"
    BTREE = "BTREE"
    HASH = "HASH"


@dataclass(frozen=True)
class OrgSpec:
    org: Org
    key: str | None = None                # campo que dita a organização (BTREE/HASH)
    unique: bool = False                  # a chave da organização é UNIQUE?
    params: dict = field(default_factory=dict)   # ver §8


class IndexKind(str, Enum):
    HASH = "HASH"
    BTREE = "BTREE"


@dataclass(frozen=True)
class IndexSpec:
    table: str
    column: str
    kind: IndexKind
    unique: bool
    params: dict = field(default_factory=dict)


@dataclass
class TableStats:
    n_records: int                        # n_r
    n_blocks: int                         # b_r (dados + overflow)
    bfr: float                            # f_r = registros por bloco (média)
    record_size: float                    # bytes, média
    distinct: dict[str, int]              # V(A, r)
    min: dict[str, Any]
    max: dict[str, Any]


# ------------------------------------------------------------------ protocolos da camada 3
class DataFile(Protocol):
    """Organização primária (Heap, Hash ou B+). Mapeamento para o enunciado §3:
    Open=create/open, Close=close, Scan/FindAll=scan, Find+FindNext=search/range
    (o iterador é o cursor; Reset = pedir novo iterador), Read=fetch,
    FindOrdered=range (só BTREE), Reorganize=reorganize."""
    schema: Schema
    spec: OrgSpec
    ptr_size: int                                         # bytes de encode_ptr (fixo)

    def scan(self) -> Iterator[tuple[Ptr, Record]]: ...  # todos; BTREE = ordem da chave
    def search(self, key: Any) -> Iterator[tuple[Ptr, Record]]: ...   # igualdade na chave da org.; HEAP -> NotApplicable
    def range(self, lo: Any, hi: Any, lo_inc: bool = True, hi_inc: bool = True
              ) -> Iterator[tuple[Ptr, Record]]: ...     # só BTREE; lo/hi=None = aberto; HEAP/HASH -> NotApplicable
    def fetch(self, ptr: Ptr) -> Record: ...
    def insert(self, rec: Record) -> Ptr: ...
    def delete(self, ptr: Ptr) -> None: ...
    def bulk_load(self, recs: Iterable[Record]) -> None: ...   # arquivo vazio; BTREE exige recs ordenados pela chave
    def reorganize(self) -> None: ...                    # pode mudar Ptrs: Table reconstrói os índices depois
    def encode_ptr(self, ptr: Ptr) -> bytes: ...
    def decode_ptr(self, b: bytes) -> Ptr: ...
    def stats(self) -> dict[str, int]: ...               # ver §6.3
    def close(self) -> None: ...


class Index(Protocol):
    spec: IndexSpec

    def search(self, key: Any) -> Iterator[Ptr]: ...
    def range(self, lo: Any, hi: Any, lo_inc: bool = True, hi_inc: bool = True
              ) -> Iterator[tuple[Any, Ptr]]: ...        # só BTREE, em ordem de chave; HASH -> NotApplicable
    def insert(self, key: Any, ptr: Ptr) -> None: ...
    def delete(self, key: Any, ptr: Ptr) -> None: ...
    def bulk_load(self, pairs: Iterable[tuple[Any, Ptr]]) -> None: ...   # pares ordenados por chave
    def stats(self) -> dict[str, int]: ...
    def close(self) -> None: ...


class TempFile(Protocol):
    """Arquivo temporário (runs, partições) em páginas WORK."""
    def append(self, rec: Record) -> None: ...   # mantém 1 página de saída fixada
    def finish(self) -> None: ...                # desafixa a página de saída (chamar antes de scan)
    def scan(self) -> Iterator[Record]: ...      # 1 página fixada por vez, seq=True, toss=True
    def n_blocks(self) -> int: ...
    def n_records(self) -> int: ...
    def drop(self) -> None: ...                  # bp.drop_file + fm.delete; idempotente


class TableLike(Protocol):
    name: str
    schema: Schema
    data: DataFile
    indexes: dict[str, Index]                    # coluna -> índice secundário
    def insert(self, rec: Record) -> Ptr: ...
    def delete(self, ptr: Ptr) -> None: ...
    def stats(self) -> TableStats: ...


# ------------------------------------------------------------------ execução
class Path(str, Enum):
    SCAN = "SCAN"              # campo sem método de acesso (config. a)
    PRIMARY = "PRIMARY"        # campo é a chave da organização primária (config. b/c)
    SECONDARY = "SECONDARY"    # campo tem índice secundário (config. d/e)


@dataclass
class JoinSide:
    table: TableLike
    column: str
    path: Path


@dataclass
class Cost:
    reads: float
    writes: float = 0.0
    seeks: float | None = None
    formula: str = ""          # texto da fórmula usada, com os valores, para o relatório

    @property
    def transfers(self) -> float: return self.reads + self.writes
```

---

## 5. Camada 3 — interface fornecida por P2 e P3

### 5.1 Utilitários de registro — P2 (`cbd/records/`), usados por P3 e P5

```python
# cbd/records/codec.py
class RecordCodec:
    def __init__(self, schema: Schema): ...
    max_size: int                                      # maior registro possível em bytes
    def encode(self, rec: Record) -> bytes             # RecordTooLarge se não couber num bloco
    def decode(self, b: bytes | memoryview) -> Record

def key_size(f: Field) -> int                          # tamanho FIXO da chave codificada
def encode_key(f: Field, v: Any) -> bytes              # tamanho key_size(f); VARCHAR preenchido até size
                                                       # PRESERVA ORDEM: a < b  <=>  encode_key(a) < encode_key(b)
def decode_key(f: Field, b: bytes | memoryview) -> Any

# cbd/records/slotted_page.py
class SlottedPage:
    """Opera sobre page.data (bytearray) in-place. Não faz pin/unpin nem dirty."""
    HEADER: int                                        # bytes do cabeçalho padrão
    USER: int = 16                                     # bytes livres no cabeçalho para quem usa (ex.: P3: is_leaf, next, prev)
    def __init__(self, buf: bytearray): ...
    def init(self) -> None                             # formata página vazia
    def n_slots(self) -> int
    def get(self, slot: int) -> memoryview | None      # None = slot apagado
    def insert(self, data: bytes) -> int | None        # devolve o slot ou None se não couber (compacta antes de desistir)
    def insert_at(self, pos: int, data: bytes) -> bool # insere deslocando o diretório de slots (ordem lógica; para folhas B+)
    def delete(self, slot: int) -> None                # marca o slot como livre
    def remove_at(self, pos: int) -> None              # remove a entrada do diretório (desloca; para folhas B+)
    def free_space(self) -> int
    def compact(self) -> None
    def user_header(self) -> memoryview                # USER bytes reservados
    @staticmethod
    def max_record(block_size: int) -> int             # maior registro que cabe num bloco vazio

# cbd/records/hashing.py
def hash_value(v: Any, seed: int = 0) -> int           # 64 bits, determinístico entre execuções; int, float, str, date, tuple
```

### 5.2 Arquivos e tabela — P2 (`cbd/files/`)

Construtores padronizados. **Todos os `DataFile` e `Index` seguem esta forma**, e o catálogo depende disso:

```python
# DataFile:  Cls.create(bp, name, schema, spec) / Cls.open(bp, name, schema, spec)
HeapFile.create(bp, name, schema, spec, ptype=PageType.DATA)      # cbd/files/heap.py      (P2)
HashFile.create(bp, name, schema, spec)                           # cbd/files/hash_file.py (P2)
BTreeFile.create(bp, name, schema, spec)                          # cbd/files/btree_file.py (P3)

# Index:     Cls.create(bp, name, spec, key_field: Field, data: DataFile) / Cls.open(...mesmos args)
HashIndex.create(bp, name, spec, key_field, data)                 # cbd/files/hash_index.py  (P2)
BTreeIndex.create(bp, name, spec, key_field, data)                # cbd/files/btree_index.py (P3)
#   o índice usa data.ptr_size / data.encode_ptr / data.decode_ptr para guardar ponteiros
```

Extras do `HashFile` (usados por P5 no hash join, §7.3):

```python
class HashFile(DataFile):
    n_buckets: int
    seed: int
    def bucket_of(self, key: Any) -> int          # hash_value(key, self.seed) % n_buckets
    def scan_bucket(self, i: int) -> Iterator[tuple[Ptr, Record]]   # bucket i + cadeia de overflow
```

Extras do `BTreeFile` (usados por P4/P5 e P6):

```python
class BTreeFile(DataFile):
    height: int                                   # nº de níveis, incluindo as folhas

# cbd/files/btree_core.py (P3)
def expected_height(n_entries: int, fanout: int, fill: float) -> int   # altura estimada pela fórmula (para custo × medido)
```

`Table` (`cbd/files/table.py`, P2) implementa `TableLike`:

```python
class Table:
    name: str; schema: Schema; data: DataFile; indexes: dict[str, Index]
    def insert(self, rec) -> Ptr        # data.insert + insert em cada índice
    def delete(self, ptr) -> None       # lê o registro, remove dos índices, data.delete
    def stats(self) -> TableStats       # última análise (Catalog.analyze)
    def reorganize(self) -> None        # data.reorganize + reconstrói os índices
```

`Catalog` (`cbd/files/catalog.py`, P2):

```python
class Catalog:
    def __init__(self, bp: BufferPool): ...           # abre ou cria o arquivo "catalog" (páginas CATALOG)
    def create_table(self, schema: Schema, spec: OrgSpec) -> Table
    def load_table(self, name: str, recs: Iterable[Record], bulk: bool = True) -> None
        # bulk=True: BTREE ordena e usa bulk_load; HEAP/HASH inserem em lote. Depois roda analyze().
    def create_index(self, spec: IndexSpec) -> Index  # constrói a partir dos dados existentes (bulk_load)
    def drop_index(self, table: str, column: str) -> None
    def table(self, name: str) -> Table
    def tables(self) -> list[str]
    def drop_table(self, name: str) -> None
    def analyze(self, name: str) -> TableStats        # scan completo; grava no catálogo (forced output)
    def temp(self, schema: Schema) -> TempFile        # HeapFile em páginas WORK, nome único, fora do catálogo
    def close(self) -> None
```

### 5.3 Combinações de organização e índice a suportar (enunciado §4.2)

| Organização primária (`OrgSpec`) | Índices secundários (`IndexSpec`) |
| :--- | :--- |
| `HEAP` | campo UNIQUE → `HASH, unique=True` · campo NOT UNIQUE → `BTREE, unique=False` (sem repetir chave nas folhas) |
| `BTREE, unique=True` ou `False` | campo NOT UNIQUE → `BTREE, unique=False` |
| `HASH, unique=True` ou `False` | campo NOT UNIQUE → `BTREE, unique=False` |

"Índice secundário clustering" (§4.2): interpretamos como índice B+ sobre campo NOT UNIQUE em que **todos os ponteiros de uma mesma chave ficam agrupados** num só bloco ou lista de ponteiros (a chave aparece uma vez na folha). **Confirmar com o professor** e registrar no relatório.

Pela tabela, todo índice B+ secundário é NOT UNIQUE e todo índice secundário UNIQUE é hash (só sobre heap). Por isso o `BTreeIndex` **não precisa** de modo UNIQUE. Se algum dia for preciso, o formato NOT UNIQUE com 1 ponteiro por chave já atende.

### 5.4 Ponteiros

| Organização | `Ptr` | `ptr_size` | Custo de `fetch(ptr)` |
| :--- | :--- | :--- | :--- |
| HEAP | `RID(block, slot)` | 8 | 1 bloco |
| HASH | `RID(block, slot)` (o bloco pode ser de overflow) | 8 | 1 bloco |
| BTREE | `(chave, seq)`, em que `seq` é um inteiro oculto e crescente por arquivo, que torna a chave única | `key_size + 8` | `height` blocos (desce a árvore) |

O ponteiro lógico do B+ existe porque registros mudam de folha nos splits. Um RID quebraria os índices secundários.

---

## 6. Convenções para P4/P5 lerem a camada 3

### 6.1 Iteradores e pins

Todo iterador da camada 3 fixa **no máximo uma página por vez** e a desafixa antes de passar para a próxima. Quem consome não precisa liberar nada, mas **deve esgotar ou fechar o iterador** (`it.close()`). Ao abandonar um gerador no meio, o `finally` interno faz o unpin.

### 6.2 Custo esperado das chamadas (para os modelos de custo)

| Chamada | Blocos lidos |
| :--- | :--- |
| `HeapFile.scan()` | `b_r` |
| `HashFile.search(k)` | 1 + overflow do bucket |
| `HashFile.scan_bucket(i)` | blocos do bucket i |
| `BTreeFile.search(k)` | `height` + folhas extras com a mesma chave |
| `BTreeFile.range(lo, hi)` | `height` + folhas na faixa |
| `BTreeIndex.search(k)` (não-unique) | `height` + blocos da lista de ponteiros da chave |
| `HashIndex.search(k)` | 1 + overflow |
| `DataFile.fetch(ptr)` | ver §5.4 |

### 6.3 Chaves de `stats()` (para o indicador 6.1.1 do enunciado)

`DataFile.stats()` e `Index.stats()` devolvem um `dict[str, int]` com **no mínimo**:

| Chave | Significado |
| :--- | :--- |
| `blocks` | total de blocos de usuário do arquivo (= `fm.n_blocks`) |
| `records` | registros (dados) ou entradas (índice) |
| `overflow_blocks` | blocos de overflow (hash) ou de listas de ponteiros (índice B+ não-unique); 0 se não houver |
| `levels` | níveis (B+); 1 para heap/hash |
| `leaf_blocks` | folhas (B+); 0 nos demais |
| `buckets` | buckets (hash); 0 nos demais |
| `meta_blocks` | blocos de metadados (meta, FSM, diretório) |

---

## 7. Camada 2 — interface fornecida por P4 e P5 (`cbd/exec/`)

Todo operador recebe `eng` (o `Engine`, §8.1) como 1º argumento, para ter acesso a `eng.bp`, `eng.catalog` e `eng.M`. O operador devolve um **iterador**. O parâmetro opcional `info: dict` é preenchido pelo operador para a bancada registrar (`variant`, `passes`, `runs`, `partitions`, `outer`, `build` etc.).

Cada operador tem uma função irmã **`<nome>_cost(...)`** com os mesmos argumentos (sem `info`), que devolve `Cost`: o custo estimado pelas fórmulas dos livros-texto, calculado com `TableStats` reais e `M`. Quem implementa o algoritmo escreve a fórmula, porque é quem melhor entende o que o algoritmo realmente faz.

### 7.1 P4

```python
# cbd/exec/access.py — adaptador das entradas (P4 implementa; P5 usa)
def read_all(side: JoinSide) -> Iterator[Record]                   # ordem física (scan)
def read_ordered(side: JoinSide) -> Iterator[Record] | None
    # em ordem da coluna se o caminho dá ordem: PRIMARY em BTREE (data.range(None, None))
    # ou SECONDARY BTREE (index.range + fetch). Senão None.
def lookup(side: JoinSide, value) -> Iterator[Record] | None
    # igualdade via caminho: PRIMARY (data.search) ou SECONDARY (index.search + fetch). SCAN -> None.

# cbd/exec/selection.py
def select_eq(eng, table, column, value, path: Path, sort_ptrs=False, info=None) -> Iterator[Record]
def select_range(eng, table, column, lo, hi, lo_inc=True, hi_inc=True,
                 path: Path = Path.SCAN, sort_ptrs=False, info=None) -> Iterator[Record]
#   lo/hi = None -> aberto. Caminho inexistente (ex.: faixa em HASH primário) -> NotApplicable.
#   sort_ptrs (só SECONDARY): ordena os ponteiros por bloco antes do fetch (em lotes que cabem em M).
def select_eq_cost(eng, table, column, value, path) -> Cost
def select_range_cost(eng, table, column, lo, hi, lo_inc=True, hi_inc=True, path=Path.SCAN) -> Cost

# cbd/exec/external_sort.py
def external_sort(eng, source: Iterable[Record], schema: Schema, key: str,
                  n_blocks_hint: int | None = None, mem: int | None = None, info=None) -> Iterator[Record]
#   runs em TempFile (WORK); última fusão é streaming (não grava a saída).
#   mem = páginas WORK que este sort pode usar (padrão eng.M; mínimo 3). O sort-merge usa mem < M
#   porque as duas fusões finais rodam ao mesmo tempo.
def external_sort_cost(eng, n_blocks: int, mem: int | None = None) -> Cost

# cbd/exec/nested_loop.py
def nl_join(eng, r: JoinSide, s: JoinSide, outer: str = "auto", info=None) -> Iterator[Record]
#   outer: "auto" | "r" | "s". Usa lookup() no interno se ele tiver caminho de acesso
#   (variante "indexed"); senão nested-loop em blocos (variante "block").
def nl_join_cost(eng, r, s, outer="auto") -> Cost
```

### 7.2 P5

```python
# cbd/exec/sort_merge.py
def sort_merge_join(eng, r: JoinSide, s: JoinSide, info=None) -> Iterator[Record]
#   cada lado: read_ordered() se houver, senão external_sort(read_all())
def sort_merge_join_cost(eng, r, s) -> Cost

# cbd/exec/hash_join.py
def hash_join(eng, r: JoinSide, s: JoinSide, build: str = "auto", info=None) -> Iterator[Record]
#   GRACE com particionamento recursivo; aproveita HashFile.scan_bucket quando um lado é HASH primário na coluna
def hash_join_cost(eng, r, s, build="auto") -> Cost
```

**Escopo do sort-merge:** como só fazemos o join PK→FK (§0), um dos lados é UNIQUE na coluna do join. O sort-merge pode assumir isso (P5 §3.2) e levantar `NotApplicable` se nenhum lado for UNIQUE.

### 7.3 Formato da saída dos joins

Cada tupla de saída é **`r_rec + s_rec`** (concatenação na ordem dos argumentos `r`, `s`), **mesmo que o algoritmo inverta internamente** quem é externo ou construtor. No NATURAL JOIN a coluna repetida **não** é removida (a bancada compara o mesmo formato no SQLite). A ordem das tuplas é livre.

### 7.4 Mapa das configurações a–e (enunciado §5.1) para `JoinSide.path`

| Config | Lado r | Lado s | Como montar (`OrgSpec` / `IndexSpec`) |
| :--- | :--- | :--- | :--- |
| a | SCAN | SCAN | nenhum dos campos é chave de organização nem tem índice |
| b | PRIMARY | SCAN | r organizada (BTREE ou HASH) pela coluna do join |
| c | PRIMARY | PRIMARY | ambas organizadas pela coluna do join |
| d | PRIMARY | SECONDARY | r organizada pela coluna; s com índice secundário nela |
| e | SECONDARY | SECONDARY | ambas com índice secundário na coluna |

Nas configurações b e d, "um dos campos" pode ser o do mestre ou o do detalhe. A bancada roda **as duas orientações** (ex.: b com `curso` organizado por `codigo`, e b com `aluno` organizado por `curso`). Qual lado vale a pena organizar ou indexar é uma das recomendações que o relatório precisa dar. Nos dois casos `r` continua sendo o mestre, para a saída `r + s` manter o formato.

Para seleção (§5.2): a → `SCAN`, b → `PRIMARY`, c → `SECONDARY`.

---

## 8. Camada 1 — o que P6 usa de todos

### 8.1 Engine (`cbd/engine.py`, dono: P6)

```python
from cbd.contracts import BufferConfig, PageType, StorageConfig
from cbd.storage.file_manager import FileManager
from cbd.storage.buffer_pool import BufferPool
from cbd.files.catalog import Catalog


class Engine:
    def __init__(self, scfg: StorageConfig, bcfg: BufferConfig):
        self.scfg, self.bcfg = scfg, bcfg
        self.fm = FileManager(scfg)
        self.bp = BufferPool(self.fm, bcfg)
        self.catalog = Catalog(self.bp)

    @property
    def M(self) -> int:
        return self.bp.capacity(PageType.WORK)

    def cold(self) -> None:
        """Cache frio e contadores zerados: chamar antes de cada medição."""
        self.bp.clear()
        self.fm.reset_stats()
        self.bp.reset_stats()

    def snapshot(self) -> dict[str, float]:
        io, b = self.fm.io, self.bp.stats
        out = {
            "io.reads": io.reads, "io.writes": io.writes, "io.seeks": io.seeks,
            "io.seq_reads": io.seq_reads, "io.rand_reads": io.rand_reads,
            "io.seq_writes": io.seq_writes, "io.rand_writes": io.rand_writes,
            "bp.requests": b.requests, "bp.hits": b.hits, "bp.misses": b.misses,
            "bp.hit_rate": b.hit_rate, "bp.dirty_evictions": b.dirty_evictions,
            "bp.forced_writes": b.forced_writes, "bp.prefetched": b.prefetched,
        }
        for area, c in self.bp.stats_by_area.items():
            out[f"bp.{area.value}.requests"] = c.requests
            out[f"bp.{area.value}.hits"] = c.hits
            out[f"bp.{area.value}.misses"] = c.misses
        return out

    def close(self) -> None:
        self.catalog.close()
        self.bp.flush_all()
        self.fm.close_all()
```

Uma base gerada é reaberta com outra `BufferConfig` (ex.: trocar a política) sem ser gerada de novo. Já mudar o `block_size` exige gerar a base de novo, num `root_dir` separado por tamanho de bloco (ex.: `data/db_4k`).

### 8.2 Parâmetros que a bancada pode variar (o que "pode mudar" em cada módulo)

| Dono | Onde | Parâmetro | Valores |
| :--- | :--- | :--- | :--- |
| P1 | `StorageConfig.block_size` | tamanho do bloco | 2048, **4096**, 8192 |
| P1 | `BufferConfig.areas` | frames por área | ex.: `CATALOG 8, INDEX 64, DATA 128, WORK 64` |
| P1 | `BufferConfig.policy` | política de substituição | LRU, MRU, CLOCK, FIFO. **Escolhida por tipo de operação** num experimento curto (P6 §4.4), não variada em toda a matriz |
| P1 | `BufferConfig.toss_immediate` | toss-immediate | `False`/`True` |
| P1 | `BufferConfig.prefetch` | leitura antecipada | 0, 4, 8 |
| P2 | `OrgSpec.params["buckets"]` (HASH) | nº de buckets | padrão: `ceil(n_r / (bfr · 0.8))`; em NOT UNIQUE, no máximo `V(A)` (P2 §6.1) |
| P2 | `OrgSpec.params["load"]` (HASH) | fator de carga alvo | 0.8 |
| P2 | `OrgSpec.params["expected_records"]` (HASH) | base para calcular N quando `buckets` não é dado | n_r da tabela |
| P2 | `OrgSpec.params["distinct"]` (HASH NOT UNIQUE) | `V(A)` da chave, para limitar N | a bancada passa |
| P2 | `OrgSpec.params["seed"]` (HASH) | seed da função de hash | 0; igual em duas tabelas para o *bucket-join* do P5 |
| P2 | `OrgSpec.params["compact_at"]` (HEAP) | fração apagada que dispara reorganize | 0.2 |
| P2 | `IndexSpec.params["buckets"]` (índice HASH) | nº de buckets | idem |
| P3 | `OrgSpec.params["fill"]` / `IndexSpec.params["fill"]` | ocupação no bulk load | 0.7 a 1.0 (padrão 0.9) |
| P3 | `Catalog.load_table(bulk=...)` | bulk load × inserção registro a registro | `True`/`False` |
| P4 | `nl_join(outer=...)` | quem é o externo | auto, r, s |
| P4 | `select_*(path=...)` | caminho de acesso | SCAN, PRIMARY, SECONDARY |
| P5 | `hash_join(build=...)` | lado construtor | auto, r, s |
| P6 | volumes, seletividades, pares de tabelas | — | §8.3 |

### 8.3 Base de dados (esquema físico comum)

O P6 gera; todos usam os mesmos nomes de tabela e coluna nos testes.

```python
from cbd.contracts import Field, FType as T, Schema

CURSO = Schema("curso", (
    Field("codigo", T.INT, unique=True), Field("nome", T.VARCHAR, 60),
    Field("escola", T.VARCHAR, 40), Field("carga_horaria", T.INT),
    Field("coordenador", T.INT), Field("email", T.CHAR, 40, unique=True),
    Field("endereco", T.VARCHAR, 80), Field("telefones", T.LIST, 15, nullable=True),
))
DISCIPLINA = Schema("disciplina", (
    Field("codigo", T.INT, unique=True), Field("curso", T.INT),
    Field("nome", T.VARCHAR, 60), Field("ementa", T.VARCHAR, 200, nullable=True),
    Field("carga_horaria", T.INT), Field("prereqs", T.LIST, 8, nullable=True),
))
TURMA = Schema("turma", (
    Field("numero", T.INT, unique=True), Field("disciplina", T.INT),
    Field("semestre", T.CHAR, 6), Field("horario", T.CHAR, 12),
    Field("sala", T.CHAR, 8), Field("professor", T.INT),
))
ALUNO = Schema("aluno", (
    Field("dre", T.INT, unique=True), Field("cpf", T.CHAR, 11, unique=True),
    Field("nome", T.VARCHAR, 60), Field("nascimento", T.DATE), Field("sexo", T.CHAR, 1),
    Field("endereco", T.VARCHAR, 80), Field("emails", T.LIST, 40, nullable=True),
    Field("curso", T.INT), Field("cr", T.FLOAT), Field("orientador", T.INT, nullable=True),
))
INSCRICAO = Schema("inscricao", (
    Field("dre", T.INT), Field("turma", T.INT),
    Field("frequencia", T.FLOAT), Field("media", T.FLOAT, nullable=True),
))
DETALHES = Schema("detalhes", (
    Field("cpf", T.CHAR, 11, unique=True), Field("detalhes", T.LIST, 60, nullable=True),
))
```

Estes esquemas ficam em `bench/schema.py` (P6). Os demais importam de lá nos testes de integração. Nos testes unitários, use esquemas pequenos próprios.

Joins de referência (só o NATURAL mestre-detalhe, §0):

| Combinação de tamanho | Junção (mestre ⋈ detalhe) | Coluna | Blocos com B = 4096 (estimativa de `results/base_stats.csv`) |
| :--- | :--- | :--- | :--- |
| menor ⋈ menor | curso ⋈ disciplina | `codigo` = `curso` | 2 ⋈ 27 |
| menor ⋈ maior | curso ⋈ aluno | `codigo` = `curso` | 2 ⋈ 1 962 |
| maior ⋈ maior | aluno ⋈ inscricao | `dre` | 1 962 ⋈ 4 588 |

"Menor" = cabe em `M − 2 = 62` páginas. As tabelas reais já cobrem as três combinações, então não há subconjuntos. `turma` e `detalhes` ficam na base lógica, mas não entram nos experimentos. Seleções: igualdade em `aluno.dre` e faixa em `aluno.cr`.

---

## 9. Layout do repositório

```
cbd/
  __init__.py
  contracts.py              # §4 (P1 cria; todos mantêm)
  engine.py                 # §8.1 (P6)
  storage/                  # P1: file_manager.py, buffer_pool.py, policies.py
  records/                  # P2: codec.py, slotted_page.py, hashing.py
  files/                    # P2: heap.py, hash_file.py, hash_index.py, table.py, catalog.py
                            # P3: btree_node.py, btree_core.py, btree_file.py, btree_index.py
  exec/                     # P4: access.py, selection.py, external_sort.py, nested_loop.py
                            # P5: sort_merge.py, hash_join.py
bench/                      # P6: schema.py, generator.py, sqlite_ref.py, matrix.py, run.py,
                            #     compare_cost.py, plots.py
tests/                      # test_<módulo>.py de cada um; tests/integration/ (P6)
results/                    # CSVs e gráficos (versionados)
ai/P1 … ai/P6/              # log de prompts e código bruto da IA (§11)
POST_MORTEM.md              # consolidado por P1
```

Nenhum módulo importa de uma camada **acima** da sua. P1 não importa nada de `files/` ou `exec/`, e P2/P3 não importam de `exec/`.

---

## 10. Como o grupo trabalha junto

### 10.1 Ordem de entrega (prazo final: 22/11/2026)

| Data | Marco | Quem desbloqueia quem |
| :--- | :--- | :--- |
| **05/11** | `contracts.py` + `FileManager` + `BufferPool` mínimo (só LRU, sem áreas/prefetch) no `main` | P1 → P2, P3 |
| **09/11** | codec, slotted page, `HeapFile`, `Catalog`, `catalog.temp()` · B+ com insert/search/range/scan · gerador + SQLite | P2/P3 → P4, P5 |
| **12/11** | P1 completo (políticas, áreas, toss, prefetch, `reserve_work`) · `external_sort` | P4 → P5 |
| **14/11** | Hash primário e índice (P2) · B+ delete/merge, bulk load, índice secundário (P3) · seleções e NLJ (P4) · SMJ e hash join sobre heap (P5) | — |
| **16/11** | Integração completa: matriz rodando ponta a ponta em escala pequena | P6 |
| **18/11** | Rodada final em escala cheia · testes de estresse (P1 coordena) | — |
| **20/11** | Relatório técnico (P6) + relatório de IA + `POST_MORTEM.md` (P1) | — |
| **21/11** | Slides | todos |

Antes de a camada de baixo ficar pronta, **escreva contra a interface** e teste com dublês simples. Exemplo: um `FakeTable` com uma lista Python para testar a lógica de um join em casos pequenos. Mas **toda medição é feita com a pilha real**.

### 10.2 Git

- Uma branch por pessoa e funcionalidade (`p3/btree-split`), com PR para o `main`. Precisa de 1 revisão de quem consome a interface (P2 revisa P3 e vice-versa; P4/P5 revisam P2/P3; P6 revisa P4/P5; qualquer um revisa P1).
- Mudança em `contracts.py` ou neste documento: pode acontecer sempre que o grupo decidir. Faça em PR próprio, avise e peça revisão de quem é afetado.
- `pytest -q` precisa passar no `main`.

---

## 11. Uso de IA (enunciado §7) — obrigatório para todos

Cada integrante mantém em `ai/Px/`:

| Arquivo | Conteúdo |
| :--- | :--- |
| `prompts.md` | cada prompt relevante, com data e ferramenta/modelo |
| `raw/` | o código **bruto** devolvido pela IA, **antes** de qualquer edição (um arquivo por resposta) |
| `review.md` | defeitos achados no código da IA: arquivo e linha, categoria (correção, eficiência, complexidade, vazamento de pin, acesso fora da faixa…), como foi achado (teste, leitura, contagem que não bateu com a fórmula), correção aplicada |
| `stress.md` | testes de estresse e o resultado do código ingênuo × refatorado |

O P1 consolida tudo no `POST_MORTEM.md` da raiz.

**Checklist do enunciado (§7.1) que todos revisam no próprio código:**

1. Nenhum `fm.read`/`fm.write` fora de `cbd/storage/` (`grep -rn "fm\.\(read\|write\)" cbd/ | grep -v storage/`).
2. Todo `pin` tem `unpin`. Teste: `bp.pinned() == 0` ao fim de cada teste.
3. Toda alteração de página faz `mark_dirty()`.
4. Busca binária é sobre **blocos/nós**, não sobre uma lista de registros carregada inteira.
5. B+: ocupação mínima, folhas encadeadas, split propagado até a raiz.
6. Hash: busca e remoção percorrem a cadeia de overflow.
7. Joins e sort: nenhuma estrutura Python maior que a área reservada (§3.4).
8. Offsets e tamanhos de campos variáveis validados contra o tamanho do bloco.

O fixture compartilhado em `tests/conftest.py` (P1) verifica os itens 1 e 2 em todos os testes:

```python
@pytest.fixture
def eng(tmp_path):
    from cbd.engine import Engine
    from cbd.contracts import StorageConfig, BufferConfig
    e = Engine(StorageConfig(root_dir=str(tmp_path)), BufferConfig())
    yield e
    assert e.bp.pinned() == 0, "pin sem unpin"
    e.close()
```

---

## 12. Glossário de símbolos usados nas fórmulas de custo

| Símbolo | Significado | De onde vem |
| :--- | :--- | :--- |
| `n_r` | registros de r | `TableStats.n_records` |
| `b_r` | blocos de r | `TableStats.n_blocks` |
| `f_r` | registros por bloco | `TableStats.bfr` |
| `V(A,r)` | valores distintos de A em r | `TableStats.distinct[A]` |
| `M` | páginas da área WORK | `eng.M` |
| `h_i` | altura do índice ou árvore B+ i | `stats()["levels"]` |
| `sel` | fração de registros selecionados | parâmetro do experimento |

Referências: Silberschatz, Korth & Sudarshan, *Database System Concepts*, 7ª ed. (SKS), caps. 13–15; Elmasri & Navathe, *Fundamentals of Database Systems* (E&N), caps. 16–19. Os PDFs estão em `refs/`, fora do repositório.
