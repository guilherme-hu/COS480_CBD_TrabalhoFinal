"""Seleção por igualdade e faixa. Camada 2, dono: P4.

Usa os cursores DataFile e Index e a reserva WORK. Os caminhos de acesso
seguem a arquitetura §7.1 e os custos de referência seguem o guia P4 §4.
"""

from contextlib import closing, nullcontext
from datetime import date
from itertools import islice
from math import ceil

from cbd.contracts import BufferFullError, Cost, IndexKind, JoinSide, NotApplicable, Org, Path
from cbd.exec.access import can_lookup, fetch_cost, lookup, read_all


def _access(table, column, path, *, ordered=False):
    field = table.schema.get(column)
    side = JoinSide(table, column, path)
    can_lookup(side)  # Valida que o caminho pedido existe.
    if path is Path.SCAN:
        return side, "scan-stop" if field.unique and not ordered else "scan"

    if path is Path.PRIMARY:
        is_hash = table.data.spec.org is Org.HASH
    else:
        is_hash = table.indexes[column].spec.kind is IndexKind.HASH
    if ordered and is_hash:
        raise NotApplicable(f"{table.name}.{column}: hash não fornece busca por faixa")
    return side, f"{path.value.lower()}-{'hash' if is_hash else 'btree'}"


def _cursor(source):
    return closing(source) if hasattr(source, "close") else nullcontext(source)


def _report(rows, info, variant, *, sorted_ptrs=False):
    if info is not None:
        info.update(variant=variant, matches=0, sort_ptrs_applied=sorted_ptrs)
    with _cursor(rows):
        for count, row in enumerate(rows, 1):
            if info is not None:
                info["matches"] = count
            yield row


def _scan_eq(side, value, stop_first):
    idx = side.table.schema.idx(side.column)
    with closing(read_all(side)) as rows:
        for row in rows:
            if row[idx] == value:
                yield row
                if stop_first:
                    return


def _starts_after(lo, hi, lo_inc, hi_inc):
    return lo is not None and hi is not None and (
        lo > hi or (lo == hi and not (lo_inc and hi_inc))
    )


def _in_range(value, lo, hi, lo_inc, hi_inc):
    return value is not None and (
        lo is None or value > lo or (lo_inc and value == lo)
    ) and (hi is None or value < hi or (hi_inc and value == hi))


def _scan_range(side, lo, hi, lo_inc, hi_inc):
    idx = side.table.schema.idx(side.column)
    with closing(read_all(side)) as rows:
        for row in rows:
            if _in_range(row[idx], lo, hi, lo_inc, hi_inc):
                yield row


def _primary_range(data, lo, hi, lo_inc, hi_inc):
    with _cursor(data.range(lo, hi, lo_inc, hi_inc)) as rows:
        for _, row in rows:
            yield row


def _can_sort_ptrs(side, requested):
    return requested and side.path is Path.SECONDARY and side.table.data.spec.org is not Org.BTREE


def _fetch(eng, data, pointers, sort_ptrs):
    if not sort_ptrs:
        for ptr in pointers:
            yield data.fetch(ptr)
        return

    per_page = eng.bp.block_size // data.ptr_size
    if eng.M < 1 or per_page < 1:
        raise BufferFullError("sort_ptrs precisa de espaço WORK para os ponteiros")
    with eng.bp.reserve_work(eng.M) as pages:
        while batch := list(islice(pointers, pages * per_page)):
            batch.sort(key=lambda ptr: (ptr.block, ptr.slot))
            for ptr in batch:
                yield data.fetch(ptr)
            batch.clear()  # Libera o lote anterior antes de montar o próximo.


def _secondary(eng, side, bounds, sort_ptrs, *, ordered=False):
    data = side.table.data
    index = side.table.indexes[side.column]
    if ordered:
        with _cursor(index.range(*bounds)) as entries:
            yield from _fetch(eng, data, (ptr for _, ptr in entries), sort_ptrs)
    else:
        with _cursor(index.search(*bounds)) as pointers:
            yield from _fetch(eng, data, pointers, sort_ptrs)


def select_eq(eng, table, column, value, path, sort_ptrs=False, info=None):
    """Filtra por igualdade SQL, em streaming, usando o caminho explícito.

    SCAN lê até todos os blocos. Índices usam uma busca e, no secundário,
    um fetch por ponteiro. Levanta NotApplicable se o caminho não existir.
    """
    side, variant = _access(table, column, path)
    sorted_ptrs = _can_sort_ptrs(side, sort_ptrs)
    if value is None:
        rows = ()
    elif path is Path.SCAN:
        rows = _scan_eq(side, value, variant == "scan-stop")
    elif sorted_ptrs:
        rows = _secondary(eng, side, (value,), sorted_ptrs)
    else:
        rows = lookup(side, value)
    yield from _report(rows, info, variant, sorted_ptrs=sorted_ptrs and value is not None)


