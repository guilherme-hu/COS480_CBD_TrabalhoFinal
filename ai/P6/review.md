# Revisão do código gerado

## Ordem de carga perdida no SQLite — corrigido

- Arquivo: `bench/sqlite_ref.py`, método `create_schema`, mapeamento de INT.
- Categoria: correção/metodologia experimental.
- Defeito: a primeira versão usava `INTEGER` para a coluna numérica com PK.
  No SQLite, `INTEGER PRIMARY KEY` passa a ser alias de `rowid`. A exportação
  `ORDER BY rowid` ordenava as chaves em vez de preservar a inserção aleatória.
  Isso invalidaria o objetivo de produzir heaps sem ordenação por acaso.
- Detecção: `test_sqlite_integrity_and_subsets` falhou comparando a sequência
  exportada com a sequência do gerador. A primeira disciplina retornada tinha
  código 1, mas o primeiro registro gerado tinha código 2.
- Correção: declarar o tipo SQL como `INT`, mantendo a afinidade de inteiro
  e um `rowid` independente da PK. A base final foi regenerada após a correção.
- Regressão: comparação de todos os registros das seis tabelas em escala
  pequena; checksums e consultas verificados por uma implementação Python
  independente. A primeira versão de `sqlite_ref.py` está em `raw/`.

## Volumes de turma — decisão de preparação

- P6 sugere 10 mil turmas e exige ≥1280 blocos para tabelas grandes na
  configuração-base. Pelo layout definido, 10 mil turmas ficam muito abaixo.
- A base final usa `--rows turma=100000`, com override explícito no manifesto.
  Os volumes originais continuam sendo os padrões do gerador.
- A preparação registra estimativas separadamente de estatísticas físicas.
  `b`/`bfr` não foram preenchidos com números inventados; a carga pelo catálogo
  registra os dados reais quando as camadas P1/P2/P3 estiverem disponíveis.

## Fronteiras e validação

- Nenhum acesso a `FileManager.read/write` foi introduzido.
- A referência SQLite e sua indexação são preparação/oráculo, fora de medições.
- Gerador, leitura do SQLite e assinaturas percorrem registros em streaming.
- A memória dos subconjuntos é limitada pelo orçamento de bytes.
- Testes do adaptador usam dublê do catálogo; isso não constitui validação da
  pilha física, ainda ausente.
- Contratos e esquemas são comparados aos trechos literais da arquitetura.
