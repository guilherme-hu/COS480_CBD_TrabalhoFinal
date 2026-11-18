"""Controle ingênuo da #64. Materializa todo o externo e excede WORK.

Gerado no fechamento para reproduzir o defeito pedido na #64. A primeira
versão histórica do operador é preservada em nested_loop_v1.py.
"""

from contextlib import closing

from cbd.exec.access import read_all


def naive_bnl(r, s, info):
    r_key = r.table.schema.idx(r.column)
    s_key = s.table.schema.idx(s.column)
    with closing(read_all(r)) as source:
        outer = list(source)
    info.update(peak_outer_records=len(outer), passes=0)
    if not outer:
        return

    info["passes"] = 1
    with closing(read_all(s)) as source:
        for detail in source:
            key = detail[s_key]
            if key is not None:
                for master in outer:
                    if master[r_key] == key:
                        yield master + detail
