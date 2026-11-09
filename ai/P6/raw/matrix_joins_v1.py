"""Matriz de experimentos (P6 §4). Camada 1, dono: P6.

Lista declarativa: cada `Experiment` diz QUAL operador rodar, sobre QUAIS
variantes físicas e caminhos, e com QUAL configuração de buffer. Os operadores
e as funções de custo são referenciados por nome (`op`, `cost_fn`) e resolvidos
só na execução (`bench/run.py`), para que a matriz possa ser gerada e testada
antes de P4/P5 existirem.

Escopo (arquitetura §0, §7.4; P6 §4.2): NATURAL JOIN mestre-detalhe nas 3
combinações de tamanho × 3 algoritmos × 13 configurações físicas = 117 linhas,
mais as linhas com lado externo/construtor forçado. `r` é sempre o mestre.
Combinações que o operador recusar (NotApplicable) continuam na lista: o
orquestrador grava a linha com status N/A e o motivo, nunca a omite.
"""
from dataclasses import dataclass, field
from typing import Any

from bench.workload import JOINS
from cbd.contracts import Path, PageType, Policy

BASE_BLOCK_SIZE = 4096
BASE_AREAS = {PageType.CATALOG: 8, PageType.INDEX: 64, PageType.DATA: 128, PageType.WORK: 64}

# Algoritmos de junção do enunciado §5.1: nome do operador -> função de custo irmã.
JOIN_ALGORITHMS = {
    'nl_join': 'nl_join_cost',              # P4: nested-loop em blocos (ou indexado)
    'sort_merge_join': 'sort_merge_join_cost',  # P5
    'hash_join': 'hash_join_cost',          # P5: partition-hash (GRACE)
}

# Política por algoritmo. PROVISÓRIO: LRU em tudo até o experimento de escolha da
# política (#16, P6 §4.4) preencher esta tabela com a de menor I/O por operação.
POLICY_BY_ALGORITHM = {name: Policy.LRU for name in JOIN_ALGORITHMS}


@dataclass(frozen=True)
class Side:
    """Um lado do join: variante física (nome no catálogo), coluna e caminho."""
    table: str      # tabela lógica (curso, aluno, ...)
    variant: str    # nome da variante criada por bench.build.physical_plan
    column: str
    path: Path
    org: str        # HEAP | BTREE | HASH | HASH_EQ (hash com N/seed iguais ao do outro lado)


@dataclass(frozen=True)
class Experiment:
    exp_id: str
    kind: str                       # "join" | "select"
    op: str                         # nome do operador em cbd/exec
    cost_fn: str                    # nome da função *_cost irmã
    args: dict[str, Any]            # argumentos do operador além de eng/info (r, s, outer, build...)
    size_combo: str = ''
    config: str = ''                # a..e (enunciado §5.1)
    algorithm: str = ''
    sel_type: str = ''
    selectivity: float | None = None
    path: str = ''
    sort_ptrs: bool | None = None
    block_size: int = BASE_BLOCK_SIZE
    policy: Policy = Policy.LRU
    areas: dict = field(default_factory=lambda: dict(BASE_AREAS))
    toss: bool = False
    prefetch: int = 0
    query_id: str = ''              # consulta de workload.json com a resposta esperada


def _heap(table, column, path):
    return Side(table, f'{table}__heap', column, path, 'HEAP')


def _organized(table, column, org, combo):
    if org == 'BTREE':
        return Side(table, f'{table}__bt_{column}', column, Path.PRIMARY, 'BTREE')
    if org == 'HASH':
        return Side(table, f'{table}__hs_{column}', column, Path.PRIMARY, 'HASH')
    # Par do bucket-join: N e seed iguais nos dois lados (D-P6-01).
    return Side(table, f'{table}__hs_{column}_eq_{combo}', column, Path.PRIMARY, 'HASH_EQ')


def join_configs(combo, r, rc, s, sc):
    """As 13 configurações físicas de um join (P6 §4.2), como (rótulo, config, lado r, lado s).

    a: heap SCAN × heap SCAN
    b: um lado organizado (BTREE|HASH, PRIMARY), o outro heap SCAN — duas orientações
    c: os dois organizados: BTREE-BTREE, HASH-HASH (N/seed diferentes), HASH-HASH (iguais)
    d: um lado organizado (PRIMARY), o outro heap com índice (SECONDARY) — duas orientações
    e: heap SECONDARY × heap SECONDARY
    """
    out = [('a', 'a', _heap(r, rc, Path.SCAN), _heap(s, sc, Path.SCAN))]
    for config, other in (('b', Path.SCAN), ('d', Path.SECONDARY)):
        for org in ('BTREE', 'HASH'):
            o = org.lower()
            out.append((f'{config}_r_{o}', config, _organized(r, rc, org, combo), _heap(s, sc, other)))
            out.append((f'{config}_s_{o}', config, _heap(r, rc, other), _organized(s, sc, org, combo)))
    for org in ('BTREE', 'HASH', 'HASH_EQ'):
        out.append((f'c_{org.lower()}', 'c', _organized(r, rc, org, combo), _organized(s, sc, org, combo)))
    out.append(('e', 'e', _heap(r, rc, Path.SECONDARY), _heap(s, sc, Path.SECONDARY)))
    return out


def join_experiments():
    """117 linhas (3 tamanhos × 13 configs × 3 algoritmos) + linhas de lado forçado."""
    out = []
    for combo, r, rc, s, sc in JOINS:
        query_id = f'join_{combo}'
        for label, config, rside, sside in join_configs(combo, r, rc, s, sc):
            for alg, cost in JOIN_ALGORITHMS.items():
                out.append(Experiment(
                    exp_id=f'join.{combo}.{label}.{alg}', kind='join', op=alg, cost_fn=cost,
                    args=dict(r=rside, s=sside), size_combo=combo, config=config,
                    algorithm=alg, policy=POLICY_BY_ALGORITHM[alg], query_id=query_id))
        if combo == 'small_large':
            # Enunciado §5.1.3: qual tabela é interna/externa importa. Forçar os dois
            # lados explicitamente cobre o "oposto ao auto" seja qual for a escolha do auto.
            a_r, a_s = _heap(r, rc, Path.SCAN), _heap(s, sc, Path.SCAN)
            for alg, param in (('nl_join', 'outer'), ('hash_join', 'build')):
                for side in ('r', 's'):
                    out.append(Experiment(
                        exp_id=f'join.{combo}.a.{alg}.{param}_{side}', kind='join', op=alg,
                        cost_fn=JOIN_ALGORITHMS[alg], args={'r': a_r, 's': a_s, param: side},
                        size_combo=combo, config='a', algorithm=alg,
                        policy=POLICY_BY_ALGORITHM[alg], query_id=query_id))
    return out
