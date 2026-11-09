"""Consultas lógicas e respostas esperadas. Camada 1, dono: P6.

Escopo (arquitetura §0, P6 §4.3): só o NATURAL JOIN mestre-detalhe nas 3
combinações de tamanho, igualdade UNIQUE em aluno.dre e faixa NOT UNIQUE em
aluno.cr com seletividades-alvo de 1% e 50%. Caminhos e algoritmos ficam com
a matriz de experimentos; aqui só há o "o quê" e a resposta esperada.
"""
import math
import random

# (combinação de tamanho, mestre, coluna PK, detalhe, coluna FK).
# r = mestre e s = detalhe em todas as linhas, como pede a arquitetura §7.4.
JOINS = (
    ('small_small', 'curso', 'codigo', 'disciplina', 'curso'),
    ('small_large', 'curso', 'codigo', 'aluno', 'curso'),
    ('large_large', 'aluno', 'dre', 'inscricao', 'dre'),
)

# Seletividade-alvo -> operador. 1% usa a cauda alta do CR (consulta "CR acima
# de 9" do enunciado §6.4); 50% usa a cauda baixa ("CR abaixo de 5").
CR_RANGES = ((.01, '>='), (.5, '<='))
EQ_SAMPLES = 20
EQ_SEED = 490


def queries(ref):
    for combo, r, rc, s, sc in JOINS:
        yield dict(id=f'join_{combo}', kind='join', size_combo=combo,
                   r_table=r, r_column=rc, s_table=s, s_column=sc)
    n = ref.conn.execute('SELECT COUNT(*) FROM aluno').fetchone()[0]
    for i, dre in enumerate(random.Random(EQ_SEED).sample(range(1, n + 1), min(EQ_SAMPLES, n))):
        yield dict(id=f'eq_dre_{i:02d}', kind='select', sel_type='eq_unique',
                   r_table='aluno', column='dre', operator='=', value=dre,
                   target_selectivity=1 / n)
    for sel, op in CR_RANGES:
        # q = quantil empírico dos dados carregados (P6 §4.3). Com empates em CR
        # a resposta pode passar da meta; a seletividade real vai para o JSON.
        offset = max(0, math.ceil(n * sel) - 1)
        order = 'DESC' if op == '>=' else 'ASC'
        value = ref.conn.execute(
            f'SELECT cr FROM aluno ORDER BY cr {order} LIMIT 1 OFFSET ?', (offset,)).fetchone()[0]
        yield dict(id=f'range_cr_{sel:g}', kind='select', sel_type='range_nonunique',
                   r_table='aluno', column='cr', operator=op, value=value,
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