def select_range(eng, table, column, lo, hi, lo_inc=True, hi_inc=True,
                 path=Path.SCAN, sort_ptrs=False, info=None):
    """Filtra no intervalo inclusivo/exclusivo informado. None abre um extremo.

    Usa scan ou faixa B+. Cada ponteiro secundário exige um fetch. Hash
    por faixa ou um caminho incompatível levanta NotApplicable.
    """
    side, variant = _access(table, column, path, ordered=True)
    bounds = lo, hi, lo_inc, hi_inc
    sorted_ptrs = _can_sort_ptrs(side, sort_ptrs)
    if _starts_after(*bounds):
        rows = ()
    elif path is Path.SCAN:
        rows = _scan_range(side, *bounds)
    elif path is Path.PRIMARY:
        rows = _primary_range(table.data, *bounds)
    else:
        rows = _secondary(eng, side, bounds, sorted_ptrs, ordered=True)
    yield from _report(rows, info, variant, sorted_ptrs=sorted_ptrs and bool(rows))


def _depth(stats):
    return stats["levels"]


def _bucket(stats):
    return 1 + stats["overflow_blocks"] / max(1, stats["buckets"])


def _result_leaves(matches, bfr):
    return max(1, ceil(matches / bfr)) if bfr > 0 else 1


def _range_share(stats, column, lo, hi, lo_inc, hi_inc):
    low, high = stats.min.get(column), stats.max.get(column)
    if low is None or high is None:
        return 0.0
    if low == high:
        return float(_in_range(low, lo, hi, lo_inc, hi_inc))
    if lo is not None and lo == hi:
        return 1 / max(1, stats.distinct.get(column, 1)) if low <= lo <= high else 0.0

    start = low if lo is None else max(low, lo)
    end = high if hi is None else min(high, hi)
    if isinstance(low, date):
        low, high, start, end = (bound.toordinal() for bound in (low, high, start, end))
    elif isinstance(low, str):
        encoded = [bound.encode("utf-8") for bound in (low, high, start, end)]
        width = max(map(len, encoded))
        low, high, start, end = (int.from_bytes(bound.ljust(width, b"\0"), "big") for bound in encoded)
    return max(0.0, (end - start) / (high - low)) if high > low else 0.0


def select_eq_cost(eng, table, column, value, path):
    """Estima leituras em blocos para igualdade sem reuso entre buscas."""
    _, variant = _access(table, column, path)
    if value is None:
        return Cost(0, seeks=0, formula="Igualdade com NULL não lê blocos")
    stats = table.stats()
    unique = table.schema.get(column).unique
    distinct = max(1, stats.distinct.get(column, 0))
    matches = min(1, stats.n_records) if unique else stats.n_records / distinct
    if path is Path.SCAN:
        b = stats.n_blocks
        return Cost(b / 2 if unique else b, seeks=int(b > 0), formula=f"A1: {b}{'/2' if unique else ''}")
    if path is Path.PRIMARY:
        data_stats = table.data.stats()
        if variant == "primary-hash":
            chain = _bucket(data_stats)
            extra = 0 if unique else _result_leaves(matches, stats.bfr) - 1
            return Cost(chain + extra, seeks=chain + extra,
                        formula=f"Hash: balde médio {chain:g} + blocos dos repetidos {extra}")
        depth = _depth(data_stats)
        leaves = 1 if unique else _result_leaves(matches, stats.bfr)
        return Cost(depth - 1 + leaves, seeks=depth,
                    formula=f"A{'2' if unique else '3'}: {depth} - 1 + {leaves}")

    index_stats = table.indexes[column].stats()
    if variant == "secondary-hash":
        index_reads = _bucket(index_stats)
    else:
        index_reads = _depth(index_stats) + index_stats["overflow_blocks"] / distinct
    fetch = fetch_cost(table)
    reads = index_reads + matches * fetch
    return Cost(reads, seeks=reads, formula=f"A4: índice {index_reads:g} + {matches:g} × fetch {fetch:g}")


def select_range_cost(eng, table, column, lo, hi, lo_inc=True, hi_inc=True, path=Path.SCAN):
    """Estima os blocos da faixa, assumindo valores uniformes e fetch convencional."""
    _access(table, column, path, ordered=True)
    if _starts_after(lo, hi, lo_inc, hi_inc):
        return Cost(0, seeks=0, formula="Intervalo vazio não lê blocos")
    stats = table.stats()
    if path is Path.SCAN:
        return Cost(stats.n_blocks, seeks=int(stats.n_blocks > 0), formula=f"A1: {stats.n_blocks}")
    share = _range_share(stats, column, lo, hi, lo_inc, hi_inc)
    matches = stats.n_records * share
    if path is Path.PRIMARY:
        depth = _depth(table.data.stats())
        leaves = _result_leaves(matches, stats.bfr)
        return Cost(depth - 1 + leaves, seeks=depth,
                    formula=f"A5: {depth} - 1 + {leaves} folhas ({matches:g} registros uniformes)")

    index_stats = table.indexes[column].stats()
    depth = _depth(index_stats)
    leaves = max(1, ceil(index_stats["leaf_blocks"] * share))
    fetch = fetch_cost(table)
    return Cost(depth - 1 + leaves + matches * fetch, seeks=depth + matches * fetch,
                formula=f"A6: {depth} - 1 + {leaves} folhas + {matches:g} × fetch {fetch:g}")
