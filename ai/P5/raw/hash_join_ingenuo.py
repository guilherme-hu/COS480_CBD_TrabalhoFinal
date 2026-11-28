"""Versão ingênua para a comparação da #69. Executável, fora da produção."""


def hash_join_ingenuo(eng, r, s, info=None):
    """Materializa todo r em memória e sonda s. Mede o pico em registros vivos."""
    from contextlib import closing

    from cbd.exec.access import read_all

    ri, si = r.table.schema.idx(r.column), s.table.schema.idx(s.column)
    groups = {}
    count = 0
    with closing(read_all(r)) as records:
        for row in records:
            if row[ri] is not None:
                groups.setdefault(row[ri], []).append(row)
                count += 1
    if info is not None:
        info["peak_build_records"] = count
    with closing(read_all(s)) as records:
        for row in records:
            if row[si] is not None:
                for match in groups.get(row[si], ()):
                    yield match + row
