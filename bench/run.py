"""Orquestrador da bancada: mede cada experimento e grava o CSV bruto. Camada 1, dono: P6.

Implementa: protocolo de medição do P6 §6.1 e CSV do P6 §6.3.
Usa: Engine (cbd/engine.py), Catalog/Table (P2/P3) e os operadores de cbd/exec
(P4/P5), resolvidos por nome (`OPERATORS`) só na hora de executar.
Invariantes: a janela medida começa em `eng.cold()` e termina no `snapshot()`;
dentro dela só roda o operador e o laço que consome a saída (contagem e checksum
custam CPU, não I/O). Custo estimado, estatísticas das tabelas e validação ficam
fora da janela. `bp.pinned() == 0` ao fim de toda execução, senão aborta.
"""
import argparse
import csv
from datetime import date
import importlib
import json
from pathlib import Path as FsPath
import statistics
import time

from bench.sqlite_ref import output_signature
from cbd.contracts import BufferConfig, JoinSide, NotApplicable, PageType, StorageConfig

# Colunas do CSV, na ordem do P6 §6.3.
COLUMNS = (
    'exp_id', 'rep', 'status', 'note', 'kind', 'join_type', 'size_combo', 'config', 'algorithm',
    'variant', 'sel_type', 'selectivity', 'path', 'sort_ptrs',
    'r_table', 'r_org', 'r_rows', 'r_blocks', 's_table', 's_org', 's_rows', 's_blocks',
    'block_size', 'policy', 'area_catalog', 'area_index', 'area_data', 'area_work', 'toss', 'prefetch',
    'result_rows', 'checksum', 'valid',
    'io.reads', 'io.writes', 'io.seeks', 'io.seq_reads', 'io.rand_reads', 'io.seq_writes', 'io.rand_writes',
    'bp.requests', 'bp.hits', 'bp.misses', 'bp.hit_rate', 'bp.dirty_evictions', 'bp.forced_writes',
    'bp.prefetched',
    'bp.DATA.hits', 'bp.DATA.misses', 'bp.INDEX.hits', 'bp.INDEX.misses', 'bp.WORK.hits', 'bp.WORK.misses',
    'est.reads', 'est.writes', 'est.seeks', 'est.formula', 'info_json', 'time_s',
)

# Nome do operador -> módulo em cbd/exec (arquitetura §7). Funções *_cost no mesmo módulo.
OPERATORS = {
    'nl_join': 'cbd.exec.nested_loop', 'sort_merge_join': 'cbd.exec.sort_merge',
    'hash_join': 'cbd.exec.hash_join',
    'select_eq': 'cbd.exec.selection', 'select_range': 'cbd.exec.selection',
}


# Enunciado §6 / P6 §6.2: no mínimo 3 repetições, até a variação ficar < 5%, no máximo 10.
MIN_REPS, MAX_REPS, MAX_SPREAD = 3, 10, .05
clock = time.perf_counter      # trocado nos testes para simular tempos


class PinLeak(RuntimeError):
    """Página fixada ao fim de uma execução: a medição seguinte seria inválida."""


def load_function(name):
    module = OPERATORS[name.removesuffix('_cost')]
    return getattr(importlib.import_module(module), name)


def buffer_config(exp):
    return BufferConfig(areas=dict(exp.areas), policy=exp.policy,
                        toss_immediate=exp.toss, prefetch=exp.prefetch)


def resolve(exp, eng):
    """Troca os nomes de variante da matriz pelos objetos reais do catálogo."""
    args = dict(exp.args)
    if exp.kind == 'join':
        for key in ('r', 's'):
            side = args[key]
            args[key] = JoinSide(eng.catalog.table(side.variant), side.column, side.path)
    else:
        args['table'] = eng.catalog.table(args['table'].variant)
    return args


def _sides(exp):
    if exp.kind == 'join':
        return exp.args['r'], exp.args['s']
    return exp.args['table'], None


