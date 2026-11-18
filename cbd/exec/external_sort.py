"""Merge-sort externo. Camada 2, dono: P4.

Usa o codec, a reserva WORK e os arquivos TempFile. D-P4-03 limita os
lotes pelos bytes codificados e reserva a última fusão para o consumidor.
"""

from contextlib import ExitStack, closing, nullcontext
from heapq import merge
from typing import Iterable, Iterator

from cbd.contracts import Cost, Record, RecordTooLarge, Schema


def _memory(eng, mem):
    pages = eng.M if mem is None else mem
    if not 3 <= pages <= eng.M:
        raise ValueError("external_sort exige 3 <= mem <= eng.M")
    return pages


def _layout(schema, block_size):
    from cbd.records.codec import RecordCodec
    from cbd.records.slotted_page import SlottedPage

    codec = RecordCodec(schema)
    payload = block_size - SlottedPage.HEADER
    slot_size = payload - SlottedPage.max_record(block_size)

    def charge(record):
        return len(codec.encode(record)) + slot_size

    return payload, charge


def _cursor(rows):
    return closing(rows) if hasattr(rows, "close") else nullcontext(rows)


def _write_run(catalog, schema, records, live):
    run = catalog.temp(schema)
    live[id(run)] = run
    try:
        for record in records:
            run.append(record)
    finally:
        run.finish()
    return run


def _merge_runs(runs, order):
    with ExitStack() as cursors:
        scans = [cursors.enter_context(_cursor(run.scan())) for run in runs]
        yield from merge(*scans, key=order)


def _initial_runs(eng, records, schema, order, pages, live, info):
    runs = []
    batch = []
    payload, charge = _layout(schema, eng.bp.block_size)
    occupied, used_pages = 0, 1

    with eng.bp.reserve_work(pages - 1) as reserved:
        for record in records:
            size = charge(record)
            if size > payload:
                raise RecordTooLarge("Registro excede uma página de trabalho da ordenação")
            needs_page = occupied + size > payload
            # D-P4-03: cada registro e seu slot precisam caber inteiros na página.
            if needs_page and used_pages == reserved:
                batch.sort(key=order)
                runs.append(_write_run(eng.catalog, schema, batch, live))
                batch.clear()
                occupied, used_pages = 0, 1
            elif needs_page:
                occupied = 0
                used_pages += 1

            occupied += size
            batch.append(record)

        batch.sort(key=order)
        if not runs:
            if info is not None:
                info["in_memory"] = True
            yield from batch
        elif batch:
            runs.append(_write_run(eng.catalog, schema, batch, live))

    return runs


def external_sort(eng, source: Iterable[Record], schema: Schema, key: str,
                  n_blocks_hint: int | None = None, mem: int | None = None,
                  info=None) -> Iterator[Record]:
    """Ordena a chave ascendente, com None no fim, sem materializar a saída.

    mem é o orçamento em páginas WORK deste sort. Se a fonte também usa
    WORK, seu orçamento precisa caber à parte. n_blocks_hint é uma estimativa
    do chamador; os lotes usam os registros efetivamente lidos.
    Se os dados cabem em mem−1 páginas, só a entrada é lida. Cada passada
    intermediária pode reler e gravar todos os blocos. A final só lê.
    """
    live = {}
    try:
        with _cursor(iter(source)) as records:
            pages = _memory(eng, mem)
            column = schema.idx(key)

            def order(record):
                value = record[column]
                return value is None, value

            if info is not None:
                info.update(runs=0, passes=0, in_memory=False)
            runs = yield from _initial_runs(eng, records, schema, order, pages, live, info)

        if info is not None:
            info["runs"] = len(runs)
        while len(runs) > pages:
            next_pass = []
            fan_in = pages - 1
            for start in range(0, len(runs), fan_in):
                group = runs[start:start + fan_in]
                with closing(_merge_runs(group, order)) as rows:
                    next_pass.append(_write_run(eng.catalog, schema, rows, live))
                for run in group:
                    run.drop()
                    del live[id(run)]
            runs = next_pass
            if info is not None:
                info["passes"] += 1

        if runs:
            if info is not None:
                info["passes"] += 1
            yield from _merge_runs(runs, order)
    finally:
        # ExitStack tenta descartar cada run mesmo se outro drop falhar.
        with ExitStack() as cleanup:
            for run in live.values():
                cleanup.callback(run.drop)


def external_sort_cost(eng, n_blocks: int, mem: int | None = None) -> Cost:
    """Estima I/O supondo a mesma blocagem por passada e ausência de reuso.

    A ordenação pode mudar a ocupação das páginas, sobretudo com registros
    variáveis. n_blocks é uma estimativa da entrada, em blocos.
    """
    pages = _memory(eng, mem)
    if n_blocks < 0:
        raise ValueError("n_blocks precisa ser não negativo")
    fan_in = pages - 1
    runs = (n_blocks + fan_in - 1) // fan_in
    if runs <= 1:
        return Cost(n_blocks, seeks=int(n_blocks > 0),
                    formula=f"Sort em memória: {n_blocks} leituras e 0 gravações")

    remaining, passes = runs, 1  # A última passada admite até mem runs e só lê.
    while remaining > pages:
        remaining = (remaining + fan_in - 1) // fan_in
        passes += 1
    reads = n_blocks * (passes + 1)
    writes = n_blocks * passes
    seeks = 2 * runs + n_blocks * (2 * passes - 1)
    return Cost(reads, writes, seeks,
                f"Sort: {runs} runs, {passes} fusões, {n_blocks} × ({passes} + 1) leituras"
                f" e {n_blocks} × {passes} gravações")
