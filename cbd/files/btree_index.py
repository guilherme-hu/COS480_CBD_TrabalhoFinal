"""Esqueleto do secundário B+ NOT UNIQUE (P3); não é integração pronta.

D-P3-08: chave única por entrada e RIDs externos agrupados. Largura do
contador ainda L01: nenhum formato binário provisório é persistido.
Lacunas e fonte: docs/implementacao/P3_PLANO_ATUAL.md §8, L01–L05.
"""
from cbd.files.btree_core import _pending


class BTreeIndex:
    """Superfície conhecida de Index e construtores usados pelo catálogo P2."""

    @classmethod
    def create(cls, bp, name, spec, key_field, data):
        """Pendente L01/L02; falha antes de qualquer criação de arquivo."""
        _pending("L01/L02", "criação e metadados do secundário")

    @classmethod
    def open(cls, bp, name, spec, key_field, data):
        """Pendente L01/L02: formato da entrada e metadados de abertura."""
        _pending("L01/L02", "abertura do secundário")

    def bulk_load(self, pairs):
        """Pendente L01–L04; não consome pairs nem grava grupos parciais."""
        _pending("L01/L02/L03/L04", "construção do secundário e auxiliares")

    def search(self, key):
        """Pendente L01/L02/L05; retorno futuro: RIDs individuais."""
        _pending("L01/L02/L05", "busca pública do grupo secundário")

    def range(self, lo, hi, lo_inc=True, hi_inc=True):
        """Pendente L01/L02/L05; retorno futuro: pares (chave, RID)."""
        _pending("L01/L02/L05", "faixa pública do secundário")

    def insert(self, key, ptr):
        """D-P3-01 não oferece manutenção; rejeição final ainda é L05."""
        _pending("L05", "API final de recusa de insert no secundário")

    def delete(self, key, ptr):
        """D-P3-01 não oferece manutenção; rejeição final ainda é L05."""
        _pending("L05", "API final de recusa de delete no secundário")

    def stats(self):
        """Pendente L02/L05; não confundir chaves distintas e total de RIDs."""
        _pending("L02/L05", "estatísticas do secundário")

    def close(self):
        """Pendente L02/L04; nenhum objeto aberto é produzido nesta etapa."""
        _pending("L02/L04", "ciclo de vida do secundário")
