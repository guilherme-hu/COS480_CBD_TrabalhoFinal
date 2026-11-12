"""Preparação da base e plano de carga física. CLI: python -m bench.build --help."""
import argparse
from dataclasses import asdict
import csv
import json
import math
from pathlib import Path
import tempfile

from bench.generator import Dataset, BASE_COUNTS, SEEDS
from bench.schema import SCHEMAS, ACCESS_COLUMNS, PHYSICAL_TABLES
from bench.sqlite_ref import SQLiteReference
from bench.workload import JOINS, prepare_workload
from cbd.contracts import Schema, Org, OrgSpec, IndexKind, IndexSpec


def _default_buckets(stat, load=.8):
    """N padrão do P2 §6.1 a partir da blocagem estimada: ceil(n / (bfr · load))."""
    return max(1, math.ceil(stat['n'] / (max(1, stat['estimated_bfr']) * load)))


def _hash_params(table, column, stat, **extra):
    params = {'expected_records': stat['n'], **extra}
    if not SCHEMAS[table].get(column).unique:
        # P2 §6.1: em NOT UNIQUE o HashFile limita N a V(A) para evitar buckets vazios.
        params['distinct'] = stat['distinct'][column]
    return params


def physical_plan(stats):
    """Variantes físicas do escopo (P6 §3). Só produz especificações; não depende de P1/P2/P3.

    Por tabela de PHYSICAL_TABLES: `<t>__heap` (índice secundário em cada coluna de
    ACCESS_COLUMNS: UNIQUE -> HASH, NOT UNIQUE -> B+), `<t>__bt_<col>` e `<t>__hs_<col>`
    (índices B+ NOT UNIQUE nas outras colunas NOT UNIQUE). Mais `aluno__bt_dre_inc`
    (carga incremental, P6 §4.4) e um par hash por join com N e seed iguais (bucket-join).
    """
    plan = []

    def add(table, name, org, column=None, params=None, bulk=True):
        schema = SCHEMAS[table]
        spec = OrgSpec(org, column, schema.get(column).unique if column else False, params or {})
        indexes = []
        for col in ACCESS_COLUMNS[table]:
            field = schema.get(col)
            # Arquitetura §5.3: sobre B+/hash primário só há secundário B+ NOT UNIQUE.
            if org == Org.HEAP or (col != column and not field.unique):
                kind = IndexKind.HASH if field.unique else IndexKind.BTREE
                indexes.append(asdict(IndexSpec(name, col, kind, field.unique)))
        plan.append(dict(source=table, name=name, org=asdict(spec), indexes=indexes, bulk=bulk))

    for table in PHYSICAL_TABLES:
        seed = SEEDS[table]  # seeds distintas entre tabelas: caso "sem bucket-join"
        add(table, f'{table}__heap', Org.HEAP)
        for col in ACCESS_COLUMNS[table]:
            add(table, f'{table}__bt_{col}', Org.BTREE, col, {'fill': .9})
            add(table, f'{table}__hs_{col}', Org.HASH, col,
                _hash_params(table, col, stats[table], seed=seed, load=.8))
    add('aluno', 'aluno__bt_dre_inc', Org.BTREE, 'dre', {'fill': .9}, bulk=False)
    for combo, r, rc, s, sc in JOINS:
        # D-P6-01 (provisória): N comum = menor N padrão do par, limitado por V(A)
        # do lado NOT UNIQUE. O lado pequeno não ganha buckets vazios; o grande
        # ganha cadeias de overflow com blocos cheios (ver docs/decisoes/P6.md).
        buckets = min(_default_buckets(stats[r]), _default_buckets(stats[s]))
        for table, col in ((r, rc), (s, sc)):
            if not SCHEMAS[table].get(col).unique:
                buckets = min(buckets, stats[table]['distinct'][col])
        for table, col in ((r, rc), (s, sc)):
            add(table, f'{table}__hs_{col}_eq_{combo}', Org.HASH, col,
                _hash_params(table, col, stats[table], buckets=buckets, seed=480))
    return plan


