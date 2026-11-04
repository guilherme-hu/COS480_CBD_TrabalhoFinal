"""Dados sintéticos reproduzíveis; nenhuma tabela é materializada em lista."""
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
import math
import random

BASE_COUNTS = dict(curso=50, disciplina=1_000, turma=10_000,
                   aluno=50_000, inscricao=500_000, detalhes=50_000)
SEEDS = {name: 480 + i for i, name in enumerate(BASE_COUNTS)}


@dataclass(frozen=True)
class Dataset:
    scale: float = 1.0
    row_overrides: tuple[tuple[str, int], ...] = ()

    def __post_init__(self):
        if not math.isfinite(self.scale) or self.scale <= 0:
            raise ValueError("scale deve ser finita e maior que zero")
        for table, count in self.row_overrides:
            if table not in BASE_COUNTS or table == 'detalhes' or type(count) is not int or count < 1:
                raise ValueError(f'Volume inválido: {table}={count}; detalhes acompanha aluno')

    @property
    def counts(self):
        counts = {name: max(1, int(n * self.scale)) for name, n in BASE_COUNTS.items()}
        counts.update(self.row_overrides)
        counts['detalhes'] = counts['aluno']
        # A PK composta não permite repetir o mesmo par aluno/turma.
        counts["inscricao"] = min(counts["inscricao"], counts["aluno"] * counts["turma"])
        return counts

    def records(self, table):
        if table not in BASE_COUNTS:
            raise KeyError(table)
        n = self.counts
        rng = random.Random(SEEDS[table])
        domain = n["aluno"] * n["turma"] if table == "inscricao" else n[table]
        for pos, index in enumerate(permutation(domain, rng)):
            if pos >= n[table]:
                break
            k = index + 1
            if table == "curso":
                yield (k, f"Curso {k}", f"Escola {(k - 1) % 8 + 1}", 3600,
                       rng.randint(1, 2000), f"curso{k}@universidade.example",
                       f"Rua da Universidade|{k}|Centro|Rio de Janeiro|RJ",
                       None if k % 10 == 0 else (f"212555{k % 10000:04d}",))
            elif table == "disciplina":
                yield (k, rng.randint(1, n["curso"]), f"Disciplina {k}",
                       None if k % 7 == 0 else f"Ementa da disciplina {k}: fundamentos e pratica.",
                       rng.choice((30, 60, 90)),
                       tuple(str(rng.randint(1, k - 1)) for _ in range(min(2, k - 1))) if k > 1 else ())
            elif table == "turma":
                yield (k, rng.randint(1, n["disciplina"]), "2026.2", "SEG 08-10", f"S{k % 200:03d}",
                       rng.randint(1, 2000))
            elif table == "aluno":
                # Inversa da CDF Zipf, calculada uma vez e armazenada em O(n_cursos).
                curso = zipf_course(rng, n["curso"])
                cr = rng.gauss(6.5, 1.8)
                while not 0 <= cr <= 10:
                    cr = rng.gauss(6.5, 1.8)
                yield (k, cpf(k), f"Aluno {k:08d}", date(1990, 1, 1) + timedelta(days=rng.randrange(7305)),
                       rng.choice(("F", "M", "O")),
                       f"Rua dos Estudantes|{k}|Centro|Rio de Janeiro|RJ",
                       None if rng.random() < .1 else (f"a{k}@universidade.example",),
                       curso, round(cr, 2), None if rng.random() < .3 else rng.randint(1, 2000))
            elif table == "inscricao":
                dre, turma = divmod(index, n["turma"])
                yield (dre + 1, turma + 1, round(rng.uniform(40, 100), 2),
                       None if rng.random() < .05 else round(rng.uniform(0, 10), 2))
            else:
                yield (cpf(k), tuple(f"Detalhe {j} do aluno {k}" for j in range(rng.randrange(9))))


def cpf(k):
    """Identificador sintético único de 11 dígitos; não representa CPF real."""
    if not 1 <= k < 100_000_000_000:
        raise ValueError("Volume ultrapassa o domínio do CPF sintético")
    return f"{k:011d}"


def permutation(n, rng):
    """Feistel com cycle walking: permutação pseudoaleatória em memória O(1).

    Evita tanto shuffle de milhões de IDs quanto rejeições crescentes por set.
    O domínio é a menor potência de 4 que contém n; chaves independentes por tabela.
    """
    if n < 1:
        return
    bits = max(1, ((n - 1).bit_length() + 1) // 2)
    mask = (1 << bits) - 1
    keys = [rng.getrandbits(64) for _ in range(6)]

    def permute(x):
        left, right = x >> bits, x & mask
        for key in keys:
            v = (right ^ key) * 0x9E3779B97F4A7C15 & ((1 << 64) - 1)
            v ^= v >> 32
            left, right = right, left ^ (v & mask)
        return (left << bits) | right

    for i in range(n):
        value = permute(i)
        while value >= n:
            value = permute(value)
        yield value


def zipf_course(rng, count):
    # Cache limitado ao número de escalas usadas, sem guardar registros.
    from bisect import bisect_left
    return bisect_left(_zipf_weights(count), rng.random() * _zipf_weights(count)[-1]) + 1


@lru_cache(maxsize=16)
def _zipf_weights(count):
    from itertools import accumulate
    return tuple(accumulate(1 / k ** .7 for k in range(1, count + 1)))
