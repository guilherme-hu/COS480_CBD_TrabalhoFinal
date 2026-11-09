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


def measure(eng, exp, rep=1, functions=None):
    """Executa UMA medição de `exp` em `eng` e devolve a linha do CSV (dict).

    `functions` (nome -> callable) substitui os operadores reais nos testes.
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
    t0 = time.perf_counter()
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
    dt = time.perf_counter() - t0
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


def run(experiments, engines, log, functions=None):
    for exp in experiments:
        log.write(measure(engines.get(exp), exp, 1, functions))


def main():
    from bench.matrix import join_experiments, select_experiments
    parser = argparse.ArgumentParser(description='Roda a matriz de experimentos e grava o CSV bruto.')
    parser.add_argument('--data', type=FsPath, default=FsPath('data/prepared'),
                        help='Pasta de bench.build (workload.json e db_<B>k/ da carga física)')
    parser.add_argument('--only', choices=('joins', 'selects', 'all'), default='all')
    parser.add_argument('--output', type=FsPath, default=None, help='CSV de saída (padrão results/raw/<data>_<only>.csv)')
    args = parser.parse_args()
    workload = json.loads((args.data / 'workload.json').read_text())
    exps = []
    if args.only in ('joins', 'all'):
        exps += join_experiments()
    if args.only in ('selects', 'all'):
        exps += select_experiments(workload)
    output = args.output or FsPath('results/raw') / f'{date.today().isoformat()}_{args.only}.csv'
    engines, log = EngineCache(args.data), CsvLog(output)
    try:
        run(exps, engines, log)
    finally:
        engines.close()
        log.close()
    print(f'{len(exps)} experimentos gravados em {output}')


if __name__ == '__main__':
    main()
