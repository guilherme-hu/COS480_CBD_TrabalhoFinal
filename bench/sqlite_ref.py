"""Base lógica persistente e oráculo SQLite; nunca usada para medir I/O do SGBD."""
from contextlib import closing
from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3

from cbd.contracts import FType
from cbd.records.codec import RecordCodec
from cbd.records.hashing import MASK, hash_value
from cbd.records.slotted_page import SlottedPage
from bench.schema import SCHEMAS, PK, FOREIGN_KEYS, logical_key

_SLOT = 4  # entrada (offset u16, tamanho u16) do diretório da slotted page


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def to_sql(schema, record):
    if len(record) != len(schema.fields):
        raise ValueError(f"{schema.name}: número incorreto de campos")
    result = []
    for field, value in zip(schema.fields, record):
        if value is None:
            if not field.nullable:
                raise ValueError(f"{schema.name}.{field.name}: nulo não permitido")
        elif field.ftype in (FType.CHAR, FType.VARCHAR):
            if not isinstance(value, str) or len(value.encode('utf-8')) > field.size:
                raise ValueError(f"{schema.name}.{field.name}: texto inválido")
        elif field.ftype == FType.LIST:
            if not isinstance(value, tuple) or len(value) > 16 or any(
                not isinstance(item, str) or len(item.encode('utf-8')) > field.size for item in value
            ):
                raise ValueError(f"{schema.name}.{field.name}: lista inválida")
            value = json.dumps(value, ensure_ascii=False, separators=(',', ':'))  # D-P6-08
        elif field.ftype == FType.DATE:
            if type(value) is not date:
                raise ValueError(f"{schema.name}.{field.name}: data inválida")
            value = value.isoformat()  # D-P6-08
        elif field.ftype == FType.INT:
            if type(value) is not int or not -(1 << 63) <= value < (1 << 63):
                raise ValueError(f"{schema.name}.{field.name}: inteiro inválido")
        elif field.ftype == FType.FLOAT:
            import math
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{schema.name}.{field.name}: float inválido")
        result.append(value)
    return tuple(result)


def from_sql(schema, row):
    return tuple(None if value is None else
                 date.fromisoformat(value) if field.ftype == FType.DATE else
                 tuple(json.loads(value)) if field.ftype == FType.LIST else value
                 for field, value in zip(schema.fields, row))


_CODECS = {}


def record_bytes(schema, record):
    """Bytes que o codec real do P2 grava (D-P2-01 a D-P2-05); não é medição de páginas físicas.

    Usa o próprio RecordCodec em vez de refazer a conta do layout: a estimativa acompanha
    o formato do registro sem precisar de outra issue quando ele mudar (#90).
    """
    codec = _CODECS.get(schema.name)
    if codec is None:
        codec = _CODECS[schema.name] = RecordCodec(schema)
    return len(codec.encode(record))


