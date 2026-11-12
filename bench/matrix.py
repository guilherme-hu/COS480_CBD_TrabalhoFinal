"""Matriz de experimentos (P6 §4). Camada 1, dono: P6.

Lista declarativa: cada `Experiment` diz QUAL operador rodar, sobre QUAIS
variantes físicas e caminhos, e com QUAL configuração de buffer. Os operadores
e as funções de custo são referenciados por nome (`op`, `cost_fn`) e resolvidos
só na execução (`bench/run.py`), para que a matriz possa ser gerada e testada
antes de P4/P5 existirem.

Escopo (arquitetura §0, §7.4; P6 §4.2 e §4.3): NATURAL JOIN mestre-detalhe nas 3
combinações de tamanho × 3 algoritmos × 13 configurações físicas = 117 linhas,
mais as linhas com lado externo/construtor forçado. `r` é sempre o mestre.
Seleções: igualdade em aluno.dre (20 valores) e faixa em aluno.cr (1% e 50%),
nos caminhos a (SCAN), b (PRIMARY em B+ e em hash) e c (SECONDARY; na faixa,
também o B+ secundário sobre os arquivos B+ e hash, enunciado §4.2).
Experimentos de fator único (P6 §4.4, `factor_experiments`): repetem consultas da
matriz principal mudando um único fator (bloco, toss-immediate, prefetch, WORK).
Combinações que o operador recusar (NotApplicable) continuam na lista: o
orquestrador grava a linha com status N/A e o motivo, nunca a omite.
"""
from dataclasses import dataclass, field, replace
from typing import Any

from bench.workload import JOINS
from cbd.contracts import Path, PageType, Policy

BASE_BLOCK_SIZE = 4096
BASE_AREAS = {PageType.CATALOG: 8, PageType.INDEX: 64, PageType.DATA: 128, PageType.WORK: 64}


def areas_for_block(block_size, work=None):
    """Áreas do buffer para o tamanho de bloco `block_size`, com a MESMA memória em bytes
    da configuração-base (D-P6-02, ver docs/decisoes/P6.md).

    Em B = 4096 são as BASE_AREAS; em 8192 cada área tem metade das páginas e em 2048
    o dobro. Assim o experimento de tamanho de bloco não mistura "bloco maior" com
    "buffer maior". `work` = páginas de WORK em B = 4096, se diferente da base.
    """
    base = dict(BASE_AREAS)
    if work is not None:
        base[PageType.WORK] = work
    out = {}
    for area, pages in base.items():
        size = pages * BASE_BLOCK_SIZE
        # Divisão exata: senão a memória em bytes muda (ou a área zera) sem aviso.
        if size % block_size or size < block_size:
            raise ValueError(f'{area.name}: {pages} páginas de {BASE_BLOCK_SIZE} B não dão um '
                             f'número inteiro positivo de páginas de {block_size} B')
        out[area] = size // block_size
    return out

# Algoritmos de junção do enunciado §5.1: nome do operador -> função de custo irmã.
JOIN_ALGORITHMS = {
    'nl_join': 'nl_join_cost',              # P4: nested-loop em blocos (ou indexado)
    'sort_merge_join': 'sort_merge_join_cost',  # P5
    'hash_join': 'hash_join_cost',          # P5: partition-hash (GRACE)
}

SELECT_OPERATORS = {'select_eq': 'select_eq_cost', 'select_range': 'select_range_cost'}  # P4

# Política por algoritmo. PROVISÓRIO: LRU em tudo até o experimento de escolha da
# política (#16, P6 §4.4) preencher esta tabela com a de menor I/O por operação.
POLICY_BY_ALGORITHM = {name: Policy.LRU for name in (*JOIN_ALGORITHMS, *SELECT_OPERATORS)}


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


def _select_paths(column, unique):
    """Caminhos da seleção (enunciado §5.2 e §4.2): a = heap SCAN; b = organizado pela
    coluna (B+ e hash, PRIMARY); c = índice secundário (SECONDARY) sobre o heap e,
    em campo NOT UNIQUE, também o B+ secundário "clustering" sobre os arquivos
    organizados por B+ e por hash na chave (enunciado §4.2, itens B+.2 e Hash.3;
    variantes aluno__bt_dre e aluno__hs_dre de bench.build)."""
    out = [('a', 'a_scan', _heap('aluno', column, Path.SCAN)),
           ('b', 'b_btree', _organized('aluno', column, 'BTREE', '')),
           ('b', 'b_hash', _organized('aluno', column, 'HASH', '')),
           ('c', 'c_secondary', _heap('aluno', column, Path.SECONDARY))]
    if not unique:
        for org, short in (('BTREE', 'bt'), ('HASH', 'hs')):
            out.append(('c', f'c_secondary_on_{org.lower()}',
                        Side('aluno', f'aluno__{short}_dre', column, Path.SECONDARY, org)))
    return out


