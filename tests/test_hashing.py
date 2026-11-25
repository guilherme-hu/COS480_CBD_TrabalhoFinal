"""Testes do hash_value (P2, issue #41): estabilidade, codificação injetiva, dispersão e seeds.

Usos: partições do hash join do P5 (seed = 100 + nível), checksum do P6 (D-P6-05) e
bucket_of do hash em chaves que não são INT (D-P2-14). Ver docs/decisoes/P2.md.
"""
import math
import os
import subprocess
import sys
from collections import Counter
from datetime import date, datetime

import pytest

from bench.generator import Dataset
from bench.schema import ACCESS_COLUMNS, SCHEMAS
from cbd.records.hashing import hash_value

VALUES = [0, 1, -7, 2 ** 62, 2.5, -0.0, "abc", "", "ação", date(2026, 11, 26), None,
          (1, 2), ((1,), (2, 3)), ("a", None, 3.0)]


def _z(sizes):
    """Qui-quadrado dos tamanhos de bucket contra o uniforme, convertido em z (Wilson–Hilferty).

    Para uma função que espalha como um sorteio, z ~ N(0, 1); |z| < 4 deixa passar com
    probabilidade 1 − 6·10⁻⁵ (k grande) e reprova qualquer concentração de verdade. É bilateral:
    também reprova a uniformidade perfeita (o (k + seed) mod N da D-P2-14 dá z ≈ −150 em
    aluno.dre), então não serve para testar o bucket_of de chaves INT. Com poucos buckets a
    cauda de baixo quase some: χ² = 0 dá z = −3,40 com 4 buckets (k = 3), que passa, e
    z = −4,007 com 5 (k = 4), que reprovaria por 0,007; ali o teste é só contra concentração.
    """
    n, k = sum(sizes), len(sizes) - 1
    e = n / len(sizes)
    chi = sum((s - e) ** 2 for s in sizes) / e
    return ((chi / k) ** (1 / 3) - (1 - 2 / (9 * k))) / math.sqrt(2 / (9 * k))


def _sizes(keys, n, seed, h=hash_value):
    c = Counter(h(k, seed) % n for k in keys)
    return [c.get(i, 0) for i in range(n)]


def test_hash_stable():
    # O hash() do Python muda com o PYTHONHASHSEED para texto; o hash_value não pode mudar.
    script = ("import datetime\nfrom cbd.records.hashing import hash_value\n"
              f"print([hash_value(v, s) for v in {VALUES!r} for s in (0, 101)])")
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    outs = {subprocess.check_output([sys.executable, "-c", script], text=True, cwd=root,
                                    env=dict(os.environ, PYTHONHASHSEED=seed)) for seed in ("1", "99", "random")}
    assert outs == {str([hash_value(v, s) for v in VALUES for s in (0, 101)]) + "\n"}


def test_values_are_fixed():
    # INT, FLOAT, DATE e NULL: os mesmos da versão bruta (ai/P2/raw/hashing_v1.py), então a
    # correção da #41 não mudou as partições do P5 nem os buckets de FLOAT.
    assert [hash_value(v) for v in (1, -7, 2.5, date(2026, 11, 26), None)] == [
        9340483029297802312, 12123389045890068926, 88064665064755608, 3031378479847296906, 758538543980035830]
    assert hash_value(1, 101) == 17381439065210294877
    # Texto e tupla: os da versão corrigida (#41). O checksum do P6 soma o hash de tuplas: se
    # isto falhar, a codificação mudou e o workload.json do P6 precisa ser refeito.
    values = ("abc", "", "ação", (1,), ((1,), (1, 7)), -0.0)
    assert [hash_value(v, s) for v in values for s in (0, 101)] == [
        5244449289133915670, 14483796909450408714, 11793318583766997345, 9517369960659235066,
        12538709554468450616, 4675882129179974526, 17449735858702529886, 3577865206382776947,
        3207436678667525634, 7268720151011552786, 17436960656331623218, 1821658083230550956]


