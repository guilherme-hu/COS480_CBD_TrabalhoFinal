"""Gerente de Arquivos (Camada 4, dono: P1 — Vivian).

Implementa: FileManager (cbd/contracts.py). Usa: apenas cbd/contracts.py e biblioteca padrão.
Decisões: D-P1-01 (bloco de 4 KB), D-P1-02 (alocação contígua por arquivo),
D-P1-03 (cabeçalho descritor no Bloco 0 fora de io), D-P1-04 (pilha LIFO de blocos livres),
D-P1-05 (contadores físicos seq/rand e cabeça global _head). Ver docs/decisoes/P1.md.
Invariantes: bno >= 1 para blocos de usuário; Bloco 0 exclusivo do descritor;
apenas o BufferPool chama read() e write().
Custo esperado: read/write = 1 transferência de bloco (+1 seek se _head != (fid, bno - 1));
allocate/free = 0 I/Os (operam sobre o descritor em memória).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import struct
from typing import BinaryIO, Callable

from cbd.contracts import IOCounters, StorageConfig

MAGIC = b"CBD1"
# D-P1-03: cabeçalho fixo de 32 bytes no início do Bloco físico 0 (ver docs/decisoes/P1.md)
# Campos: magic(4s), block_size(I), high_water(I), n_free_header(I), ext_bno(I), 12 bytes reservados
HEADER_STRUCT = struct.Struct("<4sIIII12x")
# Cabeçalho de bloco de extensão da free-list (next_ext: I, count: I)
EXT_STRUCT = struct.Struct("<II")


@dataclass
class _FileDesc:
    fid: int
    name: str
    path: Path
    file: BinaryIO
    high_water: int = 0
    free: list[int] = field(default_factory=list)
    free_set: set[int] = field(default_factory=set)


class FileManager:
    """Gerente de Arquivos da Camada 4 (uso de read/write exclusivo pelo BufferPool)."""

    def __init__(self, cfg: StorageConfig):
        # D-P1-01: tamanho de bloco configurável (padrão 4096 B), múltiplo de 4 bytes
        if cfg.block_size < 64 or cfg.block_size % 4 != 0:
            raise ValueError(f"block_size inválido: {cfg.block_size}")
        self.cfg = cfg
        self.block_size: int = cfg.block_size
        self.root_dir = Path(cfg.root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

        # D-P1-04: capacidade da pilha no Bloco 0 ((B - 32) // 4 = 1016 em 4 KB) e por bloco de extensão
        self._max_free: int = (self.block_size - HEADER_STRUCT.size) // 4
        self._ext_cap: int = (self.block_size - EXT_STRUCT.size) // 4
        self._next_fid: int = 1
        self._desc: dict[int, _FileDesc] = {}
        self._by_name: dict[str, int] = {}
        self._names: dict[int, str] = {}
        self._closed_paths: dict[int, Path] = {}
        self._deleted_fids: set[int] = set()

        # Hooks opcionais registrados pelo BufferPool para manter coerência mesmo em chamadas diretas
        self._on_before_close: Callable[[int], None] | None = None
        self._on_before_free: Callable[[int, int], None] | None = None
        self._on_before_delete: Callable[[int], None] | None = None

        # D-P1-05: contadores globais, por arquivo e posição da cabeça global _head
        self.io: IOCounters = IOCounters()
        self.io_by_file: dict[int, IOCounters] = {}
        self._head: tuple[int, int] | None = None

    def register_hooks(
        self,
        *,
        on_before_close: Callable[[int], None] | None = None,
        on_before_free: Callable[[int, int], None] | None = None,
        on_before_delete: Callable[[int], None] | None = None,
    ) -> None:
        """Registra callbacks opcionais do BufferPool para manter coerência de cache na Camada 4 (0 I/Os)."""
        self._on_before_close = on_before_close
        self._on_before_free = on_before_free
        self._on_before_delete = on_before_delete

    def _path_for(self, name: str) -> Path:
        p = self.root_dir / f"{name}.dat"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def _write_header(self, d: _FileDesc) -> None:
        # D-P1-03, D-P1-04: persiste o descritor no Bloco físico 0 fora de fm.io.
        # Se houver mais de _max_free blocos livres, encadeia o excedente nos próprios blocos livres.
        if len(d.free) <= self._max_free:
            header_free = d.free
            ext_bno = 0
        else:
            split = len(d.free) - self._max_free
            rest = d.free[:split]
            header_free = d.free[split:]
            chunk_size = 1 + self._ext_cap
            chunks = [rest[i : i + chunk_size] for i in range(0, len(rest), chunk_size)]
            ext_bno = chunks[0][0]
            for idx, chunk in enumerate(chunks):
                cur_bno = chunk[0]
                items = chunk[1:]
                next_bno = chunks[idx + 1][0] if idx + 1 < len(chunks) else 0
                ebuf = bytearray(self.block_size)
                EXT_STRUCT.pack_into(ebuf, 0, next_bno, len(items))
                if items:
                    struct.pack_into(f"<{len(items)}I", ebuf, EXT_STRUCT.size, *items)
                d.file.seek(cur_bno * self.block_size)
                d.file.write(ebuf)

        buf = bytearray(self.block_size)
        n_free = len(header_free)
        HEADER_STRUCT.pack_into(
            buf, 0, MAGIC, self.block_size, d.high_water, n_free, ext_bno
        )
        if n_free:
            struct.pack_into(f"<{n_free}I", buf, HEADER_STRUCT.size, *header_free)
        d.file.seek(0)
        d.file.write(buf)
        d.file.flush()

    def _read_header(self, f: BinaryIO) -> tuple[int, list[int]]:
        # D-P1-03, D-P1-04: carrega o Bloco 0 (e eventual cadeia de extensão) na abertura (open).
        f.seek(0)
        buf = f.read(self.block_size)
        if len(buf) < HEADER_STRUCT.size:
            raise ValueError("Arquivo corrompido: cabeçalho menor que 32 bytes")
        magic, bsize, high_water, n_free, ext_bno = HEADER_STRUCT.unpack_from(buf, 0)
        if magic != MAGIC:
            raise ValueError(f"Magic inválido no cabeçalho: {magic!r}")
        if bsize != self.block_size:
            raise ValueError(
                f"block_size do arquivo ({bsize}) difere do FileManager ({self.block_size})"
            )
        if n_free > self._max_free or len(buf) < HEADER_STRUCT.size + 4 * n_free:
            raise ValueError(f"Cabeçalho corrompido: n_free={n_free}")
        header_free = (
            list(struct.unpack_from(f"<{n_free}I", buf, HEADER_STRUCT.size))
            if n_free
            else []
        )
        ext_items: list[int] = []
        cur = ext_bno
        while cur != 0:
            f.seek(cur * self.block_size)
            ebuf = f.read(self.block_size)
            if len(ebuf) < EXT_STRUCT.size:
                raise ValueError(f"Bloco de extensão livre {cur} corrompido")
            next_ext, count = EXT_STRUCT.unpack_from(ebuf, 0)
            if count > self._ext_cap or len(ebuf) < EXT_STRUCT.size + 4 * count:
                raise ValueError(f"Bloco de extensão livre {cur} com count inválido: {count}")
            ext_items.append(cur)
            if count:
                ext_items.extend(struct.unpack_from(f"<{count}I", ebuf, EXT_STRUCT.size))
            cur = next_ext
        return high_water, ext_items + header_free

    def create(self, name: str) -> int:
        """Cria (ou recria do zero) o arquivo lógico `name` (.dat) e retorna seu descritor `fid` (int >= 1).

        Grava o Bloco 0 de metadados sem incrementar `self.io` (0 I/Os de consulta).
        Se `name` já estiver aberto, fecha o descritor anterior antes de truncar.
        """
        if name in self._by_name:
            self.close(self._by_name[name])
        path = self._path_for(name)
        for old_fid, old_path in list(self._closed_paths.items()):
            if old_path == path:
                self._closed_paths.pop(old_fid, None)
        f = open(path, "w+b")
        fid = self._next_fid
        self._next_fid += 1
        d = _FileDesc(fid=fid, name=name, path=path, file=f)
        self._write_header(d)
        self._desc[fid] = d
        self._by_name[name] = fid
        self._names[fid] = name
        self.io_by_file.setdefault(fid, IOCounters())
        return fid

    def open(self, name: str) -> int:
        """Abre um arquivo lógico existente `name` (.dat), lê o Bloco 0 e retorna `fid` (0 I/Os de consulta).

        Se `name` já estiver aberto na sessão, devolve o mesmo `fid`.
        Levanta `FileNotFoundError` se o arquivo não existir ou `ValueError` se o cabeçalho for inválido.
        """
        if name in self._by_name:
            return self._by_name[name]
        path = self._path_for(name)
        if not path.exists():
            raise FileNotFoundError(f"Arquivo inexistente: {path}")
        f = open(path, "r+b")
        try:
            high_water, free_list = self._read_header(f)
        except Exception:
            f.close()
            raise
        for old_fid, old_path in list(self._closed_paths.items()):
            if old_path == path:
                self._closed_paths.pop(old_fid, None)
        fid = self._next_fid
        self._next_fid += 1
        d = _FileDesc(
            fid=fid,
            name=name,
            path=path,
            file=f,
            high_water=high_water,
            free=free_list,
            free_set=set(free_list),
        )
        self._desc[fid] = d
        self._by_name[name] = fid
        self._names[fid] = name
        self.io_by_file.setdefault(fid, IOCounters())
        return fid

    def exists(self, name: str) -> bool:
        """Retorna True sse o arquivo físico `<root_dir>/<name>.dat` existir em disco (0 I/Os)."""
        return self._path_for(name).exists()

    def is_open(self, fid: int) -> bool:
        """Retorna True sse o descritor `fid` estiver atualmente aberto no FileManager (0 I/Os)."""
        return fid in self._desc

    def close(self, fid: int) -> None:
        """Persiste o descritor no Bloco 0, ajusta o tamanho físico mínimo e fecha o arquivo `fid` (0 I/Os de consulta).

        Idempotente se `fid` já estiver fechado.
        """
        if fid not in self._desc:
            return
        if self._on_before_close is not None:
            self._on_before_close(fid)
        d = self._desc.pop(fid, None)
        if d is None:
            return
        self._by_name.pop(d.name, None)
        try:
            self._write_header(d)
            # Se blocos foram alocados via new_page/allocate mas o arquivo foi fechado sem flush,
            # garante que o tamanho físico em disco cubra (high_water + 1) blocos para não corromper na reabertura.
            expected_min_size = (d.high_water + 1) * self.block_size
            d.file.seek(0, 2)
            if d.file.tell() < expected_min_size:
                d.file.truncate(expected_min_size)
                d.file.flush()
        finally:
            d.file.close()
            self._closed_paths[fid] = d.path
            if self._head is not None and self._head[0] == fid:
                self._head = None

    def delete(self, fid: int) -> None:
        """Fecha o descritor `fid` (se aberto) e remove o arquivo físico `.dat` do disco (0 I/Os).

        Idempotente para `fid` já removido nesta sessão (suporta `TempFile.drop()`).
        Suporta também `delete(fid)` após `close(fid)`. Levanta `KeyError` se `fid` nunca existiu.
        """
        if fid in self._deleted_fids:
            return
        closed_path = self._closed_paths.pop(fid, None)
        if closed_path is not None:
            self._deleted_fids.add(fid)
            closed_path.unlink(missing_ok=True)
            return
        if fid not in self._desc:
            raise KeyError(f"fid inválido ou desconhecido: {fid}")
        if self._on_before_delete is not None:
            self._on_before_delete(fid)
        d = self._desc.pop(fid, None)
        if d is None:
            raise KeyError(f"fid inválido ou desconhecido: {fid}")
        self._deleted_fids.add(fid)
        self._by_name.pop(d.name, None)
        d.file.close()
        d.path.unlink(missing_ok=True)
        if self._head is not None and self._head[0] == fid:
            self._head = None

    def close_all(self) -> None:
        """Fecha todos os arquivos atualmente abertos persistindo seus cabeçalhos no Bloco 0 (0 I/Os de consulta)."""
        for fid in list(self._desc.keys()):
            self.close(fid)

    def _get_desc(self, fid: int) -> _FileDesc:
        d = self._desc.get(fid)
        if d is None:
            raise KeyError(f"fid inválido ou fechado: {fid}")
        return d

    def is_valid_block(self, fid: int, bno: int) -> bool:
        """Retorna True sse `bno` (em blocos, >= 1) foi alocado (`1 <= bno <= high_water`) e não está na pilha de livres (0 I/Os)."""
        d = self._get_desc(fid)
        return 1 <= bno <= d.high_water and bno not in d.free_set

    def allocate(self, fid: int) -> int:
        """Aloca 1 bloco em `fid` reutilizando o topo da pilha LIFO de livres ou incrementando `high_water` (0 I/Os).

        Retorna o número do bloco `bno >= 1`. Levanta `KeyError` se `fid` não estiver aberto.
        """
        # D-P1-02, D-P1-04: reaproveita o topo da pilha LIFO em O(1) antes de estender o fim
        # do arquivo (high_water += 1). Não grava no disco aqui porque new_page() já inicializa
        # o frame na memória com dirty=True.
        d = self._get_desc(fid)
        if d.free:
            bno = d.free.pop()
            d.free_set.discard(bno)
            return bno
        d.high_water += 1
        return d.high_water

    def free(self, fid: int, bno: int) -> None:
        """Devolve o bloco `bno` (`1 <= bno <= high_water`) à pilha de livres em RAM (0 I/Os).

        Levanta `KeyError` se `fid` estiver fechado ou `ValueError` se `bno` for inválido ou já estiver livre.
        """
        # D-P1-04: mantém todos os blocos livres em d.free / d.free_set sem perda;
        # se ultrapassar _max_free, o excedente é persistido em blocos de extensão no close().
        d = self._get_desc(fid)
        if bno < 1 or bno > d.high_water:
            raise ValueError(f"bno {bno} fora da faixa [1, {d.high_water}]")
        if bno in d.free_set:
            raise ValueError(f"bno {bno} já está livre")
        if self._on_before_free is not None:
            self._on_before_free(fid, bno)
        d.free.append(bno)
        d.free_set.add(bno)

    def n_blocks(self, fid: int) -> int:
        """Retorna a quantidade de blocos de usuário ocupados (`high_water - len(free)`) em `fid` (0 I/Os)."""
        d = self._get_desc(fid)
        return d.high_water - len(d.free)

    def _count(self, fid: int, bno: int, write: bool) -> None:
        # D-P1-05: modela um único braço mecânico de disco (cabeça global _head).
        # O acesso só é sequencial (sem seek) se o acesso imediatamente anterior foi (fid, bno - 1).
        # Qualquer salto de bloco ou troca de arquivo (fid diferente) conta como aleatório (+1 seek).
        seq = self._head == (fid, bno - 1)
        self._head = (fid, bno)
        f_io = self.io_by_file.setdefault(fid, IOCounters())
        if write:
            if seq:
                self.io.seq_writes += 1
                f_io.seq_writes += 1
            else:
                self.io.rand_writes += 1
                f_io.rand_writes += 1
        else:
            if seq:
                self.io.seq_reads += 1
                f_io.seq_reads += 1
            else:
                self.io.rand_reads += 1
                f_io.rand_reads += 1

    def read(self, fid: int, bno: int) -> bytearray:
        """Lê `block_size` bytes do bloco `bno` (offset `bno * block_size`) e contabiliza 1 leitura física.

        Custo: 1 `seq_read` (0 seeks) se `_head == (fid, bno - 1)`, ou 1 `rand_read` (+1 seek) caso contrário.
        Levanta `KeyError` se `fid` fechado ou `ValueError` se `bno` livre, fora de `[1, high_water]` ou além do EOF físico.
        """
        # D-P1-02: na alocação contígua, o bloco bno começa exatamente no byte bno * B
        # (pois o Bloco 0 ocupa os primeiros B bytes).
        d = self._get_desc(fid)
        if bno < 1 or bno > d.high_water or bno in d.free_set:
            raise ValueError(
                f"Leitura inválida: fid={fid}, bno={bno} (high_water={d.high_water})"
            )
        d.file.seek(bno * self.block_size)
        raw = d.file.read(self.block_size)
        # Nunca devolve zeros se ler além do EOF físico: levanta erro para não esconder bugs.
        if len(raw) != self.block_size:
            raise ValueError(
                f"Bloco {bno} do arquivo {d.name!r} além do fim físico ({len(raw)} bytes lidos)"
            )
        self._count(fid, bno, write=False)
        return bytearray(raw)

    def write(self, fid: int, bno: int, data: bytes | bytearray) -> None:
        """Grava `data` (`len == block_size` bytes) no bloco `bno` (offset `bno * block_size`) e contabiliza 1 escrita física.

        Custo: 1 `seq_write` (0 seeks) se `_head == (fid, bno - 1)`, ou 1 `rand_write` (+1 seek) caso contrário.
        Levanta `KeyError` se `fid` fechado ou `ValueError` se `bno` inválido/livre ou `len(data) != block_size`.
        """
        d = self._get_desc(fid)
        if bno < 1 or bno > d.high_water or bno in d.free_set:
            raise ValueError(
                f"Gravação inválida: fid={fid}, bno={bno} (high_water={d.high_water})"
            )
        if len(data) != self.block_size:
            raise ValueError(
                f"Tamanho de dados ({len(data)}) difere de block_size ({self.block_size})"
            )
        d.file.seek(bno * self.block_size)
        d.file.write(data)
        self._count(fid, bno, write=True)

    def name_of(self, fid: int) -> str:
        """Retorna o nome lógico (`str`) associado ao identificador `fid`, ou levanta `KeyError` (0 I/Os)."""
        if fid not in self._names:
            raise KeyError(f"fid desconhecido: {fid}")
        return self._names[fid]

    def reset_stats(self) -> None:
        """Zera os contadores `io` e `io_by_file` e reinicia a posição da cabeça `_head = None` (0 I/Os)."""
        # D-P1-05: zera contadores e reinicia _head = None para que o 1º acesso após
        # Engine.cold() sempre conte 1 seek inicial (rand_reads = 1), igual à fórmula do SKS.
        self.io = IOCounters()
        for fid in list(self.io_by_file.keys()):
            self.io_by_file[fid] = IOCounters()
        self._head = None