def select_experiments(workload):
    """Seleções a partir das consultas de `workload.json` (valores nunca redigitados).

    Igualdade UNIQUE: 20 DREs × 4 caminhos = 80 linhas (o relatório usa a média).
    Faixa NOT UNIQUE: 2 seletividades × (SCAN, B+, hash e SECONDARY sobre heap, B+
    e hash, cada SECONDARY com sort_ptrs False e True) = 18 linhas. Faixa em hash primário gera N/A na execução.
    sort_ptrs só varia na faixa: a igualdade UNIQUE devolve 1 ponteiro, e ordenar
    um ponteiro não muda nenhum acesso.
    """
    out = []
    for q in workload:
        if q['kind'] != 'select':
            continue
        col, op = q['column'], q['operator']
        if q['sel_type'] == 'eq_unique':
            operator, base = 'select_eq', dict(column=col, value=q['value'])
        else:
            # cr >= q -> [q, +inf); cr <= q -> (-inf, q]. None = aberto (arquitetura §7.1).
            lo, hi = (q['value'], None) if op == '>=' else (None, q['value'])
            operator, base = 'select_range', dict(column=col, lo=lo, hi=hi, lo_inc=True, hi_inc=True)
        for config, label, side in _select_paths(col, q['sel_type'] == 'eq_unique'):
            variants = [None]
            if side.path == Path.SECONDARY:
                variants = [False] if operator == 'select_eq' else [False, True]
            for sort_ptrs in variants:
                suffix = '' if sort_ptrs is None else ('_sorted' if sort_ptrs else '_unsorted')
                args = dict(base, table=side, path=side.path)
                if sort_ptrs is not None:
                    args['sort_ptrs'] = sort_ptrs
                out.append(Experiment(
                    exp_id=f'select.{q["id"]}.{label}{suffix}', kind='select', op=operator,
                    cost_fn=SELECT_OPERATORS[operator], args=args, config=config,
                    algorithm=operator, sel_type=q['sel_type'], selectivity=q['selectivity'],
                    path=side.path.value, sort_ptrs=sort_ptrs,
                    policy=POLICY_BY_ALGORITHM[operator], query_id=q['id']))
    return out


# Experimentos de fator único (P6 §4.4, issue #19). Níveis do guia; a base é o nível
# do meio (B = 4096, toss off, prefetch 0, WORK = 64). PENDENTE: o PR do P1
# (branch Arquivos_Buffer_Pool, cartões D-P1-09 toss e D-P1-12 prefetch) ainda não
# está no main; se um desses fatores não entrar, as linhas dele viram N/A com motivo
# (critério da #19), em vez de sumir.
BLOCK_SIZES = (2048, 4096, 8192)
TOSS_LEVELS = (False, True)
PREFETCH_LEVELS = (0, 8)
WORK_LEVELS = (16, 64, 256)


def factor_experiments(workload):
    """70 linhas: cada uma é uma linha da matriz principal com um fator mudado.

    exp_id = `factor.<fator>_<nível>.<exp_id da matriz>`, para o resto da linha
    (operador, lados, consulta esperada) ser rastreável até a matriz principal.
    - bloco: joins nos 3 tamanhos × 3 algoritmos, configs a e c_btree, áreas com a
      mesma memória em bytes (D-P6-02): 3 × 18 = 54;
    - toss-immediate: os scans (faixa em cr, caminho a, 1% e 50%) e o BNL maior ⋈
      maior em config a: 2 × 3 = 6;
    - prefetch: os scans de seleção (o efeito aparece em io.seeks): 2 × 2 = 4;
    - WORK: sort-merge e hash join maior ⋈ maior em config a: 3 × 2 = 6.
    O nível-base de cada fator também é medido, para a série de cada figura sair de
    uma mesma rodada.
    """
    joins = {e.exp_id: e for e in join_experiments()}
    scans = [e for e in select_experiments(workload)
             if e.sel_type == 'range_nonunique' and e.path == Path.SCAN.value]

    def row(exp, factor, level, **change):
        return replace(exp, exp_id=f'factor.{factor}_{level}.{exp.exp_id}', **change)

    out = []
    block_joins = [joins[f'join.{combo}.{label}.{alg}'] for combo, *_ in JOINS
                   for label in ('a', 'c_btree') for alg in JOIN_ALGORITHMS]
    for b in BLOCK_SIZES:
        out += [row(e, 'block', b, block_size=b, areas=areas_for_block(b)) for e in block_joins]
    for toss in TOSS_LEVELS:
        level = 'on' if toss else 'off'
        out += [row(e, 'toss', level, toss=toss) for e in (*scans, joins['join.large_large.a.nl_join'])]
    for k in PREFETCH_LEVELS:
        out += [row(e, 'prefetch', k, prefetch=k) for e in scans]
    for work in WORK_LEVELS:
        out += [row(joins[f'join.large_large.a.{alg}'], 'work', work,
                    areas=areas_for_block(BASE_BLOCK_SIZE, work=work))
                for alg in ('sort_merge_join', 'hash_join')]
    return out
