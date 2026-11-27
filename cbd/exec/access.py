"""Adapta os caminhos de acesso das entradas dos operadores. Camada 2, dono: P4.

Usa JoinSide, DataFile e Index. Cada gerador devolve um registro por vez e
fecha o cursor de origem ao terminar. O I/O é o do caminho selecionado.
"""

from contextlib import closing, nullcontext
from typing import Iterator

from cbd.contracts import (
    DataFile, Index, IndexKind, JoinSide, NotApplicable, Org, Path, Record, TableLike,
)


def _check(side: JoinSide) -> None:
    table, column = side.table, side.column
    spec = table.data.spec
    if side.path is Path.PRIMARY and (spec.org is Org.HEAP or spec.key != column):
        raise NotApplicable(f"{table.name}.{column} não é a chave da organização primária")
    if side.path is Path.SECONDARY and column not in table.indexes:
        raise NotApplicable(f"{table.name}.{column} não tem índice secundário")


def _using(cursor):
    # Cursores da pilha real retêm páginas. Iteradores comuns de dublês
    # podem não oferecer close() e não precisam de encerramento adicional.
    return closing(cursor) if hasattr(cursor, "close") else nullcontext(cursor)


def read_all(side: JoinSide) -> Iterator[Record]:
    """Varre os dados na ordem do arquivo, independentemente de side.path.

    O custo é o do scan do arquivo. Feche o iterador se parar antes do fim.
    """
    with _using(side.table.data.scan()) as rows:
        for _, record in rows:
            yield record


def can_order(side: JoinSide) -> bool:
    """Indica se o caminho entrega registros em ordem da coluna, sem ler blocos.

    Levanta NotApplicable se o caminho solicitado for incompatível.
    """
    _check(side)
    if side.path is Path.PRIMARY:
        return side.table.data.spec.org is Org.BTREE
    if side.path is Path.SECONDARY:
        return side.table.indexes[side.column].spec.kind is IndexKind.BTREE
    return False


def _ordered(data: DataFile, index: Index | None) -> Iterator[Record]:
    if index is None:
        with _using(data.range(None, None)) as rows:
            for _, record in rows:
                yield record
        return

    with _using(index.range(None, None)) as entries:
        for _, ptr in entries:
            yield data.fetch(ptr)


def read_ordered(side: JoinSide) -> Iterator[Record] | None:
    """Usa a faixa completa da B+ ou devolve None se o caminho não dá ordem.

    No secundário soma um fetch por ponteiro. Levanta NotApplicable para um
    caminho incompatível. Feche o iterador se parar antes do fim.
    """
    if not can_order(side):
        return None
    # Capture o caminho validado agora. A leitura começa na primeira iteração.
    index = side.table.indexes[side.column] if side.path is Path.SECONDARY else None
    return _ordered(side.table.data, index)


def can_lookup(side: JoinSide) -> bool:
    """Indica se o caminho permite igualdade direta, sem ler blocos.

    Levanta NotApplicable se o caminho solicitado for incompatível.
    """
    _check(side)
    return side.path is not Path.SCAN


def _matching(data: DataFile, index: Index | None, value) -> Iterator[Record]:
    if index is None:
        with _using(data.search(value)) as rows:
            for _, record in rows:
                yield record
        return

    with _using(index.search(value)) as pointers:
        for ptr in pointers:
            yield data.fetch(ptr)


def lookup(side: JoinSide, value) -> Iterator[Record] | None:
    """Busca a chave value, ou devolve None para SCAN.

    O custo é o da busca, mais um fetch por referência se for secundário.
    Levanta NotApplicable para caminho incompatível. Feche o iterador se
    parar antes do fim.
    """
    if not can_lookup(side):
        return None
    index = side.table.indexes[side.column] if side.path is Path.SECONDARY else None
    return _matching(side.table.data, index, value)


def fetch_cost(table: TableLike) -> float:
    """Estima as leituras em blocos de um fetch sem reuso no buffer (§5.4)."""
    return table.data.height if table.data.spec.org is Org.BTREE else 1