def test_encoding_is_injective():
    # Pares que a versão bruta confundia (mesmos bytes): tupla aninhada achatada e texto
    # com o separador 0x1F dentro. Mais vizinhos óbvios: texto × INT, vazio × NULL.
    # ("as", "b") × ("a", "sb") e (1, (2, 3)) × (1, (2,), 3) têm o mesmo número de itens:
    # pegam a falta do prefixo de tamanho do texto e a do número de itens da tupla.
    distinct = [(1, (2, 3)), ((1, 2), 3), (1, 2, 3), ((1,), (2,)), ((1, 2),), (1, 2),
                ("as", "b"), ("a", "sb"), (1, (2,), 3),
                ("a\x1fsb",), ("a", "b"), ("ab",), ("a", "b", ""), ("ab", ""), (), ((),), "", None,
                (None,), 1, (1,), "1", 1.0, date.fromordinal(1)]
    hashes = [hash_value(v) for v in distinct]
    assert len(set(hashes)) == len(distinct)
    # valores iguais continuam com hash igual
    assert hash_value(-0.0) == hash_value(0.0)
    assert hash_value(("x", (1, None))) == hash_value(("x", (1, None)))


@pytest.mark.parametrize("table,column", [(t, c) for t, cols in ACCESS_COLUMNS.items() for c in cols])
def test_dispersion(table, column):
    # Valores distintos de cada coluna de acesso da bancada (escala 1), em N buckets com
    # ~10 valores esperados por bucket (o qui-quadrado precisa de ≥ 5). Mede a função,
    # não o skew dos dados: o Zipf de aluno.curso pesa igual em qualquer função.
    schema = SCHEMAS[table]
    i = schema.idx(column)
    keys = sorted({r[i] for r in Dataset().records(table)} - {None})
    n = max(2, len(keys) // 10)
    pow2 = 1 << (n.bit_length() - 1)         # potência de 2: o "% n" só vê os bits de baixo
    for seed in (0, 7, 100):
        for buckets in {n, pow2}:
            assert abs(_z(_sizes(keys, buckets, seed))) < 4, (table, column, seed, buckets)


def test_seeds_are_independent():
    # Recursão do hash join (guia P5 §4.4; SKS §15.5.5.2, p. 714): a partição que não
    # coube é reparticionada com seed nova e precisa se dividir de novo. Com n potência de 2
    # (16 → 8) e com mdc > 1 (15 → 10), os dois casos em que mod e CRC-32 não separam.
    keys = range(1, 50001)
    for n0, n1 in ((16, 8), (15, 10)):
        part = [k for k in keys if hash_value(k, 100) % n0 == 0]
        assert abs(_z(_sizes(part, n1, 101))) < 4, (n0, n1)
        # 3º nível do P5 (seed 102) sobre uma partição do 2º
        sub = [k for k in part if hash_value(k, 101) % n1 == 0]
        assert abs(_z(_sizes(sub, 4, 102))) < 4, (n0, n1)
        # o mesmo teste reprova o (k + seed) mod n: a recursão não separa nada
        mod = [k for k in keys if (k + 100) % n0 == 0]
        assert _z(_sizes(mod, n1, 101, h=lambda k, s: k + s)) > 20
    # seeds diferentes concordam no bucket só por acaso (~1/N das chaves)
    same = sum(hash_value(k, 0) % 64 == hash_value(k, 1) % 64 for k in keys)
    assert abs(same - 50000 / 64) < 4 * math.sqrt(50000 / 64)


def test_datetime_is_rejected():
    # datetime é subclasse de date: no ramo DATE perderia a hora (10h e 23h do mesmo dia
    # com o mesmo hash). O codec também recusa datetime.
    with pytest.raises(TypeError):
        hash_value(datetime(2026, 1, 1, 10))
    with pytest.raises(TypeError):
        hash_value((1, datetime(2026, 1, 1)))