class SQLiteReference:
    def __init__(self, path):
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute('PRAGMA foreign_keys = ON')

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.conn.close()

    def create_schema(self):
        for name, schema in SCHEMAS.items():
            columns = []
            for field in schema.fields:
                # D-P6-06: INT evita o alias INTEGER PRIMARY KEY = rowid do SQLite.
                # Precisamos preservar a ordem de inserção embaralhada no export para heap.
                sql_type = {FType.INT: 'INT', FType.FLOAT: 'REAL'}.get(field.ftype, 'TEXT')
                column = f'{quote(field.name)} {sql_type}'
                if not field.nullable:
                    column += ' NOT NULL'
                if field.unique:
                    column += ' UNIQUE'
                columns.append(column)
            columns.append('PRIMARY KEY (' + ','.join(map(quote, PK[name])) + ')')
            for col, parent, key in FOREIGN_KEYS.get(name, ()):
                columns.append(f'FOREIGN KEY ({quote(col)}) REFERENCES {quote(parent)} ({quote(key)})')
            self.conn.execute(f'CREATE TABLE {quote(name)} ({",".join(columns)})')

    def load(self, table, records):
        schema = SCHEMAS[table]
        placeholders = ','.join('?' for _ in schema.fields)
        # executemany consome o iterador, sem materializar a tabela.
        self.conn.executemany(f'INSERT INTO {quote(table)} VALUES ({placeholders})',
                              (to_sql(schema, record) for record in records))

    def records(self, table, where='', params=()):
        schema = SCHEMAS[table]
        suffix = f' WHERE {where}' if where else ''
        with closing(self.conn.execute(f'SELECT * FROM {quote(table)}{suffix} ORDER BY rowid', params)) as cursor:
            for row in cursor:
                yield from_sql(schema, row)

    def add_reference_indexes(self):
        # Apenas acelera o oráculo e o foreign_key_check; não são os índices físicos P2/P3.
        for table, column in (('aluno', 'curso'), ('disciplina', 'curso'), ('inscricao', 'turma')):
            self.conn.execute(f'CREATE INDEX {quote("ref_" + table + "_" + column)} '
                              f'ON {quote(table)} ({quote(column)})')

    def stats(self, table, block_size=4096):
        schema = SCHEMAS[table]
        n = total = blocks = used = 0
        room = block_size - SlottedPage.HEADER
        digest = hashlib.sha256()
        for record in self.records(table):
            encoded = json.dumps(to_sql(schema, record), ensure_ascii=False, separators=(',', ':')).encode()
            digest.update(encoded + b'\n')
            size = record_bytes(schema, record)
            # Encaixe guloso do HeapFile.bulk_load: cada registro ocupa os bytes dele mais
            # 1 slot de 4 B; a página útil é B menos o cabeçalho da slotted page (#94).
            if size > SlottedPage.max_record(block_size):
                raise ValueError(f'{table}: registro não cabe no bloco {block_size}')
            if not blocks or used + size + _SLOT > room:
                blocks += 1
                used = 0
            used += size + _SLOT
            n += 1
            total += size
        distinct = {}
        from bench.schema import ACCESS_COLUMNS
        for col in ACCESS_COLUMNS.get(table, ()):
            distinct[col] = self.conn.execute(
                f'SELECT COUNT(DISTINCT {quote(col)}) FROM {quote(table)}').fetchone()[0]
        return dict(n=n, estimated_blocks=blocks, estimated_bfr=n / blocks if blocks else 0,
                    record_size=total / n if n else 0, distinct=distinct, sha256=digest.hexdigest())

    def result(self, query):
        """(count, checksum) da PK lógica, inclusive a PK composta de inscrição."""
        sql, params = query_sql(query, keys_only=True)
        n = checksum = 0
        with closing(self.conn.execute(sql, params)) as cursor:
            for row in cursor:
                if query['kind'] == 'join':
                    width = len(PK[query['r_table']])
                    key = (tuple(row[:width]), tuple(row[width:]))
                else:
                    key = tuple(row)
                n += 1
                checksum = (checksum + hash_value(key)) & MASK
        return n, checksum


def query_sql(query, keys_only=False):
    r = query['r_table']
    if r not in SCHEMAS:
        raise ValueError(r)
    rcols = PK[r] if keys_only else tuple(f.name for f in SCHEMAS[r].fields)
    projection = [f'r.{quote(c)}' for c in rcols]
    if query['kind'] == 'join':
        s = query['s_table']
        scols = PK[s] if keys_only else tuple(f.name for f in SCHEMAS[s].fields)
        SCHEMAS[r].get(query['r_column'])
        SCHEMAS[s].get(query['s_column'])
        projection += [f's.{quote(c)}' for c in scols]
        sql = f'SELECT {",".join(projection)} FROM {quote(r)} r JOIN {quote(s)} s '
        sql += f'ON r.{quote(query["r_column"])} = s.{quote(query["s_column"])}'
        return sql, ()
    col, op = query['column'], query['operator']
    SCHEMAS[r].get(col)
    if op not in ('=', '<', '<=', '>', '>='):
        raise ValueError(op)
    return (f'SELECT {",".join(projection)} FROM {quote(r)} r '
            f'WHERE r.{quote(col)} {op} ?', (query['value'],))


def output_signature(query, records):
    """Assinatura da saída real P4/P5 (r_rec + s_rec), sem depender da ordem."""
    # D-P6-05: soma mod 2^64 (não XOR) para contar duplicatas; só a PK lógica.
    n = checksum = 0
    r = query['r_table']
    width = len(SCHEMAS[r].fields)
    for record in records:
        key = logical_key(r, record[:width])
        if query['kind'] == 'join':
            key = (key, logical_key(query['s_table'], record[width:]))
        n += 1
        checksum = (checksum + hash_value(key)) & MASK
    return n, checksum
