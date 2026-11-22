# Estado da parte P2 — atualizado em 22/11
## Pronto (com teste que prova)
- nada ainda. `cbd/records/hashing.py` existe (escrito pelo P6 para a referência SQLite), sem teste próprio.
## Em andamento
- #39 codec (branch `p2/39-codec`): D-P2-01 (campos variáveis com prefixo `u16`), D-P2-02 (NULL como sentinela, sem null bitmap) e D-P2-03 (LIST com itens `(u8 len, bytes)`, sem contagem) decididas; PR #91 aberto com as decisões. Próxima decisão: campos estruturados e derivados. Código ainda não escrito.
## Falta
- #39, #40, #41, #43, #83, #46, #48, #52, #55, #62, nesta ordem (tabela no `CLAUDE.local.md` §5).
## Requisitos do enunciado cobertos
| Enunciado § | Onde está | Teste que prova |
| :-- | :-- | :-- |
## Desvios ou dúvidas sobre o contrato
- `aluno.curso` é Zipf (0,7) no gerador: curso 1 tem 6 192 alunos, o menor 416. O guia P2 §6.1 fala em "~1 000 por curso" (é só a média).
- D-P2-01 muda o tamanho do registro: o estimador do P6 (`bench/sqlite_ref.py::record_bytes`, 4 B por variável) precisa passar para 2 B, e o D-P2-02 tira o bitmap (`(nf + 7) // 8`). Pedido ao P6 na issue #90 (22/11); complementos do D-P2-02 e do D-P2-03 (itens de LIST com `1 + len`, sem contagem) em comentários na #90.
## Armadilhas conhecidas / bugs abertos
- No Windows, 2 testes do P6 (`test_data_preparation.py`) só passam com `PYTHONUTF8=1` (abrem arquivo sem `encoding`). Avisar o P6.
## Decisões provisórias a rever
