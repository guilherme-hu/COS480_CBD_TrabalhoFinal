"""Testes unitários do Gerente de Arquivos (FileManager — P1)."""
from __future__ import annotations

import pytest

from cbd.contracts import StorageConfig
from cbd.storage.file_manager import FileManager


def test_roundtrip(tmp_path):
    """Gravar, fechar, reabrir e ler devolve os mesmos bytes; persiste high_water e livres."""
    cfg = StorageConfig(root_dir=str(tmp_path), block_size=4096)
    fm = FileManager(cfg)
    fid = fm.create("aluno")
    assert fm.exists("aluno")
    assert fm.name_of(fid) == "aluno"

    b1 = fm.allocate(fid)
    b2 = fm.allocate(fid)
    b3 = fm.allocate(fid)
    assert (b1, b2, b3) == (1, 2, 3)

    payload1 = bytearray(b"A" * 4096)
    payload3 = bytearray(b"C" * 4096)
    fm.write(fid, 1, payload1)
    fm.write(fid, 2, bytearray(4096))
    fm.write(fid, 3, payload3)
    fm.free(fid, 2)
    assert fm.n_blocks(fid) == 2

    fm.close(fid)

    # Reabre em nova instância para validar persistência do Bloco 0 (descritor)
    fm2 = FileManager(cfg)
    fid2 = fm2.open("aluno")
    assert fm2.n_blocks(fid2) == 2
    assert fm2.read(fid2, 1) == payload1
    assert fm2.read(fid2, 3) == payload3
    # O próximo allocate deve reutilizar o bloco 2 que estava na pilha de livres do Bloco 0
    assert fm2.allocate(fid2) == 2
    assert fm2.n_blocks(fid2) == 3
    fm2.close_all()


def test_free_reuse(tmp_path):
    """free(5) faz o próximo allocate devolver 5; n_blocks permanece consistente."""
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    fid = fm.create("turma")
    for expected in range(1, 7):
        assert fm.allocate(fid) == expected
    assert fm.n_blocks(fid) == 6

    fm.free(fid, 5)
    assert fm.n_blocks(fid) == 5
    assert fm.allocate(fid) == 5
    assert fm.n_blocks(fid) == 6
    assert fm.allocate(fid) == 7
    assert fm.n_blocks(fid) == 7
    fm.close_all()


def test_seq_vs_rand(tmp_path):
    """Ler 1..10 em ordem com cabeça zerada dá seq_reads == 9 e rand_reads == 1 (1 seek)."""
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    fid = fm.create("inscricao")
    for i in range(1, 11):
        bno = fm.allocate(fid)
        fm.write(fid, bno, bytes([i]) * 4096)

    fm.reset_stats()
    assert fm.io.reads == 0 and fm.io.writes == 0 and fm.io.seeks == 0

    for bno in range(1, 11):
        data = fm.read(fid, bno)
        assert data[0] == bno

    assert fm.io.rand_reads == 1
    assert fm.io.seq_reads == 9
    assert fm.io.reads == 10
    assert fm.io.seeks == 1
    assert fm.io_by_file[fid].rand_reads == 1
    assert fm.io_by_file[fid].seq_reads == 9
    fm.close_all()


def test_interleaved_files_count_seeks(tmp_path):
    """Alternar leitura entre dois arquivos deve contar seeks a cada troca de fid."""
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    f1 = fm.create("r")
    f2 = fm.create("s")
    for b in (1, 2):
        fm.allocate(f1)
        fm.write(f1, b, b"R" * 4096)
        fm.allocate(f2)
        fm.write(f2, b, b"S" * 4096)

    fm.reset_stats()
    fm.read(f1, 1)  # seek 1
    fm.read(f2, 1)  # seek 2 (trocou de arquivo)
    fm.read(f1, 2)  # seek 3 (voltou para f1, cabeça estava em f2)
    assert fm.io.rand_reads == 3
    assert fm.io.seq_reads == 0
    assert fm.io.seeks == 3
    fm.close_all()


def test_invalid_reads_and_deletes(tmp_path):
    """Leitura além do fim do arquivo ou de bloco livre deve levantar ValueError."""
    fm = FileManager(StorageConfig(root_dir=str(tmp_path), block_size=4096))
    fid = fm.create("teste")
    with pytest.raises(ValueError):
        fm.read(fid, 1)

    b1 = fm.allocate(fid)
    # Alocado, mas ainda não gravado no disco -> leitura além do fim físico levanta ValueError
    with pytest.raises(ValueError):
        fm.read(fid, b1)

    fm.write(fid, b1, b"X" * 4096)
    fm.free(fid, b1)
    # Bloco liberado não pode ser lido nem liberado duas vezes
    with pytest.raises(ValueError):
        fm.read(fid, b1)
    with pytest.raises(ValueError):
        fm.free(fid, b1)

    fm.delete(fid)
    assert not fm.exists("teste")
