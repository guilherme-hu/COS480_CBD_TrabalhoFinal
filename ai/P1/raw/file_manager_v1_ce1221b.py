"""Gerente de Arquivos (Camada 4 — P1).

Simula o disco secundário por arquivos do sistema operacional (<root_dir>/<name>.dat)
divididos em blocos de tamanho fixo, com descritor e pilha de blocos livres no Bloco 0
e contadores de leituras/gravações físicas sequenciais e aleatórias (seeks).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import struct
from typing import BinaryIO

from cbd.contracts import IOCounters, StorageConfig

MAGIC = b"CBD1"
HEADER_STRUCT = struct.Struct("<4sIIII12x")  # 32 bytes fixos no início do Bloco 0


@dataclass
class _FileDesc:
    fid: int
    name: str
    path: Path
    file: BinaryIO
    high_water: int = 0
    free: list[int] = field(default_factory=list)
    free_set: set[int] = field(default_factory=set)
    lost_free: int = 0


class FileManager:
    """Gerente de Arquivos da Camada 4 (uso de read/write exclusivo pelo BufferPool)."""

    def __init__(self, cfg: StorageConfig):
        if cfg.block_size < 64 or cfg.block_size % 4 != 0:
            raise ValueError(f"block_size inválido: {cfg.block_size}")
        self.cfg = cfg
        self.block_size: int = cfg.block_size
        self.root_dir = Path(cfg.root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

        self._max_free: int = (self.block_size - HEADER_STRUCT.size) // 4
        self._next_fid: int = 1
        self._desc: dict[int, _FileDesc] = {}
        self._by_name: dict[str, int] = {}
        self._names: dict[int, str] = {}

        self.io: IOCounters = IOCounters()
        self.io_by_file: dict[int, IOCounters] = {}
        self._head: tuple[int, int] | None = None

    def _path_for(self, name: str) -> Path:
        p = self.root_dir / f"{name}.dat"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def _write_header(self, d: _FileDesc) -> None:
        buf = bytearray(self.block_size)
        n_free = len(d.free)
        HEADER_STRUCT.pack_into(
            buf, 0, MAGIC, self.block_size, d.high_water, n_free, d.lost_free
        )
        if n_free:
            struct.pack_into(f"<{n_free}I", buf, HEADER_STRUCT.size, *d.free)
        d.file.seek(0)
        d.file.write(buf)
        d.file.flush()

    def _read_header(self, f: BinaryIO) -> tuple[int, list[int], int]:
        f.seek(0)
        buf = f.read(self.block_size)
        if len(buf) < HEADER_STRUCT.size:
            raise ValueError("Arquivo corrompido: cabeçalho menor que 32 bytes")
        magic, bsize, high_water, n_free, lost_free = HEADER_STRUCT.unpack_from(buf, 0)
        if magic != MAGIC:
            raise ValueError(f"Magic inválido no cabeçalho: {magic!r}")
        if bsize != self.block_size:
            raise ValueError(
                f"block_size do arquivo ({bsize}) difere do FileManager ({self.block_size})"
            )
        if n_free > self._max_free or len(buf) < HEADER_STRUCT.size + 4 * n_free:
            raise ValueError(f"Cabeçalho corrompido: n_free={n_free}")
        free_list = (
            list(struct.unpack_from(f"<{n_free}I", buf, HEADER_STRUCT.size))
            if n_free
            else []
        )
        return high_water, free_list, lost_free

    def create(self, name: str) -> int:
        if name in self._by_name:
            self.close(self._by_name[name])
        path = self._path_for(name)
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
        if name in self._by_name:
            return self._by_name[name]
        path = self._path_for(name)
        if not path.exists():
            raise FileNotFoundError(f"Arquivo inexistente: {path}")
        f = open(path, "r+b")
        try:
            high_water, free_list, lost_free = self._read_header(f)
        except Exception:
            f.close()
            raise
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
            lost_free=lost_free,
        )
        self._desc[fid] = d
        self._by_name[name] = fid
        self._names[fid] = name
        self.io_by_file.setdefault(fid, IOCounters())
        return fid

    def exists(self, name: str) -> bool:
        return self._path_for(name).exists()

    def close(self, fid: int) -> None:
        d = self._desc.pop(fid, None)
        if d is None:
            return
        self._by_name.pop(d.name, None)
        try:
            self._write_header(d)
        finally:
            d.file.close()

    def delete(self, fid: int) -> None:
        d = self._desc.pop(fid, None)
        if d is None:
            raise KeyError(f"fid inválido ou já fechado: {fid}")
        self._by_name.pop(d.name, None)
        d.file.close()
        d.path.unlink(missing_ok=True)
        if self._head is not None and self._head[0] == fid:
            self._head = None

    def close_all(self) -> None:
        for fid in list(self._desc.keys()):
            self.close(fid)

    def _get_desc(self, fid: int) -> _FileDesc:
        d = self._desc.get(fid)
        if d is None:
            raise KeyError(f"fid inválido ou fechado: {fid}")
        return d

    def is_valid_block(self, fid: int, bno: int) -> bool:
        d = self._get_desc(fid)
        return 1 <= bno <= d.high_water and bno not in d.free_set

    def allocate(self, fid: int) -> int:
        d = self._get_desc(fid)
        if d.free:
            bno = d.free.pop()
            d.free_set.discard(bno)
            return bno
        d.high_water += 1
        return d.high_water

    def free(self, fid: int, bno: int) -> None:
        d = self._get_desc(fid)
        if bno < 1 or bno > d.high_water:
            raise ValueError(f"bno {bno} fora da faixa [1, {d.high_water}]")
        if bno in d.free_set:
            raise ValueError(f"bno {bno} já está livre")
        if len(d.free) < self._max_free:
            d.free.append(bno)
            d.free_set.add(bno)
        else:
            d.lost_free += 1

    def n_blocks(self, fid: int) -> int:
        d = self._get_desc(fid)
        return d.high_water - len(d.free) - d.lost_free

    def _count(self, fid: int, bno: int, write: bool) -> None:
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
        d = self._get_desc(fid)
        if bno < 1 or bno > d.high_water or bno in d.free_set:
            raise ValueError(
                f"Leitura inválida: fid={fid}, bno={bno} (high_water={d.high_water})"
            )
        d.file.seek(bno * self.block_size)
        raw = d.file.read(self.block_size)
        if len(raw) != self.block_size:
            raise ValueError(
                f"Bloco {bno} do arquivo {d.name!r} além do fim físico ({len(raw)} bytes lidos)"
            )
        self._count(fid, bno, write=False)
        return bytearray(raw)

    def write(self, fid: int, bno: int, data: bytes | bytearray) -> None:
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
        if fid not in self._names:
            raise KeyError(f"fid desconhecido: {fid}")
        return self._names[fid]

    def reset_stats(self) -> None:
        self.io = IOCounters()
        for fid in list(self.io_by_file.keys()):
            self.io_by_file[fid] = IOCounters()
        self._head = None
