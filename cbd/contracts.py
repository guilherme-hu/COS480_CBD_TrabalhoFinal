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
