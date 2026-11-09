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

## N comum dos pares bucket-join inflava o lado menor — corrigido (08/11, #5)

- Arquivo: `bench/build.py`, `physical_plan`, laço dos pares `_eq_`.
- Categoria: eficiência / metodologia experimental (afeta a contagem medida).
- Defeito: a versão gerada pela IA nesta sessão usava `N = max(N_r, N_s)`
  (limitado por V(A)) para o par hash do bucket-join. Na escala 1, B = 4096,
  `curso` passaria de ~2 para 34–50 blocos e `aluno` de ~1 962 para ~5 735
  blocos, quase todos com um bucket meio vazio. O custo do bucket-join é
  `b_r + b_s`, então esses blocos entrariam direto na medição.
- Detecção: conta de blocos feita ao escrever o cartão D-P6-01 (antes do commit),
  comparando as opções com os volumes de `results/base_stats.csv`.
- Correção: `N = min(N_r, N_s)`, limitado por V(A) do lado NOT UNIQUE. Total
  estimado no par aluno ⋈ inscricao cai de ~11 470 para ~7 360 blocos.
- Regressão: `test_plan_and_catalog_adapter` confere N e seed iguais no par e
  N ≤ V(A). A confirmação física depende da carga real (#14).
- Status da decisão: provisória (D-P6-01), a rever com o integrante.

## Exceção de operador derrubava a rodada inteira — corrigido (08/11, #11)

- Arquivo: `bench/run.py`, `measure` (versão bruta em `ai/P6/raw/run_v1.py`).
- Categoria: robustez do orquestrador.
- Defeito: só `NotApplicable` era tratada; qualquer outro erro de um operador
  de P4/P5 (ex.: `IndexError` por offset fora do bloco) propagava e encerrava
  a matriz, perdendo horas da rodada final.
- Detecção: autorrevisão do protocolo contra P6 §6.4 (rodada longa que pode cair).
- Correção: grava `status=error` com o tipo e a mensagem e segue; o resultado
  parcial não é gravado como medida. Se sobrar página fixada, aborta (`PinLeak`).
- Regressão: `tests/test_run.py::test_operator_bug_is_recorded_not_fatal_unless_pins_leak`.
