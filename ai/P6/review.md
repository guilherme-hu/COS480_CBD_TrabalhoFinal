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

## Achados da revisão do P2 nos PRs #28–#35 (09/11)

Revisão feita pelo guilherme-hu (P2) com IA, comentada em cada PR. Todos os
achados abaixo foram reproduzidos antes da correção (teste que falhava).

### `base_stats.csv` versionado ainda listava os subconjuntos (#28/#5)

- Arquivo: `results/base_stats.csv`.
- Categoria: artefato desatualizado.
- Defeito: a #5 tirou `aluno_p`, `detalhes_p` e `turma_p` do código, mas o CSV
  versionado não foi regenerado e seguia com 9 linhas deles.
- Correção: regenerado com os mesmos argumentos (`--scale 1 --rows turma=100000`);
  os SHA-256 dos dados são idênticos, só saem os subconjuntos e os `V(A)` de
  colunas fora do escopo.

### Índice secundário "clustering" sobre B+ e hash sem medição (#32/#10)

- Arquivo: `bench/matrix.py`, `_select_paths`.
- Categoria: aderência ao enunciado (requisito sem medição).
- Defeito: o caminho c (SECONDARY) só existia sobre heap. O enunciado §4.2 lista
  o B+ secundário em campo NOT UNIQUE também para B+ e hash primários, e o plano
  físico já cria esse índice em `aluno.cr` sobre `aluno__bt_dre` e `aluno__hs_dre`.
- Correção: a faixa em `cr` ganha `c_secondary_on_btree` e `c_secondary_on_hash`,
  com e sem `sort_ptrs` (18 linhas de faixa em vez de 10).
- Regressão: `test_select_matrix_counts` e `test_select_values_come_from_workload`.

### Exceção no `cost_fn` derrubava a rodada (#33/#11)

