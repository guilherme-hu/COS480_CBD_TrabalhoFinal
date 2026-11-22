# Estado da parte P2 — atualizado em 22/11
## Pronto (com teste que prova)
- nada ainda. `cbd/records/hashing.py` existe (escrito pelo P6 para a referência SQLite), sem teste próprio.
## Em andamento
- #39 codec (branch `p2/39-codec`): D-P2-01 decidida (campos variáveis com prefixo de tamanho `u16`). Próxima decisão: D-P2-02, nulos e campos opcionais. Código ainda não escrito.
## Falta
- #39, #40, #41, #43, #83, #46, #48, #52, #55, #62, nesta ordem (tabela no `CLAUDE.local.md` §5).
## Requisitos do enunciado cobertos
| Enunciado § | Onde está | Teste que prova |
| :-- | :-- | :-- |
## Desvios ou dúvidas sobre o contrato
- `aluno.curso` é Zipf (0,7) no gerador: curso 1 tem 6 192 alunos, o menor 416. O guia P2 §6.1 fala em "~1 000 por curso" (é só a média).
- D-P2-01 muda o tamanho do registro: o estimador do P6 (`bench/sqlite_ref.py::record_bytes`, 4 B por variável) precisa passar para 2 B. Pedido ao P6 na issue #90 (22/11).
## Armadilhas conhecidas / bugs abertos
- No Windows, 2 testes do P6 (`test_data_preparation.py`) só passam com `PYTHONUTF8=1` (abrem arquivo sem `encoding`). Avisar o P6.
## Decisões provisórias a rever