def load_physical(eng, ref, plan):
    """Carga só via Catalog; falha antes de alterar se já existir uma variante.

    Preparação: fora da medição. Não acessa bytes de arquivos físicos.
    """
    existing = set(eng.catalog.tables())
    conflicts = existing.intersection(item['name'] for item in plan)
    if conflicts:
        raise ValueError(f'Variantes já existentes: {sorted(conflicts)}')
    rows = []
    for item in plan:
        source, name = item['source'], item['name']
        cfg = item['org']
        spec = OrgSpec(Org(cfg['org']), cfg['key'], cfg['unique'], cfg['params'])
        eng.catalog.create_table(Schema(name, SCHEMAS[source].fields), spec)
        eng.catalog.load_table(name, ref.records(source), bulk=item['bulk'])
        for index in item['indexes']:
            eng.catalog.create_index(IndexSpec(name, index['column'], IndexKind(index['kind']),
                                                index['unique'], index['params']))
        table = eng.catalog.table(name)
        stats = table.stats()
        rows.append(dict(table=source, variant=name, org=spec.org.value,
                         n=stats.n_records, b=stats.n_blocks, bfr=stats.bfr,
                         distinct=stats.distinct, data=table.data.stats(),
                         indexes={col: idx.stats() for col, idx in table.indexes.items()}))
    return rows


