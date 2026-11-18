"""Planejamento e execução do nested-loop. Camada 2, dono: P4.

Usa os cursores de access.py e as mesmas estatísticas na execução e no
estimador. D-P4-01 define a orientação automática da junção.
"""

from contextlib import closing
from dataclasses import dataclass
from math import ceil

from cbd.contracts import BufferFullError, Cost, JoinSide, TableStats
from cbd.exec.access import can_lookup, lookup, read_all
from cbd.exec.selection import select_eq_cost


@dataclass(frozen=True)
class _Plan:
    outer: str
    left: JoinSide
    right: JoinSide
    left_stats: TableStats
    right_stats: TableStats
    indexed: bool


def _plan(eng, r, s, outer):
    if outer not in ("auto", "r", "s"):
        raise ValueError("outer deve ser 'auto', 'r' ou 's'")

    r_lookup, s_lookup = can_lookup(r), can_lookup(s)
    r_stats, s_stats = r.table.stats(), s.table.stats()
    if outer == "auto":
        # D-P4-01: escolhe apenas pelas estatísticas de tamanho e pelo caminho.
        if r_lookup != s_lookup:
            outer = "s" if r_lookup else "r"
        elif r_lookup:
            outer = "r" if r_stats.n_records <= s_stats.n_records else "s"
        else:
            outer = "r" if r_stats.n_blocks <= s_stats.n_blocks else "s"

    if outer == "r":
        plan = _Plan(outer, r, s, r_stats, s_stats, s_lookup)
    else:
        plan = _Plan(outer, s, r, s_stats, r_stats, r_lookup)
    if not plan.indexed and eng.M < 3:
        raise BufferFullError("nested-loop em blocos precisa de M >= 3")
    return plan


def _joined(plan, ext, inner):
    return ext + inner if plan.outer == "r" else inner + ext


def _indexed(plan, outer_key, info):
    with closing(read_all(plan.left)) as rows:
        for ext in rows:
            key = ext[outer_key]
            if key is None:
                continue
            if info is not None:
                info["passes"] += 1
            with closing(lookup(plan.right, key)) as matches:
                for inner in matches:
                    yield _joined(plan, ext, inner)


def nl_join(eng, r, s, outer="auto", info=None):
    """Produz tuplas na ordem r + s. A variante indexada pesquisa o interno."""
    plan = _plan(eng, r, s, outer)
    if not plan.indexed:
        raise NotImplementedError("Variante em blocos aguarda definição do limite de memória do lote")
    if info is not None:
        info.update(variant="indexed", outer=plan.outer, outer_blocks=plan.left_stats.n_blocks, passes=0)
    outer_key = plan.left.table.schema.idx(plan.left.column)
    plan.right.table.schema.idx(plan.right.column)
    yield from _indexed(plan, outer_key, info)


def nl_join_cost(eng, r, s, outer="auto"):
    """Estima as leituras e seeks, sem assumir reuso de páginas entre passadas."""
    plan = _plan(eng, r, s, outer)
    outer_blocks = plan.left_stats.n_blocks
    if plan.indexed:
        n = plan.left_stats.n_records
        # O estimador de igualdade usa a cardinalidade média da chave para o INL.
        lookup_cost = select_eq_cost(eng, plan.right.table, plan.right.column,
                                    object(), plan.right.path)
        seeks = None if lookup_cost.seeks is None else int(outer_blocks > 0) + n * lookup_cost.seeks
        return Cost(outer_blocks + n * lookup_cost.reads, seeks=seeks,
                    formula=f"INL ({plan.outer} externo): {outer_blocks} + {n} × {lookup_cost.reads:g}")

    inner_blocks = plan.right_stats.n_blocks
    passes = ceil(outer_blocks / (eng.M - 2))
    return Cost(outer_blocks + passes * inner_blocks, seeks=2 * passes,
                formula=f"BNL ({plan.outer} externo): {outer_blocks} + {passes} × {inner_blocks}")
