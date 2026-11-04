from cbd.contracts import Field, FType as T, Schema

CURSO = Schema("curso", (
    Field("codigo", T.INT, unique=True), Field("nome", T.VARCHAR, 60),
    Field("escola", T.VARCHAR, 40), Field("carga_horaria", T.INT),
    Field("coordenador", T.INT), Field("email", T.CHAR, 40, unique=True),
    Field("endereco", T.VARCHAR, 80), Field("telefones", T.LIST, 15, nullable=True),
))
DISCIPLINA = Schema("disciplina", (
    Field("codigo", T.INT, unique=True), Field("curso", T.INT),
    Field("nome", T.VARCHAR, 60), Field("ementa", T.VARCHAR, 200, nullable=True),
    Field("carga_horaria", T.INT), Field("prereqs", T.LIST, 8, nullable=True),
))
TURMA = Schema("turma", (
    Field("numero", T.INT, unique=True), Field("disciplina", T.INT),
    Field("semestre", T.CHAR, 6), Field("horario", T.CHAR, 12),
    Field("sala", T.CHAR, 8), Field("professor", T.INT),
))
ALUNO = Schema("aluno", (
    Field("dre", T.INT, unique=True), Field("cpf", T.CHAR, 11, unique=True),
    Field("nome", T.VARCHAR, 60), Field("nascimento", T.DATE), Field("sexo", T.CHAR, 1),
    Field("endereco", T.VARCHAR, 80), Field("emails", T.LIST, 40, nullable=True),
    Field("curso", T.INT), Field("cr", T.FLOAT), Field("orientador", T.INT, nullable=True),
))
INSCRICAO = Schema("inscricao", (
    Field("dre", T.INT), Field("turma", T.INT),
    Field("frequencia", T.FLOAT), Field("media", T.FLOAT, nullable=True),
))
DETALHES = Schema("detalhes", (
    Field("cpf", T.CHAR, 11, unique=True), Field("detalhes", T.LIST, 60, nullable=True),
))

# Nomes e ordem dos campos são o contrato da arquitetura §8.3.
SCHEMAS = {s.name: s for s in (CURSO, DISCIPLINA, TURMA, ALUNO, INSCRICAO, DETALHES)}
for name, base in (("aluno_p", ALUNO), ("detalhes_p", DETALHES), ("turma_p", TURMA)):
    SCHEMAS[name] = Schema(name, base.fields)

PK = {
    "curso": ("codigo",), "disciplina": ("codigo",), "turma": ("numero",),
    "aluno": ("dre",), "inscricao": ("dre", "turma"), "detalhes": ("cpf",),
    "aluno_p": ("dre",), "detalhes_p": ("cpf",), "turma_p": ("numero",),
}
ACCESS_COLUMNS = {
    "curso": ("codigo",), "disciplina": ("curso",), "turma": ("professor",),
    "aluno": ("dre", "cpf", "curso", "orientador", "cr"),
    "inscricao": ("dre",), "detalhes": ("cpf",),
    "aluno_p": ("dre", "cpf", "curso", "orientador", "cr"),
    "detalhes_p": ("cpf",), "turma_p": ("professor",),
}
FOREIGN_KEYS = {
    "disciplina": (("curso", "curso", "codigo"),),
    "turma": (("disciplina", "disciplina", "codigo"),),
    "aluno": (("curso", "curso", "codigo"),),
    "inscricao": (("dre", "aluno", "dre"), ("turma", "turma", "numero")),
    "detalhes": (("cpf", "aluno", "cpf"),),
    "aluno_p": (("dre", "aluno", "dre"),),
    "detalhes_p": (("cpf", "aluno_p", "cpf"),),
    "turma_p": (("numero", "turma", "numero"),),
}


def logical_key(table, record):
    return tuple(record[SCHEMAS[table].idx(col)] for col in PK[table])