def _query_for_signature(exp):
    """Consulta mínima que output_signature precisa para extrair as PKs lógicas."""
    r, s = _sides(exp)
    if exp.kind == 'join':
        return dict(kind='join', r_table=r.table, s_table=s.table)
    return dict(kind='select', r_table=r.table)


def base_row(exp, rep):
    r, s = _sides(exp)
    a = exp.areas
    return {
        'exp_id': exp.exp_id, 'rep': rep, 'status': 'ok', 'note': '', 'kind': exp.kind,
        'join_type': 'natural' if exp.kind == 'join' else '', 'size_combo': exp.size_combo,
        'config': exp.config, 'algorithm': exp.algorithm, 'sel_type': exp.sel_type,
        'selectivity': exp.selectivity, 'path': exp.path, 'sort_ptrs': exp.sort_ptrs,
        'r_table': r.table, 'r_org': r.org, 's_table': s.table if s else '', 's_org': s.org if s else '',
        'block_size': exp.block_size, 'policy': exp.policy.value,
        'area_catalog': a[PageType.CATALOG], 'area_index': a[PageType.INDEX],
        'area_data': a[PageType.DATA], 'area_work': a[PageType.WORK],
        'toss': exp.toss, 'prefetch': exp.prefetch,
    }


def _check_pins(eng, exp):
    if eng.bp.pinned():
        raise PinLeak(f'{exp.exp_id}: {eng.bp.pinned()} página(s) fixada(s) ao fim da execução')


def measure(eng, exp, rep=1, functions=None, expected=None):
    """Executa UMA medição de `exp` em `eng` e devolve a linha do CSV (dict).

    `functions` (nome -> callable) substitui os operadores reais nos testes.
    `expected` = (count, checksum) da referência SQLite (workload.json) para preencher
    `valid`; a comparação acontece depois do snapshot, fora da janela medida (P6 §5).
    Status: 'ok'; 'N/A' (NotApplicable no custo ou no operador, motivo em note); ou
    'error' (outra exceção no custo ou no operador, registrada para o dono corrigir). Levanta PinLeak se
    sobrar página fixada, porque o estado do buffer contaminaria as medições seguintes.
    """
    get = (functions or {}).get
    op = get(exp.op) or load_function(exp.op)
    cost_fn = get(exp.cost_fn) or load_function(exp.cost_fn)
    row = base_row(exp, rep)
    args = resolve(exp, eng)
    try:
        before = eng.snapshot()
        est = cost_fn(eng, **args)          # ANTES da janela medida (P6 §6.1)
        if eng.snapshot() != before:
            row['note'] = 'cost_fn alterou contadores (avisar o dono); medição isolada por cold()'
    except NotApplicable as error:
        row.update(status='N/A', note=f'cost_fn: {error}')
        return row
    except Exception as error:  # bug no cost_fn: mesmo tratamento do bug no operador
        row.update(status='error', note=f'cost_fn: {type(error).__name__}: {error}')
        return row

    info = {}
    eng.cold()                              # cache frio + contadores zerados
    t0 = clock()
    try:
        # Consome TODA a saída (o operador é lazy) dentro da janela medida.
        n, checksum = output_signature(_query_for_signature(exp), op(eng, **args, info=info))
    except NotApplicable as error:
        row.update(status='N/A', note=f'operador: {error}')
        _check_pins(eng, exp)
        return row
    except Exception as error:  # bug do operador: registra e segue; a linha nunca vira resultado
        row.update(status='error', note=f'{type(error).__name__}: {error}')
        _check_pins(eng, exp)
        return row
    dt = clock() - t0
    snap = eng.snapshot()
    _check_pins(eng, exp)

    # Fora da janela: estatísticas das entradas (leem metadados, não entram na contagem).
    r, s = _sides(exp)
    for prefix, side in (('r', r), ('s', s)):
        if side is not None:
            stats = eng.catalog.table(side.variant).stats()
            row[f'{prefix}_rows'], row[f'{prefix}_blocks'] = stats.n_records, stats.n_blocks
    row.update({k: v for k, v in snap.items() if k in COLUMNS})
    row.update({
        'variant': info.get('variant', ''), 'result_rows': n, 'checksum': checksum,
        'valid': '' if expected is None else (n, checksum) == tuple(expected),
        'est.reads': est.reads, 'est.writes': est.writes, 'est.seeks': est.seeks,
        'est.formula': est.formula, 'info_json': json.dumps(info, default=str, sort_keys=True),
        'time_s': round(dt, 6),
    })
    return row