- Arquivo: `bench/run.py`, `measure`.
- Categoria: robustez do orquestrador (mesma família do achado da #11 acima).
- Defeito: a correção anterior tratou exceções do operador, mas não as do
  `cost_fn`; um `KeyError` na função de custo chegava ao `main`. O mesmo valia
  para `expected[exp.query_id]` com um id ausente do `workload.json`.
- Correção: as duas viram `status=error` e a rodada segue.
- Regressão: `test_cost_fn_bug_is_recorded_like_operator_bug` e
  `test_unknown_query_id_is_an_error_row_and_run_continues`.

### `summary.csv` e `--resume` misturavam bases diferentes (#35/#13)

- Arquivo: `bench/run.py`, `summarize`, `run` e `main`.
- Categoria: metodologia experimental (resultado errado sem aviso).
- Defeito: o resumo lia todo CSV de `results/raw/`, e com o mesmo `exp_id` em
  dois arquivos vencia o de nome maior. Nenhuma coluna registrava a base:
  `2026-11-09_all.csv` (`io.reads=9999`) e `tiny_all.csv` (`io.reads=7`)
  davam `io.reads = 7` no resumo. O `--resume` com outro `--data` misturava
  repetições de bases diferentes.
- Correção: coluna `dataset` (12 primeiros hex do SHA-256 do `workload.json`)
  em toda linha; `run` recusa `previous` de outra base e `summarize` só lê a
  base pedida (ou recusa se houver mais de uma).

### Regra dos 5% aceitava contagem de I/O diferente entre repetições (#35/#13)

- Arquivo: `bench/run.py`, `stable`.
- Categoria: metodologia experimental (esconde bug de contagem).
- Defeito: `stable` aceitava até 5% de variação em `io.reads + io.writes`
  (`1000, 1020, 1040` passava), e a mediana escondia a diferença. Com cache
  frio e a mesma base a contagem é determinística (P6 §6.2): se varia, há bug
  em `cold()`, na política ou no prefetch. O guia descrevia a regra assim e o
  código seguiu o guia sem questionar.
- Correção: contadores `io.*`/`bp.*` idênticos entre repetições; os 5% valem só
  para o tempo. Divergência vira `status=nondeterministic` e bloqueia o relatório.
  Guia P6 §6.2 atualizado.

### `--resume` nunca refazia um experimento com `error` (#35/#13)

- Arquivo: `bench/run.py`, `run`.
- Categoria: correção.
- Defeito: `if reps[0]['status'] != 'ok': continue` pulava `N/A` (correto,
  é determinístico) e também `error`; depois de o dono corrigir o operador, o
  erro antigo ficava no resumo para sempre.
- Correção: só `N/A` é final. Se a última tentativa terminou em `error`,
  `nondeterministic` ou `valid=False`, o resume mede de novo do zero
  (repetições numeradas depois das existentes) e o resumo usa só a última tentativa.

### Linha truncada no CSV quebrava o `--resume` (#35/#13)

- Arquivo: `bench/run.py`, `read_rows`/`CsvLog`.
- Categoria: robustez (rodada que cai no meio da gravação).
- Defeito: com a última linha pela metade, o `DictReader` devolve `None` e
  `stable` levantava `TypeError`; o modo `'a'` ainda colaria a linha seguinte
  na metade que sobrou.
- Correção: no resume, confere o cabeçalho contra `COLUMNS` e descarta a última
  linha incompleta; `CsvLog` troca quebras de linha por espaço (uma linha do CSV
  é sempre uma linha do arquivo).

## `exp_id` de faixa com ponto quebrava a leitura das figuras (09/11, #23)

- Arquivo: `bench/plots.py`, `select_series` (bruto em `ai/P6/raw/plots_v1.py`).
- Categoria: correção (parsing).
- Defeito: o código gerado separava o `exp_id` com `split('.')`, mas o id da
  consulta de faixa tem ponto (`select.range_cr_0.01.a_scan`); o rótulo do caminho
  saía errado e a seletividade virava `0`.
- Detecção: `test_select_series_averages_the_20_dres` (KeyError em `'0.01'`).
- Correção: separa pelo último ponto (`rsplit('.', 1)`).

## Revisão independente da pilha #24/#22/#23/#6/#19/#3/#8 (12/11)

Três revisores (subagentes de contexto limpo, só leitura) com focos de
corretude, aderência ao contrato e qualidade dos testes. Cada achado abaixo foi
reproduzido pelo agente antes de ser aceito (testes do revisor em scratchpad:
8 falhando antes da correção; mutações aplicadas numa cópia: 18 de 21 sobreviviam).
Código bruto: `ai/P6/raw/compare_cost_v1.py`, `plots_v1.py`, `report_v1.py`,
`matrix_factors_v1.py`.

### Linha com resultado errado entrava no estimado × medido e nas figuras (#22/#23)
- Arquivos: `bench/compare_cost.py` (`compare`), `bench/plots.py` (`join_series`, `select_series`).
- Categoria: corretude (validação ignorada).
- Defeito: `summarize` grava a tentativa com `status='ok'` e `valid='False'`; os dois
  módulos filtravam só por `status`. Uma junção errada com `io.reads = est.reads`
  saía "dentro da tolerância" e virava barra normal no gráfico.
- Detecção: revisores de corretude e de contrato (P6 §5: `False` bloqueia o relatório).
- Correção: linha inválida não é comparada nem desenhada; vai para a lista de
  pendências com motivo "resultado inválido" e aparece como "INVÁLIDO" na figura.

### Motivo de uma métrica "explicava" a diferença da outra (#22)
- Arquivo: `bench/compare_cost.py` (`explained = within or bool(why)`).
- Categoria: corretude (regra larga demais).
- Defeito: bastava uma regra disparar para a linha inteira contar como explicada.
  Ex.: leituras 3× acima do estimado e gravações abaixo saíam como "páginas
  temporárias descartadas", fora da lista de não explicadas.
- Correção: motivos separados para leituras e gravações; a linha só é explicada se
  cada métrica fora da tolerância tiver o próprio motivo.

### Regra da faixa valia para o SCAN e a de recursão para qualquer recursão (#22)
- Arquivo: `bench/compare_cost.py` (`reasons`).
- Categoria: corretude (regra larga demais).
- Defeito: "distribuição não uniforme" explicava qualquer diferença na faixa, até no
  SCAN (custo `b_r`, que não depende da distribuição); `levels > 0` explicava toda
  recursão como skew, inclusive a esperada com WORK = 16 (o P6 §7 diz "recursão
  inesperada").
- Correção: a regra da faixa só vale em caminho por índice/organização; recursão só
  explica se o operador marcar `skew_fallback` ou `variant` com skew.

### Regra "altura da B+ ≠ esperada" lia chaves que nenhum contrato define (#22)
- Arquivo: `bench/compare_cost.py` (`info['height']`, `info['expected_height']`).
- Categoria: contrato (aderência).
- Defeito: nenhum operador de P4/P5 põe essas chaves em `info`; a regra nunca dispara.
- Correção: não corrigida aqui (depende de P3/P4). Registrada como pendência da #36.
  Enquanto isso, diferenças de B+ vão para "não explicadas" (lado conservador).

### Linha 'ok' sem `io.reads` derrubava a comparação (#22)
- Defeito: `close(None, x)` dava `TypeError`. Correção: vai para pendências como "sem medida".

### Erro de operador aparecia como "N/A" e N/A sumia nas seleções (#23)
- Arquivo: `bench/plots.py`.
- Defeito: `'N/A' if status != 'ok'` juntava `error`/`nondeterministic` com N/A
  legítimo; `select_series` descartava N/A e erro em silêncio (P6 §10).
- Correção: célula com o status (`N/A`, `ERRO`, `INVÁLIDO`) nas duas figuras.

### `areas_for_block` podia zerar áreas ou perder bytes (#6)
- Arquivo: `bench/matrix.py`. Defeito: divisão inteira; com B = 65536 CATALOG = 0.
- Correção: `ValueError` se o bloco não divide a memória da área.

### Testes que não provavam o que diziam (#19, #23, #6, #3)
- `test_each_factor_row_changes_only_its_factor` descartava a política e o teste do
  WORK usava a própria `areas_for_block` como esperado (remover o `work` passava).
- `test_one_command_writes_every_figure` só conferia que o PNG existe: barra com o
  estimado no lugar do medido passava.
- `test_size_checks_use_areas_of_each_block_size` nunca exercitava o WORK escalado.
- **Erro do agente nesta sessão (#3):** `test_signature_detects_swapped_duplicate_with_same_count`
  passava com XOR no lugar da soma, e `test_heap_order_is_not_key_order` aceitava
  uma ordem decrescente (tão agrupada quanto a crescente).
- Correção: testes reescritos com valores literais, comparação de todos os campos
  da linha e métrica de agrupamento.

### Afirmações erradas nos cartões (#3)
- **Erro do agente nesta sessão:** a D-P6-05 dizia que o XOR "não detecta duplicata".
  Com uma duplicata o XOR detecta; ele só ignora multiplicidade par. E, como o
  resultado esperado é um conjunto (PKs únicas), XOR + contagem também seria
  completo. A vantagem real da soma é valer para multiconjuntos. Cartão reescrito.
- D-P6-03 dava valores esperados da Zipf como se fossem da base (real: 6 192 e 441);
  D-P6-04 subestimava a lista (~18 GB com os ponteiros); D-P6-07 citava um teste que
  não confere o CPF. Corrigidos.

### Descartados
- "matplotlib ausente, `test_plots` pulado": falso; matplotlib 3.11 está no `.venv`
  e o teste roda (os pulados são o do Engine real e o do PDF).
- "Fronteira da faixa sem limite de magnitude": é questão de projeto, não bug.
  A regra agora só vale em caminho por índice; o limite de magnitude fica para
  debate com o integrante.
