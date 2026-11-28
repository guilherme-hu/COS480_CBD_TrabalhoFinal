"""Esqueleto da B+ primária UNIQUE/NOT UNIQUE (P3), ainda não utilizável.

Assinaturas: contracts.DataFile e catálogo P2. D-P3-01…07.
Lacunas: P3_PLANO_ATUAL.md §8 (L02–L05). create/open falham antes de I/O;
não há bloco 1, carga, estatísticas ou consultas públicas implementadas.
Helpers de formatos/navegação são testados isoladamente nos outros módulos.
"""
from cbd.files.btree_core import _pending
from cbd.files.btree_node import _decode_rid, _encode_rid


class BTreeFile:
    """Somente superfície conhecida de DataFile; criar pelo catálogo é pendente."""

    ptr_size = 6  # D-P3-04: formato herdado de P2, não dado fictício.

    @classmethod
    def create(cls, bp, name, schema, spec):
        """Pendente L02: não cria arquivo nem grava metadados provisórios."""
        _pending("L02", "criação e metadados da B+ primária")

    @classmethod
    def open(cls, bp, name, schema, spec):
        """Pendente L02: formato/validação do bloco 1 e árvore vazia."""
        _pending("L02", "abertura da B+ primária")

    @property
    def height(self):
        """Altura incluindo folhas; não há default antes de L02."""
        _pending("L02", "altura persistida da árvore")

    def bulk_load(self, recs):
        """Pendente L02/L03/L04; não consome recs nem altera arquivos."""
        _pending("L02/L03/L04", "carga bottom-up da B+ primária")

    def scan(self):
        """Pendente L02/L04; não devolve iterador vazio como resultado fictício."""
        _pending("L02/L04", "percurso público das folhas")

    def search(self, key):
        """Pendente abertura e limites L02/L05; formato interno já isolado."""
        _pending("L02/L05", "busca pública por igualdade")

    def range(self, lo, hi, lo_inc=True, hi_inc=True):
        """Pendente L02/L05: ligar descida/folhas e limites por campo."""
        _pending("L02/L05", "busca pública por faixa")

    def fetch(self, ptr):
        """Pendente L02/L05; helper físico já existe, sem objeto aberto."""
        _pending("L02/L05", "fetch público no arquivo aberto")

    def encode_ptr(self, ptr):
        """RID → 6 B little-endian, D-P3-04; nenhum I/O."""
        return _encode_rid(ptr)

    def decode_ptr(self, b):
        """6 B → RID, D-P3-04; nenhum I/O."""
        return _decode_rid(b)

    def insert(self, rec):
        """D-P3-01 não oferece manutenção; rejeição final ainda é L05."""
        _pending("L05", "API final de recusa de insert")

    def delete(self, ptr):
        """D-P3-01 não oferece manutenção; rejeição final ainda é L05."""
        _pending("L05", "API final de recusa de delete")

    def reorganize(self):
        """Não reconstruir silenciosamente; contrato final depende L05."""
        _pending("L05", "API final de recusa de reorganize")

    def stats(self):
        """Pendente L02/L05; não produzir contagens falsas."""
        _pending("L02/L05", "estatísticas da B+ primária")

    def close(self):
        """Pendente L02/L04; nenhum objeto aberto é produzido nesta etapa."""
        _pending("L02/L04", "ciclo de vida do arquivo")