class CsvLog:
    """CSV bruto com cabeçalho fixo; acrescenta linhas e grava a cada uma (resiste a queda)."""

    def __init__(self, path):
        self.path = FsPath(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists() or self.path.stat().st_size == 0
        self.handle = self.path.open('a', newline='')
        self.writer = csv.DictWriter(self.handle, fieldnames=COLUMNS, extrasaction='raise')
        if new:
            self.writer.writeheader()
            self.handle.flush()

    def write(self, row):
        self.writer.writerow({k: row.get(k, '') for k in COLUMNS})
        self.handle.flush()

    def close(self):
        self.handle.close()


class EngineCache:
    """Mantém UM Engine aberto; reabre quando muda o tamanho de bloco ou a BufferConfig."""

    def __init__(self, db_root, open_engine=None):
        self.db_root, self.current, self.key = FsPath(db_root), None, None
        self.open_engine = open_engine or self._open

    def _open(self, block_size, bcfg):
        from cbd.engine import Engine
        return Engine(StorageConfig(str(self.db_root / f'db_{block_size // 1024}k'), block_size), bcfg)

    def get(self, exp):
        bcfg = buffer_config(exp)
        key = (exp.block_size, repr(bcfg))
        if key != self.key:
            self.close()
            self.current, self.key = self.open_engine(exp.block_size, bcfg), key
        return self.current

    def close(self):
        if self.current is not None:
            self.current.close()
            self.current, self.key = None, None


def expected_results(workload):
    """query_id -> (count, checksum) calculados no SQLite por bench.build (P6 §5)."""
    return {q['id']: (q['expected']['count'], q['expected']['checksum']) for q in workload}


def spread(values):
    """max/min − 1; 0 se todos forem zero (ex.: nenhuma gravação)."""
    lo, hi = min(values), max(values)
    if hi == 0:
        return 0.0
    return float('inf') if lo == 0 else hi / lo - 1


def stable(rows):
    """Repetições suficientes: ≥ MIN_REPS e variação < 5% em I/O total E no tempo."""
    if len(rows) < MIN_REPS:
        return False
    io = [float(r['io.reads']) + float(r['io.writes']) for r in rows]
    return spread(io) < MAX_SPREAD and spread([float(r['time_s']) for r in rows]) < MAX_SPREAD


def read_rows(path):
    path = FsPath(path)
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(newline='') as handle:
        return list(csv.DictReader(handle))


def run(experiments, engines, log, expected, functions=None, previous=()):
    """Mede cada experimento até estabilizar e devolve as linhas inválidas (valid=False).

    `previous` = linhas já gravadas (--resume): repetições existentes contam para a
    regra de parada e nunca são refeitas, então o CSV não ganha linhas duplicadas.
    Linhas N/A ou error são gravadas uma vez só. Qualquer linha inválida bloqueia o
    relatório: abra issue para o dono do operador.
    """
    done = {}
    for row in previous:
        done.setdefault(row['exp_id'], []).append(row)
    invalid = []
    for exp in experiments:
        if exp.query_id not in expected:
            # Matriz e workload.json fora de sincronia: sem resposta esperada não há
            # como validar, então nem mede; a linha fica para corrigir a preparação.
            log.write(dict(base_row(exp, 1), status='error',
                           note=f'query_id {exp.query_id!r} ausente do workload.json'))
            continue
        reps = sorted(done.get(exp.exp_id, []), key=lambda r: int(r['rep']))
        if reps and reps[0]['status'] != 'ok':
            continue
        while len(reps) < MAX_REPS and not stable(reps):
            row = measure(engines.get(exp), exp, len(reps) + 1, functions, expected[exp.query_id])
            log.write(row)
            if row.get('valid') is False:
                invalid.append(row)     # saída determinística: repetir não muda o veredito
                break
            if row['status'] != 'ok':
                break
            reps.append(row)
    return invalid


NUMERIC_SUMMARY = tuple(c for c in COLUMNS if c.startswith(('io.', 'bp.', 'est.')) and c != 'est.formula') + (
    'time_s', 'result_rows', 'r_rows', 'r_blocks', 's_rows', 's_blocks')


def summarize(raw_paths, output):
    """`summary.csv`: uma linha por exp_id com as medianas das repetições 'ok'.

    Se o mesmo exp_id aparece em mais de um CSV bruto, vale o arquivo de nome maior
    (prefixo de data: a rodada mais recente). `valid` resume como False se alguma
    repetição foi inválida. Linhas N/A/error entram como estão.
    """
    by_exp = {}
    for path in sorted(map(FsPath, raw_paths)):
        latest = {}
        for row in read_rows(path):
            latest.setdefault(row['exp_id'], []).append(row)
        by_exp.update(latest)
    columns = ('n_reps',) + tuple(c for c in COLUMNS if c not in ('rep', 'note')) + ('note',)
    output = FsPath(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        for exp_id, rows in by_exp.items():
            ok = [r for r in rows if r['status'] == 'ok']
            out = dict((ok or rows)[0], n_reps=len(ok))
            for col in NUMERIC_SUMMARY:
                values = [float(r[col]) for r in ok if r.get(col) not in ('', None)]
                out[col] = statistics.median(values) if values else ''
            if ok:
                out['valid'] = all(r['valid'] == 'True' for r in ok) if all(r['valid'] for r in ok) else ''
            writer.writerow(out)
    return len(by_exp)


def report_invalid(invalid):
    for row in invalid:
        print(f"INVÁLIDO {row['exp_id']}: algoritmo={row['algorithm']} r={row['r_table']}/{row['r_org']} "
              f"s={row['s_table'] or '-'}/{row['s_org'] or '-'} linhas={row['result_rows']}")
    return len(invalid)


def main():
    from bench.matrix import join_experiments, select_experiments
    parser = argparse.ArgumentParser(description='Roda a matriz de experimentos e grava o CSV bruto.')
    parser.add_argument('--data', type=FsPath, default=FsPath('data/prepared'),
                        help='Pasta de bench.build (workload.json e db_<B>k/ da carga física)')
    parser.add_argument('--only', choices=('joins', 'selects', 'all'), default='all')
    parser.add_argument('--output', type=FsPath, default=None, help='CSV de saída (padrão results/raw/<data>_<only>.csv)')
    parser.add_argument('--resume', action='store_true',
                        help='Continua um CSV existente: pula exp_id/rep já gravados (rodada que caiu)')
    parser.add_argument('--summary', type=FsPath, default=FsPath('results/summary.csv'),
                        help='Onde gravar as medianas de todos os CSVs em results/raw/')
    args = parser.parse_args()
    workload = json.loads((args.data / 'workload.json').read_text())
    exps = []
    if args.only in ('joins', 'all'):
        exps += join_experiments()
    if args.only in ('selects', 'all'):
        exps += select_experiments(workload)
    output = args.output or FsPath('results/raw') / f'{date.today().isoformat()}_{args.only}.csv'
    previous = read_rows(output)
    if previous and not args.resume:
        parser.error(f'{output} já tem linhas; use --resume para continuar sem duplicar')
    engines, log = EngineCache(args.data), CsvLog(output)
    try:
        invalid = run(exps, engines, log, expected_results(workload), previous=previous)
    finally:
        engines.close()
        log.close()
    n = summarize(sorted(output.parent.glob('*.csv')), args.summary)
    print(f'{len(exps)} experimentos em {output}; {n} linhas em {args.summary}')
    if report_invalid(invalid):
        raise SystemExit(f'{len(invalid)} linha(s) com valid=False: o relatório fica bloqueado')


if __name__ == '__main__':
    main()
