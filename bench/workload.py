"""Consultas lógicas e respostas esperadas; caminhos/algoritmos ficam com a bancada."""
import math
import random

JOINS = (
    (1, 'small_small', 'curso', 'codigo', 'disciplina', 'curso'),
    (1, 'small_large', 'curso', 'codigo', 'aluno', 'curso'),
    (1, 'large_large', 'aluno', 'dre', 'inscricao', 'dre'),
    (2, 'small_small', 'aluno_p', 'cpf', 'detalhes_p', 'cpf'),
    (2, 'small_large', 'aluno_p', 'cpf', 'detalhes', 'cpf'),
    (2, 'large_large', 'aluno', 'cpf', 'detalhes', 'cpf'),
    (3, 'small_small', 'aluno_p', 'orientador', 'turma_p', 'professor'),
    (3, 'small_large', 'aluno_p', 'orientador', 'turma', 'professor'),
    (3, 'large_large', 'aluno', 'orientador', 'turma', 'professor'),
)


def queries(ref):
    for kind, combo, r, rc, s, sc in JOINS:
        yield dict(id=f'join_{kind}_{combo}', kind='join', join_type=kind, size_combo=combo,
                   r_table=r, r_column=rc, s_table=s, s_column=sc)
    n = ref.conn.execute('SELECT COUNT(*) FROM aluno').fetchone()[0]
    for i, dre in enumerate(random.Random(490).sample(range(1, n + 1), min(20, n))):
        yield dict(id=f'eq_dre_{i:02d}', kind='select', sel_type='eq_unique',
                   r_table='aluno', column='dre', operator='=', value=dre,
                   target_selectivity=1 / n)
    for column in ('dre', 'cpf', 'cr'):
        for sel in (.001, .5):
            op = '>=' if column == 'cr' and sel == .001 else '<='
            # Quantil empírico. Empates em CR podem exceder a meta; grave a fração real.
            offset = max(0, math.ceil(n * sel) - 1)
            order = 'DESC' if op == '>=' else 'ASC'
            value = ref.conn.execute(
                f'SELECT "{column}" FROM aluno ORDER BY "{column}" {order} LIMIT 1 OFFSET ?',
                (offset,)).fetchone()[0]
            yield dict(id=f'range_{column}_{sel:g}', kind='select',
                       sel_type='range_nonunique' if column == 'cr' else 'range_unique',
                       r_table='aluno', column=column, operator=op, value=value,
                       target_selectivity=sel)


def prepare_workload(ref):
    output = []
    n = ref.conn.execute('SELECT COUNT(*) FROM aluno').fetchone()[0]
    for query in queries(ref):
        count, checksum = ref.result(query)
        query['expected'] = dict(count=count, checksum=checksum)
        if query['kind'] == 'select':
            query['selectivity'] = count / n
        output.append(query)
    return output
