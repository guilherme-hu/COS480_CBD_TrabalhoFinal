"""Catálogo (dicionário de dados) e fábricas dos arquivos. Camada 3, dono: P2.

Implementa: Catalog (arquitetura §5.2). Usa: HeapFile e TempHeap (P2), BufferPool (P1).
Decisões: D-P2-12 (o catálogo são 4 relações do próprio SGBD, como na Fig. 13.12 do SKS), D-P2-13 (gravação
forçada no fim de cada operação, em ordem). Ver docs/decisoes/P2.md. Estatísticas: um analyze no fim do
load_table, como o SKS recomenda (§25.1.5.1, p. 1219); insert e delete não as atualizam (p. 758).

As 4 relações (heaps em páginas CATALOG, nomes fixos no código: o bootstrap do SKS, p. 604):
    catalog__relacoes  (nome, org, chave, unique, arquivo, n_records, n_blocks, bfr, record_size)
    catalog__atributos (relacao, posicao, nome, tipo, tamanho, nulo, unique, distinct, min, max)
    catalog__indices   (relacao, coluna, tipo, unique, arquivo)
    catalog__params    (dono, chave, valor)   dono = "tabela" ou "tabela.coluna" (índice); valor é número
Ao abrir, um scan de cada relação monta o dicionário em memória (SKS p. 604); as operações só o consultam.

Gravação (D-P2-13): no fim de cada operação, o arquivo novo vai para o disco (flush_file) e as páginas do
catálogo são forçadas (bp.force) em ordem: os detalhes primeiro e a linha da tabela (ou do índice) por
último ao criar; ao apagar, essa linha primeiro. Ao trocar linhas (analyze), a nova é gravada antes de a
velha ser apagada. Por isso, ao abrir: linha sem a tabela em `relacoes` é ignorada, e de linhas repetidas
vale a última lida.
LIMITAÇÃO: sem log, uma queda no meio de uma operação ainda pode misturar estatísticas novas e antigas; e o
FileManager só grava o cabeçalho do arquivo (bloco 0) no fechamento (D-P1-03), então uma queda de verdade
perde os blocos alocados depois da abertura. A ordem garante a coerência do catálogo, não a durabilidade.
Custo: nada nas medições (o catálogo é consultado em memória). Na carga do plano do P6, ~267 gravações
forçadas em 4 KB (medido para a D-P2-13).
"""
import itertools
from datetime import date

from cbd.contracts import Field, FType, IndexKind, IndexSpec, Org, OrgSpec, PageType, Schema, TableStats
from cbd.files.heap import HeapFile, TempHeap
from cbd.files.table import Table
from cbd.records.codec import RecordCodec


def _v(name, size, nullable=False):
    return Field(name, FType.VARCHAR, size, nullable)


def _i(name, nullable=False):
    return Field(name, FType.INT, 0, nullable)


def _f(name, nullable=False):
    return Field(name, FType.FLOAT, 0, nullable)


_RELS = {
    "relacoes": Schema("catalog__relacoes", (_v("nome", 64), _v("org", 8), _v("chave", 32, True), _i("unique"),
                                             _v("arquivo", 128), _i("n_records"), _i("n_blocks"), _f("bfr"),
                                             _f("record_size"))),
    "atributos": Schema("catalog__atributos", (_v("relacao", 64), _i("posicao"), _v("nome", 32), _v("tipo", 8),
                                               _i("tamanho"), _i("nulo"), _i("unique"), _i("distinct", True),
                                               _v("min", 255, True), _v("max", 255, True))),
    "indices": Schema("catalog__indices", (_v("relacao", 64), _v("coluna", 32), _v("tipo", 8), _i("unique"),
                                           _v("arquivo", 128))),
    "params": Schema("catalog__params", (_v("dono", 128), _v("chave", 32), _f("valor"))),
}
_MINMAX = (FType.INT, FType.FLOAT, FType.DATE, FType.CHAR)       # guia P2 §8.3
_EMPTY = TableStats(0, 0, 0.0, 0.0, {}, {}, {})


# ------------------------------------------------------------ fábricas (import preguiçoso: guia P2 §8.2)
def _datafile_cls(org):
    if org is Org.HEAP:
        return HeapFile
    if org is Org.HASH:
        from cbd.files.hash_file import HashFile
        return HashFile
    from cbd.files.btree_file import BTreeFile                    # P3
    return BTreeFile


def _index_cls(kind):
    if kind is IndexKind.HASH:
        from cbd.files.hash_index import HashIndex
        return HashIndex
    from cbd.files.btree_index import BTreeIndex                  # P3
    return BTreeIndex


