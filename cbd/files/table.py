"""Tabela: arquivo de dados + índices secundários mantidos juntos. Camada 3, dono: P2.

Implementa: TableLike (cbd/contracts.py; arquitetura §5.2). Usa: DataFile e Index (camada 3).
Decisões: guia P2 §7. As estatísticas vêm do catálogo (Catalog.analyze); insert e delete não as atualizam
(SKS §16.3.1, p. 758: os sistemas não atualizam as estatísticas a cada modificação); o reorganize, que muda o
tamanho do arquivo, roda o analyze no fim, como o load_table.
Invariantes: todo registro do arquivo está em cada índice cuja coluna não é NULL, com o mesmo Ptr.
Custo esperado: insert = insert no arquivo + 1 insert por índice; delete = fetch + 1 delete por índice + delete
no arquivo. Os Ptrs ficam inválidos depois de reorganize(): quem apaga em lote busca de novo a cada remoção.
"""


class Table:
    """O arquivo de dados (`data`) e os índices secundários (`indexes`, coluna -> índice) de uma tabela."""

    def __init__(self, catalog, name, schema, data, indexes):
        self.catalog, self.name, self.schema, self.data, self.indexes = catalog, name, schema, data, indexes

    def insert(self, rec):
        """Insere no arquivo e em cada índice (chave NULL não entra). Devolve o Ptr.

        Se um índice recusar (ex.: DuplicateKey num índice UNIQUE), desfaz o que já entrou e repassa o erro.
        """
        ptr = self.data.insert(rec)
        done = []
        try:
            for col, idx in self.indexes.items():
                key = rec[self.schema.idx(col)]
                if key is not None:
                    idx.insert(key, ptr)
                    done.append((idx, key))
        except Exception:
            for idx, key in done:
                idx.delete(key, ptr)
            self.data.delete(ptr)
            raise
        return ptr

    def delete(self, ptr):
        """Lê o registro, tira a chave de cada índice e só então libera o slot (guia P2 §10)."""
        rec = self.data.fetch(ptr)
        for col, idx in self.indexes.items():
            key = rec[self.schema.idx(col)]
            if key is not None:
                idx.delete(key, ptr)
        self.data.delete(ptr)

    def reorganize(self):
        """Reescreve o arquivo (D-P2-11: só por comando), reconstrói os índices e refaz as estatísticas (analyze).

        Os Ptrs antigos morrem.
        """
        self.data.reorganize()
        for col in list(self.indexes):
            self.catalog._rebuild_index(self.name, col)
        self.catalog.analyze(self.name)

    def stats(self):
        """Estatísticas do último analyze (TableStats). Antes de qualquer carga, tudo zero."""
        return self.catalog._meta[self.name]["stats"]
