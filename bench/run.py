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
import hashlib
import importlib
import json
from pathlib import Path as FsPath
import statistics
import time

from bench.sqlite_ref import output_signature
from cbd.contracts import BufferConfig, JoinSide, NotApplicable, PageType, StorageConfig

# Colunas do CSV, na ordem do P6 §6.3.
COLUMNS = (
    'exp_id', 'rep', 'dataset', 'status', 'note', 'kind', 'join_type', 'size_combo', 'config', 'algorithm',
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


# Enunciado §6 / P6 §6.2: no mínimo 3 repetições, até a variação do TEMPO ficar < 5%,
# no máximo 10. Os contadores de blocos têm de ser idênticos entre repetições.
MIN_REPS, MAX_REPS, MAX_SPREAD = 3, 10, .05
COUNTERS = tuple(c for c in COLUMNS if c.startswith(('io.', 'bp.')))
BAD_STATUS = ('error', 'nondeterministic')
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
        # Uma linha do CSV = uma linha do arquivo: o --resume descarta uma última
        # linha incompleta sem risco de cortar um campo com quebra de linha.
        clean = {k: row.get(k, '') for k in COLUMNS}
        clean = {k: v.replace('\r', ' ').replace('\n', ' ') if isinstance(v, str) else v
                 for k, v in clean.items()}
        self.writer.writerow(clean)
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


def _counter(row, col):
    value = row.get(col, '')
    return None if value in ('', None) else float(value)


def diverging_counters(rows):
    """Contadores io.*/bp.* que diferem da 1ª repetição (cache frio + mesma base =
    contagem determinística; qualquer diferença é bug, P6 §6.2)."""
    first = rows[0]
    return [c for c in COUNTERS if any(_counter(r, c) != _counter(first, c) for r in rows[1:])]


def stable(rows):
    """Repetições suficientes: ≥ MIN_REPS, contadores idênticos e tempo variando < 5%."""
    if len(rows) < MIN_REPS or diverging_counters(rows):
        return False
    return spread([float(r['time_s']) for r in rows]) < MAX_SPREAD


def _bad(row):
    return row['status'] in BAD_STATUS or str(row.get('valid')) == 'False'


def last_attempt(rows):
    """Linhas da última tentativa de um exp_id (ordem do arquivo).

    Uma linha ruim (error, nondeterministic, valid=False) encerra uma tentativa;
    o --resume mede de novo depois dela. Devolve (linhas_ok_da_tentativa, linha_ruim):
    se a última linha é ruim, ([], ela); senão, as linhas depois da última ruim.
    """
    for i in range(len(rows) - 1, -1, -1):
        if _bad(rows[i]):
            return ([], rows[i]) if i == len(rows) - 1 else (rows[i + 1:], None)
    return rows, None


def dataset_id(workload_path):
    """Identifica a base da rodada: 12 hex do SHA-256 do workload.json (mudam volumes,
    escala ou seeds -> muda a resposta esperada -> muda o id)."""
    return hashlib.sha256(FsPath(workload_path).read_bytes()).hexdigest()[:12]


def read_rows(path):
    path = FsPath(path)
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(newline='') as handle:
        return list(csv.DictReader(handle))


def load_previous(path):
    """Linhas de um CSV bruto para o --resume, depois de conferir e reparar o arquivo.

    Cabeçalho diferente de COLUMNS -> ValueError (CSV de outra versão da bancada).
    Última linha sem '\\n' (queda no meio da gravação) -> descartada do arquivo,
    para a próxima linha não ser colada na metade que sobrou.
    """
    path = FsPath(path)
    if not path.exists() or path.stat().st_size == 0:
        return []
    data = path.read_bytes()
    header = data.split(b'\n', 1)[0].decode().rstrip('\r')
    if header.split(',') != list(COLUMNS):
        raise ValueError(f'{path}: cabeçalho diferente de COLUMNS; use outro --output')
    if not data.endswith(b'\n'):
        with path.open('r+b') as handle:
            handle.truncate(data.rindex(b'\n') + 1)
    rows = read_rows(path)
    if any(None in r.values() or None in r for r in rows):
        raise ValueError(f'{path}: linha com número errado de campos no meio do arquivo')
    return rows


def run(experiments, engines, log, expected, functions=None, previous=(), dataset=''):
    """Mede cada experimento até estabilizar e devolve as linhas que bloqueiam o relatório.

    `previous` = linhas já gravadas (--resume), todas da mesma base `dataset`
    (senão ValueError). A última tentativa de cada exp_id conta para a regra de
    parada e nunca é refeita; N/A é final (determinístico). Tentativa que terminou
    em error, nondeterministic ou valid=False é medida de novo, com as repetições
    numeradas depois das existentes (o operador pode ter sido corrigido).
    Devolvidas: valid=False e nondeterministic. Abra issue para o dono do operador.
    """
    other = {r.get('dataset', '') for r in previous} - {dataset}
    if other:
        raise ValueError(f'--resume com outra base: CSV tem {sorted(other)}, a rodada é {dataset!r}')
    done = {}
    for row in previous:
        done.setdefault(row['exp_id'], []).append(row)
    invalid = []
    for exp in experiments:
        old = sorted(done.get(exp.exp_id, []), key=lambda r: int(r['rep']))
        next_rep = int(old[-1]['rep']) + 1 if old else 1
        reps, _ = last_attempt(old)
        if reps and reps[0]['status'] == 'N/A':
            continue
        if exp.query_id not in expected:
            # Matriz e workload.json fora de sincronia: sem resposta esperada não há
            # como validar, então nem mede; a linha fica para corrigir a preparação.
            log.write(dict(base_row(exp, next_rep), dataset=dataset, status='error',
                           note=f'query_id {exp.query_id!r} ausente do workload.json'))
            continue
        while len(reps) < MAX_REPS and not stable(reps):
            row = measure(engines.get(exp), exp, next_rep, functions, expected[exp.query_id])
            row['dataset'] = dataset
            next_rep += 1
            if row['status'] == 'ok' and reps and diverging_counters([reps[0], row]):
                row.update(status='nondeterministic',
                           note='contadores diferentes da 1ª repetição: '
                                + ', '.join(diverging_counters([reps[0], row])))
            log.write(row)
            if row.get('valid') is False or row['status'] == 'nondeterministic':
                invalid.append(row)     # contagem/saída determinísticas: repetir não muda o veredito
                break
            if row['status'] != 'ok':
                break
            reps.append(row)
    return invalid


NUMERIC_SUMMARY = tuple(c for c in COLUMNS if c.startswith(('io.', 'bp.', 'est.')) and c != 'est.formula') + (
    'time_s', 'result_rows', 'r_rows', 'r_blocks', 's_rows', 's_blocks')


def summarize(raw_paths, output, dataset=None):
    """`summary.csv`: uma linha por exp_id com as medianas da última tentativa.

    Só entram linhas da base `dataset`; sem `dataset`, os arquivos precisam ser de
    uma única base (senão ValueError: rodada minúscula e rodada real não se misturam).
    Se o mesmo exp_id aparece em mais de um CSV bruto, vale o arquivo de nome maior
    (prefixo de data: a rodada mais recente). Tentativa que terminou mal (error,
    nondeterministic, valid=False) aparece como a linha ruim; N/A entra como está.
    """
    files = [(path, read_rows(path)) for path in sorted(map(FsPath, raw_paths))]
    if dataset is None:
        found = {r.get('dataset', '') for _, rows in files for r in rows}
        if len(found) > 1:
            raise ValueError(f'CSVs de bases diferentes ({sorted(found)}): informe dataset')
    by_exp = {}
    for _, rows in files:
        latest = {}
        for row in rows:
            if dataset is None or row.get('dataset', '') == dataset:
                latest.setdefault(row['exp_id'], []).append(row)
        by_exp.update(latest)
    columns = ('n_reps',) + tuple(c for c in COLUMNS if c not in ('rep', 'note')) + ('note',)
    output = FsPath(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        for exp_id, rows in by_exp.items():
            rows = sorted(rows, key=lambda r: int(r['rep']))
            attempt, bad = last_attempt(rows)
            if bad is not None:
                writer.writerow(dict(bad, n_reps=0))
                continue
            ok = [r for r in attempt if r['status'] == 'ok']
            out = dict((ok or attempt)[0], n_reps=len(ok))
            for col in NUMERIC_SUMMARY:
                values = [float(r[col]) for r in ok if r.get(col) not in ('', None)]
                out[col] = statistics.median(values) if values else ''
            if ok:
                out['valid'] = all(r['valid'] == 'True' for r in ok) if all(r['valid'] for r in ok) else ''
            writer.writerow(out)
    return len(by_exp)


def report_invalid(invalid):
    for row in invalid:
        label = 'NÃO DETERMINÍSTICO' if row['status'] == 'nondeterministic' else 'INVÁLIDO'
        print(f"{label} {row['exp_id']}: algoritmo={row['algorithm']} r={row['r_table']}/{row['r_org']} "
              f"s={row['s_table'] or '-'}/{row['s_org'] or '-'} linhas={row['result_rows']}")
    return len(invalid)


def main():
    from bench.matrix import factor_experiments, join_experiments, select_experiments
    parser = argparse.ArgumentParser(description='Roda a matriz de experimentos e grava o CSV bruto.')
    parser.add_argument('--data', type=FsPath, default=FsPath('data/prepared'),
                        help='Pasta de bench.build (workload.json e db_<B>k/ da carga física)')
    parser.add_argument('--only', choices=('joins', 'selects', 'factors', 'all'), default='all',
                        help='factors = experimentos de fator único (P6 §4.4)')
    parser.add_argument('--output', type=FsPath, default=None, help='CSV de saída (padrão results/raw/<data>_<only>.csv)')
    parser.add_argument('--resume', action='store_true',
                        help='Continua um CSV existente: pula exp_id/rep já gravados (rodada que caiu)')
    parser.add_argument('--summary', type=FsPath, default=FsPath('results/summary.csv'),
                        help='Onde gravar as medianas dos CSVs de results/raw/ desta mesma base')
    args = parser.parse_args()
    workload = json.loads((args.data / 'workload.json').read_text())
    exps = []
    if args.only in ('joins', 'all'):
        exps += join_experiments()
    if args.only in ('selects', 'all'):
        exps += select_experiments(workload)
    if args.only in ('factors', 'all'):
        exps += factor_experiments(workload)
    output = args.output or FsPath('results/raw') / f'{date.today().isoformat()}_{args.only}.csv'
    if read_rows(output) and not args.resume:
        parser.error(f'{output} já tem linhas; use --resume para continuar sem duplicar')
    dataset = dataset_id(args.data / 'workload.json')
    previous = load_previous(output) if args.resume else []
    engines, log = EngineCache(args.data), CsvLog(output)
    try:
        invalid = run(exps, engines, log, expected_results(workload), previous=previous, dataset=dataset)
    finally:
        engines.close()
        log.close()
    n = summarize(sorted(output.parent.glob('*.csv')), args.summary, dataset=dataset)
    print(f'{len(exps)} experimentos em {output}; {n} linhas em {args.summary}')
    if report_invalid(invalid):
        raise SystemExit(f'{len(invalid)} linha(s) inválida(s) ou não determinística(s): '
                         'o relatório fica bloqueado')


if __name__ == '__main__':
    main()