def size_checks(stats, work_pages=64, data_pages=128, physical=False):
    """Exibe a adequação dos pares; não reclassifica silenciosamente um join."""
    output = []
    for combo, r, _, s, _ in JOINS:
        roles = combo.split('_')
        problems = []
        for table, role in zip((r, s), roles):
            b = stats[table]['b' if physical else 'estimated_blocks']
            limit = work_pages - 2 if role == 'small' else 10 * data_pages
            if (role == 'small' and b > limit) or (role == 'large' and b < limit):
                problems.append(f'{table}: {b} blocos, esperado {"<=" if role == "small" else ">="} {limit}')
        output.append(dict(size_combo=combo, problems=problems,
                           status='inadequate' if problems else 'ok',
                           basis='physical' if physical else 'estimate'))
    return output


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def prepare(output, dataset, block_sizes=(2048, 4096, 8192), work_pages=64, force=False):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    target = output / 'ref.sqlite'
    if target.exists() and not force:
        raise FileExistsError(f'{target} já existe; use --force para recriar')
    if work_pages < 3 or not block_sizes or any(b not in (2048, 4096, 8192) for b in block_sizes):
        raise ValueError('Blocos: 2048/4096/8192; WORK >= 3')
    # Publicação da referência somente depois de carga e validação bem-sucedidas.
    with tempfile.TemporaryDirectory(prefix='.prepare-', dir=output) as staging:
        staged = Path(staging) / 'ref.sqlite'
        with SQLiteReference(staged) as ref:
            with ref.conn:
                ref.create_schema()
                for table in BASE_COUNTS:
                    print(f'Carregando {table}: {dataset.counts[table]} registros', flush=True)
                    ref.load(table, dataset.records(table))
                ref.add_reference_indexes()
            if list(ref.conn.execute('PRAGMA foreign_key_check')):
                raise ValueError('Falha de integridade referencial')
            if ref.conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Falha de integridade SQLite')
            print('Calculando consultas e assinaturas de referência...', flush=True)
            workload = prepare_workload(ref)
            reports = {}
            plans = {}
            for block_size in block_sizes:
                reports[block_size] = {table: ref.stats(table, block_size) for table in SCHEMAS}
                plans[block_size] = physical_plan(reports[block_size])
            schema = {name: asdict(value) for name, value in SCHEMAS.items()}
            metadata = dict(format_version=2, scale=dataset.scale, rows=dataset.counts, seeds=SEEDS,
                            overrides=dict(dataset.row_overrides), work_pages=work_pages, schema=schema,
                            sizes={b: size_checks(s, work_pages) for b, s in reports.items()})
            ref.conn.execute('CREATE TABLE _preparation (metadata TEXT NOT NULL)')
            ref.conn.execute('INSERT INTO _preparation VALUES (?)', (json.dumps(metadata),))
            ref.conn.commit()
        write_json(Path(staging) / 'manifest.json', metadata)
        write_json(Path(staging) / 'workload.json', workload)
        for block_size, plan in plans.items():
            write_json(Path(staging) / f'physical_plan_{block_size}.json', plan)
        with (Path(staging) / 'base_stats.csv').open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=('table', 'block_size', 'n', 'b', 'bfr',
                                                        'estimated_blocks', 'estimated_bfr',
                                                        'record_size', 'distinct', 'sha256', 'basis'))
            writer.writeheader()
            for block_size, stats in reports.items():
                for table, row in stats.items():
                    writer.writerow(dict(row, table=table, block_size=block_size,
                                         distinct=json.dumps(row['distinct'], sort_keys=True), basis='estimate'))
                    print(f"{table:12s} B={block_size} n={row['n']:7d} "
                          f"blocos_estimados={row['estimated_blocks']:5d} V(A)={row['distinct']}", flush=True)
        # O banco e os arquivos auxiliares só substituem a preparação anterior após sucesso.
        for path in Path(staging).iterdir():
            path.replace(output / path.name)
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scale', type=float, default=.1)
    parser.add_argument('--output', type=Path, default=Path('data/prepared'))
    parser.add_argument('--results', type=Path, default=Path('results'),
                        help='Diretório do CSV de estatísticas versionável')
    parser.add_argument('--block-sizes', type=int, nargs='+', default=[2048, 4096, 8192])
    parser.add_argument('--work-pages', type=int, default=64,
                        help='Páginas WORK (M) usadas para conferir se a tabela menor cabe em M-2')
    parser.add_argument('--rows', action='append', default=[], metavar='TABLE=N',
                        help='Ajusta um volume; detalhes sempre acompanha aluno')
    parser.add_argument('--force', action='store_true', help='Recria a preparação lógica existente')
    parser.add_argument('--physical', action='store_true', help='Carrega via Engine P1–P3, quando disponível')
    args = parser.parse_args()
    try:
        overrides = tuple((name, int(count)) for name, count in (v.split('=') for v in args.rows))
        dataset = Dataset(args.scale, overrides)
        metadata = prepare(args.output, dataset, tuple(args.block_sizes), args.work_pages, args.force)
    except (ValueError, FileExistsError) as error:
        parser.error(str(error))
    import shutil
    args.results.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.output / 'base_stats.csv', args.results / 'base_stats.csv')
    for check in metadata['sizes'].get(4096, next(iter(metadata['sizes'].values()))):
        for problem in check['problems']:
            print(f'Volume a calibrar (estimativa): {problem}')
    if args.physical:
        from cbd.engine import Engine
        from cbd.contracts import StorageConfig, BufferConfig, PageType
        try:
            with SQLiteReference(args.output / 'ref.sqlite') as ref:
                for block_size in args.block_sizes:
                    plan = json.loads((args.output / f'physical_plan_{block_size}.json').read_text())
                    cfg = BufferConfig()
                    cfg.areas[PageType.WORK] = args.work_pages
                    with Engine(StorageConfig(str(args.output / f'db_{block_size // 1024}k'), block_size), cfg) as eng:
                        rows = load_physical(eng, ref, plan)
                    write_json(args.output / f'physical_stats_{block_size}.json', rows)
                    by_table = {row['table']: row for row in rows if row['org'] == 'HEAP'}
                    write_json(args.output / f'physical_sizes_{block_size}.json',
                               size_checks(by_table, args.work_pages, physical=True))
        except ModuleNotFoundError as error:
            parser.exit(2, f'Base lógica pronta. Carga física depende das partes P1–P3 ausentes: {error.name}\n')
    print(f'Base e carga de trabalho prontas em {args.output}')


if __name__ == '__main__':
    main()