# ------------------------------------------------------------ conversões das colunas do catálogo
def _param(key, value):
    """Parâmetro de OrgSpec/IndexSpec como FLOAT (D-P2-12: todos os parâmetros usados são números)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"parâmetro {key}={value!r}: o catálogo só guarda número (D-P2-12)")
    if isinstance(value, int) and abs(value) > 2 ** 53:
        raise ValueError(f"parâmetro {key}={value}: inteiro grande demais para um FLOAT exato")
    return float(value)


def _unparam(value):
    return int(value) if value.is_integer() else value


def _text(field, value):
    if value is None:
        return None
    if field.ftype is FType.DATE:
        return value.isoformat()
    return repr(value) if field.ftype is FType.FLOAT else str(value)


def _untext(field, text):
    return {FType.INT: int, FType.FLOAT: float, FType.DATE: date.fromisoformat}.get(field.ftype, str)(text)


def _attr_row(name, pos, f, distinct=None, lo=None, hi=None):
    return (name, pos, f.name, f.ftype.value, f.size, int(f.nullable), int(f.unique), distinct,
            _text(f, lo), _text(f, hi))


class Catalog:
    """Mini catálogo do enunciado §1: esquema, organização, arquivos, índices, parâmetros e estatísticas."""

    def __init__(self, bp):
        """Abre (ou cria, numa base nova) as 4 relações do catálogo e as lê para a memória."""
        self.bp = bp
        self._heaps = {k: (HeapFile.open if bp.fm.exists(s.name) else HeapFile.create)(
            bp, s.name, s, OrgSpec(Org.HEAP), PageType.CATALOG) for k, s in _RELS.items()}
        self._codecs = {k: RecordCodec(s) for k, s in _RELS.items()}
        self._meta = {}                  # nome -> schema, spec, file, stats, idx {coluna: ...}, rids {relação: [RID]}
        self._open = {}                  # nome -> Table com os arquivos abertos
        self._temps = itertools.count()
        self._load()

    # ------------------------------------------------------------ leitura
    def _load(self):
        rows = {k: list(h.scan()) for k, h in self._heaps.items()}   # o catálogo é pequeno (D-P2-12)
        tabs = {}
        for rid, r in rows["relacoes"]:
            t = tabs.setdefault(r[0], dict(rel=None, atr={}, par={}, idx={},
                                           rids=dict(relacoes=[], atributos=[], params=[])))
            t["rel"] = r                                             # repetida: vale a última (D-P2-13)
            t["rids"]["relacoes"].append(rid)
        for rid, r in rows["atributos"]:
            if r[0] in tabs:                                         # órfã: a tabela não chegou ao disco
                tabs[r[0]]["atr"][r[1]] = r
                tabs[r[0]]["rids"]["atributos"].append(rid)
        for rid, r in rows["indices"]:
            if r[0] in tabs:
                i = tabs[r[0]]["idx"].setdefault(r[1], dict(row=None, par={}, rids=dict(indices=[], params=[])))
                i["row"] = r
                i["rids"]["indices"].append(rid)
        for rid, (owner, key, value) in rows["params"]:
            table, _, col = owner.partition(".")
            t = tabs.get(table)
            target = t if t and not col else t["idx"].get(col) if t else None
            if target is not None:
                target["par"][key] = _unparam(value)
                target["rids"]["params"].append(rid)
        for name, t in tabs.items():
            self._meta[name] = self._entry(name, t)

    @staticmethod
    def _entry(name, t):
        _, org, key, unique, file, n, nb, bfr, size = t["rel"]
        fields = tuple(Field(r[2], FType(r[3]), r[4], bool(r[5]), bool(r[6])) for _, r in sorted(t["atr"].items()))
        stats = TableStats(n, nb, bfr, size, {}, {}, {})
        for f, (_, r) in zip(fields, sorted(t["atr"].items())):
            if r[7] is not None:
                stats.distinct[f.name] = r[7]
            if r[8] is not None:
                stats.min[f.name], stats.max[f.name] = _untext(f, r[8]), _untext(f, r[9])
        idx = {col: dict(spec=IndexSpec(name, col, IndexKind(i["row"][2]), bool(i["row"][3]), i["par"]),
                         file=i["row"][4], rids=i["rids"])
               for col, i in t["idx"].items()}
        return dict(schema=Schema(name, fields), spec=OrgSpec(Org(org), key, bool(unique), t["par"]), file=file,
                    stats=stats, idx=idx, rids=t["rids"])

    def table(self, name):
        """A Table `name`, abrindo o arquivo e os índices na primeira vez. KeyError se não existe."""
        if name not in self._open:
            e = self._meta[name]
            data = _datafile_cls(e["spec"].org).open(self.bp, e["file"], e["schema"], e["spec"])
            idx = {col: _index_cls(i["spec"].kind).open(self.bp, i["file"], i["spec"], e["schema"].get(col), data)
                   for col, i in e["idx"].items()}
            self._open[name] = Table(self, name, e["schema"], data, idx)
        return self._open[name]

    def tables(self):
        return sorted(self._meta)

    # ------------------------------------------------------------ gravação (D-P2-13)
    def _check(self, rel, rows):
        for r in rows:
            self._codecs[rel].encode(r)                  # ValueError (nome longo, tipo errado) antes de criar nada

    def _write(self, rel, inserts=(), deletes=()):
        """Insere e apaga linhas de uma relação do catálogo, forçando as páginas. A linha nova chega ao disco
        antes de a velha ser apagada. Devolve os RIDs das linhas novas."""
        h = self._heaps[rel]
        new = [h.insert(r) for r in inserts]
        if new:
            h.force([rid.block for rid in new])
        for rid in deletes:
            h.delete(rid)
        if deletes:
            h.force([rid.block for rid in deletes])
        return new

    def _flush(self, f):
        """O arquivo novo vai para o disco antes da linha do catálogo que aponta para ele (D-P2-13)."""
        fid = getattr(f, "fid", None)
        if fid is not None:
            self.bp.flush_file(fid)

    def _delete_file(self, f):
        fid = getattr(f, "fid", None)
        if fid is None:                                  # ponytail: arquivo sem fid (dublê, P3) só é fechado
            f.close()
            return
        self.bp.drop_file(fid)
        self.bp.fm.delete(fid)

    # ------------------------------------------------------------ operações
    def create_table(self, schema, spec):
        """Cria o arquivo vazio e registra a tabela. ValueError (nome repetido ou reservado, chave fora do
        esquema, parâmetro que não é número) antes de criar qualquer coisa."""
        name, org = schema.name, Org(spec.org)
        if name in self._meta:
            raise ValueError(f"a tabela {name} já existe")
        if name.startswith("catalog") or "." in name:
            raise ValueError(f"{name}: nome reservado ao catálogo (prefixo 'catalog' ou '.')")
        if org is not Org.HEAP and spec.key not in [f.name for f in schema.fields]:
            raise ValueError(f"{name}: a chave {spec.key!r} da organização não está no esquema")
        params = [(name, k, _param(k, v)) for k, v in spec.params.items()]
        attrs = [_attr_row(name, i, f) for i, f in enumerate(schema.fields)]
        rel = (name, org.value, spec.key, int(spec.unique), name, 0, 0, 0.0, 0.0)
        for r, rows in (("atributos", attrs), ("params", params), ("relacoes", [rel])):
            self._check(r, rows)
        data = _datafile_cls(org).create(self.bp, name, schema, spec)
        self._flush(data)
        rids = dict(atributos=self._write("atributos", attrs), params=self._write("params", params))
        rids["relacoes"] = self._write("relacoes", [rel])           # por último: o "ponteiro"
        self._meta[name] = dict(schema=schema, spec=spec, file=name, stats=_EMPTY, idx={}, rids=rids)
        self._open[name] = Table(self, name, schema, data, {})
        return self._open[name]

    def load_table(self, name, recs, bulk=True):
        """Carga em massa (bulk_load; B+ ordena antes) ou registro a registro (Table.insert). No fim, analyze."""
        t = self.table(name)
        if bulk:
            if Org(t.data.spec.org) is Org.BTREE:
                k = t.schema.idx(t.data.spec.key)
                recs = sorted(recs, key=lambda r: r[k])              # preparação: fora da medição
            t.data.bulk_load(recs)
            for col in list(t.indexes):
                self._rebuild_index(name, col)
        else:
            for r in recs:
                t.insert(r)
        self.analyze(name)

    def create_index(self, spec):
        """Cria o índice a partir dos registros que já existem (bulk_load, chaves NULL de fora) e o registra."""
        e = self._meta[spec.table]
        t = self.table(spec.table)
        field = t.schema.get(spec.column)                            # KeyError se a coluna não existe
        if spec.column in e["idx"]:
            raise ValueError(f"o índice {spec.table}.{spec.column} já existe")
        kind, file = IndexKind(spec.kind), f"{spec.table}__{spec.column}"
        params = [(f"{spec.table}.{spec.column}", k, _param(k, v)) for k, v in spec.params.items()]
        row = (spec.table, spec.column, kind.value, int(spec.unique), file)
        self._check("params", params)
        self._check("indices", [row])
        idx = _index_cls(kind).create(self.bp, file, spec, field, t.data)
        self._fill(idx, t, spec.column)
        rids = dict(params=self._write("params", params), indices=self._write("indices", [row]))
        e["idx"][spec.column] = dict(spec=spec, file=file, rids=rids)
        t.indexes[spec.column] = idx
        return idx

    def _fill(self, idx, t, col):
        k = t.schema.idx(col)
        # preparação: ordena os pares em memória, fora da medição (guia P2 §8.3)
        idx.bulk_load(sorted((r[k], ptr) for ptr, r in t.data.scan() if r[k] is not None))
        self._flush(idx)

    def _rebuild_index(self, name, col):
        """Apaga e recria o arquivo do índice a partir dos dados (depois de bulk_load ou reorganize)."""
        t, i = self.table(name), self._meta[name]["idx"][col]
        self._delete_file(t.indexes[col])
        idx = _index_cls(i["spec"].kind).create(self.bp, i["file"], i["spec"], t.schema.get(col), t.data)
        self._fill(idx, t, col)
        t.indexes[col] = idx

    def drop_index(self, table, column):
        """Apaga primeiro a linha do índice (o "ponteiro"), depois os parâmetros e o arquivo."""
        t = self.table(table)
        i = self._meta[table]["idx"].pop(column)
        self._write("indices", deletes=i["rids"]["indices"])
        self._write("params", deletes=i["rids"]["params"])
        self._delete_file(t.indexes.pop(column))

    def drop_table(self, name):
        """Apaga primeiro a linha da tabela (ela deixa de existir), depois o resto e os arquivos."""
        t = self.table(name)
        e = self._meta.pop(name)
        del self._open[name]
        self._write("relacoes", deletes=e["rids"]["relacoes"])
        for i in e["idx"].values():
            self._write("indices", deletes=i["rids"]["indices"])
            self._write("params", deletes=i["rids"]["params"])
        self._write("atributos", deletes=e["rids"]["atributos"])
        self._write("params", deletes=e["rids"]["params"])
        for idx in t.indexes.values():
            self._delete_file(idx)
        self._delete_file(t.data)

    def analyze(self, name):
        """Um scan: n_r, b_r, f_r, l_r e, por coluna, V(A, r) (sem NULL), min e max (SKS §16.3.1, p. 758).

        Grava as linhas novas antes de apagar as velhas (D-P2-13). Custo: b_r leituras (fora da medição).
        """
        t, e = self.table(name), self._meta[name]
        codec = RecordCodec(t.schema)
        n, size, seen = 0, 0, [set() for _ in t.schema.fields]
        for _, r in t.data.scan():                       # preparação: sets em memória, fora da medição
            n += 1
            size += len(codec.encode(r))
            for s, v in zip(seen, r):
                if v is not None:
                    s.add(v)
        ds = t.data.stats()
        nb = ds["leaf_blocks"] if Org(t.data.spec.org) is Org.BTREE else ds["blocks"] - ds["meta_blocks"]
        stats = TableStats(n, nb, n / nb if nb else 0.0, size / n if n else 0.0, {}, {}, {})
        attrs = []
        for pos, (f, s) in enumerate(zip(t.schema.fields, seen)):
            stats.distinct[f.name] = len(s)
            if s and f.ftype in _MINMAX:
                stats.min[f.name], stats.max[f.name] = min(s), max(s)
            attrs.append(_attr_row(name, pos, f, len(s), stats.min.get(f.name), stats.max.get(f.name)))
        spec = t.data.spec
        rel = (name, Org(spec.org).value, spec.key, int(spec.unique), e["file"], n, nb, stats.bfr, stats.record_size)
        self._check("atributos", attrs)
        e["rids"]["atributos"] = self._write("atributos", attrs, e["rids"]["atributos"])
        e["rids"]["relacoes"] = self._write("relacoes", [rel], e["rids"]["relacoes"])
        e["stats"] = stats
        return stats

    def temp(self, schema):
        """TempHeap (páginas WORK) com nome único, fora do catálogo."""
        while True:
            name = f"__temp{next(self._temps)}"
            if not self.bp.fm.exists(name):
                return TempHeap(self.bp, name, schema)

    def close(self):
        """Fecha os arquivos abertos e as relações do catálogo."""
        for t in self._open.values():
            for idx in t.indexes.values():
                idx.close()
            t.data.close()
        self._open.clear()
        for h in self._heaps.values():
            h.close()
