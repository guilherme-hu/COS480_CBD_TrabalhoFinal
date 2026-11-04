# P6 — Camada 1: Bancada de Teste, Validação, Resultados e Relatório Técnico

> Leia junto com [`00_ARQUITETURA_GERAL.md`](00_ARQUITETURA_GERAL.md). Você é o **cliente de todas as interfaces**: o §7 e o §8 do geral são a sua API. Aqui está o *como* implementar.

**Peso:** ~17%. Você **consome** tudo: `Engine` (que você mesmo escreve), `Catalog`/`Table` (P2/P3), operadores e funções `*_cost` (P4/P5) e contadores (P1). Você **fornece** a base, a matriz de experimentos, os CSVs, os gráficos e o relatório técnico.

**Mudança em relação ao plano original:** as **fórmulas de custo** ficam com quem implementa cada algoritmo (`*_cost` no P4/P5; o P3 fornece `expected_height`). Você não escreve fórmulas: chama as funções, coloca estimado ao lado de medido e analisa a diferença. Isso equilibra a carga e evita fórmula escrita por quem não conhece o algoritmo.

**Marcos:** até **09/11**: `engine.py`, `schema.py`, gerador e referência SQLite. Até **16/11**: matriz completa rodando ponta a ponta em escala pequena. **18/11**: rodada final. **20/11**: relatório técnico.

---

## Em resumo (leia primeiro)

- **Você monta o laboratório e escreve o relatório técnico.** Seu código usa o de todo mundo.
- **`Engine`:** a peça que liga as camadas (disco + buffer + catálogo) e permite **zerar o cache e os contadores** antes de cada medição.
- **Gerador da base:** cria os dados da universidade (alunos, cursos, turmas…) sempre iguais (determinístico), com tamanhos escolhidos para ter tabelas "pequenas" (cabem na memória) e "grandes" (não cabem).
- **Versões físicas:** a mesma tabela é guardada de vários jeitos (heap, B+ por uma coluna, hash por uma coluna, com índices) para comparar as configurações a–e do enunciado.
- **Matriz de experimentos:** todas as combinações de tipo de join × tamanho × algoritmo × configuração × política de buffer, mais as seleções. Cada combinação roda pelo menos 3 vezes.
- **Medição:** cache frio, consumir o resultado inteiro e anotar os contadores. **Validação:** o mesmo resultado tem que sair no SQLite.
- **Estimado × medido:** você **não escreve** as fórmulas, que vêm do P4/P5. Você coloca a fórmula ao lado da medida e explica as diferenças.
- **Saídas:** CSVs, gráficos e o relatório técnico com as recomendações finais ("para tal carga, use tal configuração").
- **Ambiguidade do enunciado a perguntar ao professor:** está no §3.

---

## 1. O que você entrega

| Arquivo | Conteúdo |
| :--- | :--- |
| `cbd/engine.py` | `Engine`, exatamente como no geral §8.1 |
| `bench/schema.py` | os 6 esquemas do geral §8.3 + chaves primárias lógicas (`PK = {"aluno": ("dre",), "inscricao": ("dre", "turma"), …}`) |
| `bench/generator.py` | geração determinística dos registros (§2) |
| `bench/build.py` | monta as bases físicas (variantes de organização e índices) por tamanho de bloco (§3) |
| `bench/sqlite_ref.py` | carrega os mesmos dados no SQLite e calcula os resultados de referência (§5) |
| `bench/matrix.py` | lista de experimentos (§4) |
| `bench/run.py` | orquestrador: mede, valida e grava o CSV (§6). CLI: `python -m bench.run --scale 0.1 --only joins --policies LRU,MRU` |
| `bench/compare_cost.py` | tabela estimado × medido, com razão e motivo das diferenças (§7) |
| `bench/plots.py` | gráficos (`matplotlib`) em `results/figs/` |
| `tests/integration/` | testes ponta a ponta em escala minúscula (§8) |
| `docs/relatorio/` | relatório técnico (fonte) e PDF final (§9) |

---

## 2. Gerador (`generator.py`)

### 2.1 Regras

- `random.Random(seed)` com seed fixa por tabela (`seed = 480 + i`). A mesma `scale` gera **exatamente** os mesmos registros. Teste: hash do conteúdo igual em duas execuções.
- Funções **geradoras** (`yield`), que não montam listas com milhões de tuplas. Exceção: o `Catalog.load_table` com BTREE ordena (é preparação).
- **Ordem física aleatória:** gere os registros em ordem **embaralhada** de chave (ex.: DREs sequenciais, mas emitidos numa permutação), para o heap não ficar "ordenado por acaso" pela chave. Se não, um scan em heap com parada antecipada e o hash join parecem melhores do que são.
- Os valores seguem os tipos do geral §8.3 (DATE = `datetime.date`, LIST = `tuple`).

### 2.2 Volumes (`scale = 1.0`) e distribuições

Calibre com `B = 4096` e `M = 64`. A tabela **pequena** cabe em `M − 2 = 62` blocos; a **grande** passa de `capacity(DATA) = 128` blocos com folga (≥ 10×).

| Tabela | n (scale 1) | Distribuições importantes |
| :--- | :--- | :--- |
| curso | 50 | `codigo` 1..50 |
| disciplina | 1 000 | `curso` uniforme em 1..50 |
| turma | 10 000 | `professor` uniforme em 1..2 000 (≈ 5 turmas/professor) |
| aluno | 50 000 | `dre` 1..n (emitidos embaralhados); `cpf` único, 11 dígitos; `curso` Zipf leve em 1..50; `cr` ~ Normal(6.5, 1.8) truncada em [0, 10] com 2 casas (NOT UNIQUE, ~1000 valores); `orientador` uniforme em 1..2 000, 30% nulo |
| inscricao | 500 000 | `dre` uniforme em alunos (≈ 10/aluno); `turma` uniforme |
| detalhes | 50 000 | `cpf` = cpf dos alunos (embaralhado); LIST com 0–8 itens |

Para cada tabela, **imprima e grave** `n`, `b` (blocos), `bfr` e `V(A)` das colunas usadas (`results/base_stats.csv`) e confira os tamanhos: pequena ≤ 62 blocos, grande ≥ 1 280. Ajuste os volumes se o `bfr` real fugir muito do esperado.

Comece com `--scale 0.1` para desenvolver. Meça o tempo de 1 join grande e escolha a escala final para que a matriz inteira rode em **≤ 4 h** (§6.4).

### 2.3 Subconjuntos para as combinações de tamanho

O enunciado (§5.1.2) pede *menor ⋈ menor*, *menor ⋈ maior* e *maior ⋈ maior* para **cada** tipo de join. Crie tabelas derivadas (permitido pelo §6.4 do enunciado):

| Tipo de join | menor ⋈ menor | menor ⋈ maior | maior ⋈ maior |
| :--- | :--- | :--- | :--- |
| 1. NATURAL PK→FK (`codigo` = `curso`, `dre`) | curso ⋈ disciplina | curso ⋈ aluno | aluno ⋈ inscricao |
| 2. UNIQUE (`cpf`) | aluno_p ⋈ detalhes_p | aluno_p ⋈ detalhes | aluno ⋈ detalhes |
| 3. NOT UNIQUE (`orientador` = `professor`) | aluno_p ⋈ turma_p | aluno_p ⋈ turma | aluno ⋈ turma |

- `aluno_p`: alunos de **2 cursos** (≤ 62 blocos); `detalhes_p`: detalhes dos CPFs de `aluno_p`; `turma_p`: turmas dos professores que aparecem em `aluno_p`, cortadas para ≤ 62 blocos.
- Confirme por `stats()` que cada "menor" tem `b ≤ M − 2` e registre isso na tabela do relatório.

---

## 3. Bases físicas (`build.py`)

Uma pasta por tamanho de bloco: `data/db_2k`, `data/db_4k`, `data/db_8k`. O tamanho de bloco é a única coisa que exige gerar de novo. Em cada uma, crie as **variantes** de cada tabela:

| Nome da variante | `OrgSpec` | Índices secundários (`IndexSpec`) |
| :--- | :--- | :--- |
| `<t>__heap` | HEAP | em cada coluna de join/seleção da tabela: UNIQUE → HASH; NOT UNIQUE → BTREE (geral §5.3) |
| `<t>__bt_<col>` | BTREE em `col` (unique conforme a coluna) | nas **outras** colunas de join: BTREE NOT UNIQUE (só NOT UNIQUE é permitido sobre B+, geral §5.3) |
| `<t>__hs_<col>` | HASH em `col` | idem |

As colunas por tabela vêm dos joins e das seleções:

| Tabela | Colunas |
| :--- | :--- |
| aluno, aluno_p | `dre` (U), `cpf` (U), `curso` (NU), `orientador` (NU), `cr` (NU) |
| inscricao | `dre` (NU) |
| curso | `codigo` (U) |
| disciplina | `curso` (NU) |
| detalhes, detalhes_p | `cpf` (U) |
| turma, turma_p | `professor` (NU) |

**Por que isso basta:** o caminho de acesso é **escolhido explicitamente** (`Path`). Com `Path.SCAN`, o operador ignora os índices que existirem. Uma variante heap com todos os índices serve para as configurações a, d (lado secundário) e e. Só "estar organizado pela coluna" exige uma variante física própria.

**Ambiguidade do enunciado a registrar:** os tipos 2 e 3 dizem "campos que **não** ditam a organização primária", mas as configurações b–d pedem "um dos campos é o da organização primária". Interpretamos assim: nos tipos 2 e 3 a coluna **não é a PK** da tabela (CPF não é a PK de aluno; `orientador` não é PK), e nas configurações b–d o arquivo é organizado por ela mesmo assim. **Pergunte ao professor** e cite a resposta no relatório.

Variantes HASH: passe `OrgSpec.params["expected_records"] = n` (ou `buckets` explícito) para o P2 dimensionar N. Para o *bucket-join* do P5, crie um par de variantes com `buckets` e `seed` **iguais** (ex.: `aluno__hs_cpf_eq` e `detalhes__hs_cpf_eq`).

Carga: `Catalog.load_table(..., bulk=True)` para a base de medição. Para o experimento de carga (§4.4), crie também `aluno__bt_dre_inc` com `bulk=False`.

---

## 4. Matriz de experimentos (`matrix.py`)

`Experiment` é um `dataclass` com: `exp_id`, `kind` ("select"/"join"), `op` (função), `cost_fn`, `args` (dict), e as colunas descritivas (`join_type`, `size_combo`, `config`, `algorithm`, `org_r`, `org_s`, `sel_type`, `selectivity`, `path`), `block_size`, `policy`, `areas`, `toss`, `prefetch`.

### 4.1 Configuração-base (o enunciado §6 pede um fator por vez)

`B = 4096`, `areas = {CATALOG: 8, INDEX: 64, DATA: 128, WORK: 64}`, `policy = LRU`, `toss_immediate = False`, `prefetch = 0`, carga em bulk.

### 4.2 Joins (núcleo da avaliação)

Produto completo de:

- **tipo** (3) × **tamanho** (3) × **algoritmo** (`nl_join`, `sort_merge_join`, `hash_join`) × **configuração** a–e × **política** (as 2 escolhidas);
- para b, c e d, a organização do lado PRIMARY varia em {BTREE, HASH} (2 variantes). Configuração c com HASH nos dois: rode **duas vezes**, com N e seed iguais (`bucket-join` do P5) e diferentes.

Mapa configuração → `JoinSide` (geral §7.4), com `r` = 1ª tabela do par e `s` = 2ª:

| Config | r: variante, path | s: variante, path |
| :--- | :--- | :--- |
| a | `__heap`, SCAN | `__heap`, SCAN |
| b | `__bt_col`/`__hs_col`, PRIMARY | `__heap`, SCAN |
| c | `__bt_col`/`__hs_col`, PRIMARY | `__bt_col`/`__hs_col`, PRIMARY |
| d | `__bt_col`/`__hs_col`, PRIMARY | `__heap`, SECONDARY |
| e | `__heap`, SECONDARY | `__heap`, SECONDARY |

Algumas combinações não existem (ex.: `curso.codigo` só tem 50 valores; ou um caminho que dá `NotApplicable`). Gere a linha mesmo assim, com `status = "N/A"` e o motivo.

**Experimento de buffer a incluir obrigatoriamente** (P4 §6.2): BNL com `b_inner` um pouco maior que `capacity(DATA)` (ex.: `DATA = b_inner − 4`), comparando LRU × MRU (× Clock). É onde a política faz diferença; nos demais casos LRU e Clock tendem a empatar.

### 4.3 Seleções

| Tipo | Coluna | Valores |
| :--- | :--- | :--- |
| igualdade UNIQUE | `aluno.dre` | 20 DREs sorteados (relate a média) |
| faixa UNIQUE | `aluno.dre` (e `aluno.cpf`) | `dre ≤ q`, com `q` = quantil 0,1% e 50% calculado nos **dados reais** |
| faixa NOT UNIQUE | `aluno.cr` | `cr ≥ q` (0,1% → CR alto, "acima de 9"); `cr ≤ q` (50%) |

× caminhos a/b/c (SCAN em `__heap`; PRIMARY em `__bt_col` e `__hs_col`; SECONDARY em `__heap`) × 2 seletividades × 2 políticas. Para SECONDARY, rode com `sort_ptrs` False e True. Passe `info={"sel": sel_real}` para o P4 colocar o `n` real na fórmula.

### 4.4 Fatores extras (um de cada vez, sobre a base, num subconjunto representativo)

| Fator | Níveis | Experimentos |
| :--- | :--- | :--- |
| tamanho de bloco | 2048, 4096, 8192 | joins tipo 1 (os 3 tamanhos) × 3 algoritmos, config a e c(BTREE) |
| toss-immediate | off/on | os scans e o BNL |
| prefetch | 0, 8 | os scans (o efeito aparece em `io.seeks`) |
| memória (`WORK`) | 16, 64, 256 | sort-merge e hash join grandes (passadas, recursão) |
| bulk × incremental | `bulk=True/False` | ocupação e altura da B+ e tempo de carga (indicador §6.1.1) |
| carga de manutenção | +10% inserts, −10% deletes aleatórios via `Table` | `stats()` de dados e índices antes e depois (indicador §6.1.1) |

---

## 5. Validação com SQLite (`sqlite_ref.py`)

- Um arquivo `data/ref.sqlite` por `scale`, com as mesmas tabelas (incluindo os subconjuntos). LIST vira texto JSON; DATE vira texto ISO.
- Para cada consulta (seleção ou join), a referência calcula `(count, checksum)`:
  - **checksum** = soma, módulo 2⁶⁴, de `hash_value(chave_lógica_da_tupla)`, que independe da ordem. A chave lógica é a PK de `r` para seleção e `(PK de r, PK de s)` para join. Use o `hash_value` do P2.
  - Calcule o mesmo na saída do operador (a tupla de saída é `r_rec + s_rec`; extraia as PKs pelos índices dos esquemas).
- Valide **uma vez** por `(consulta, variante física, algoritmo)`, fora da medição (a 1ª repetição pode validar depois de medir).
- `valid` no CSV: `True`/`False`. Qualquer `False` bloqueia o relatório: abra uma issue para o dono do operador.

---

## 6. Orquestrador (`run.py`)

### 6.1 Protocolo de medição (uma execução)

```python
eng = engines.get((block_size, bcfg))           # reabre o Engine quando a BufferConfig muda
args = resolve(exp, eng)                        # JoinSide/Table reais a partir dos nomes
try:
    est = exp.cost_fn(eng, **args)              # ANTES de medir (não mexe nos contadores... confira)
except NotApplicable as e:
    write_row(exp, status="N/A", note=str(e)); continue
eng.cold()                                      # cache frio + contadores zerados
info = {}
t0 = time.perf_counter()
n, chk = 0, 0
for tup in exp.op(eng, **args, info=info):      # consumir TUDO
    n += 1
    chk = (chk + hash_value(pk_of(tup))) & MASK
dt = time.perf_counter() - t0
snap = eng.snapshot()
assert eng.bp.pinned() == 0
write_row(exp, rep, n, chk, snap, est, info, dt)
```

- Se a função `*_cost` ler páginas (ela só deveria ler `stats()`), o `cold()` depois dela já garante a medição limpa. Mesmo assim, avise o dono se `cost_fn` mexer nos contadores.
- O checksum no laço custa CPU, mas não I/O. O tempo de relógio é só informativo (o enunciado §6.1.2 diz que ele não substitui a contagem).

### 6.2 Repetições (enunciado §6)

No mínimo **3** repetições, até a variação entre elas ficar abaixo de 5%: `max/min − 1 < 0.05` em `io.reads + io.writes` **e** no tempo, com no máximo 10. As contagens são determinísticas (mesma base, cache frio), então a variação vem só do tempo. Grave todas as repetições, e o resumo usa a mediana.

### 6.3 CSV (`results/raw/<data>_<parte>.csv`)

Colunas, nesta ordem:

```
exp_id, rep, status, note, kind, join_type, size_combo, config, algorithm, variant,
sel_type, selectivity, path, sort_ptrs,
r_table, r_org, r_rows, r_blocks, s_table, s_org, s_rows, s_blocks,
block_size, policy, area_catalog, area_index, area_data, area_work, toss, prefetch,
result_rows, checksum, valid,
io.reads, io.writes, io.seeks, io.seq_reads, io.rand_reads, io.seq_writes, io.rand_writes,
bp.requests, bp.hits, bp.misses, bp.hit_rate, bp.dirty_evictions, bp.forced_writes, bp.prefetched,
bp.DATA.hits, bp.DATA.misses, bp.INDEX.hits, bp.INDEX.misses, bp.WORK.hits, bp.WORK.misses,
est.reads, est.writes, est.seeks, est.formula, info_json, time_s
```

`results/summary.csv`: uma linha por `exp_id` com as medianas. `results/occupancy.csv`: `stats()` de cada arquivo e índice antes e depois da carga de manutenção.

### 6.4 Tempo de execução

O Python é lento: meça antes. Opções, nesta ordem:

1. reduzir `scale` (mantendo pequena ≤ M − 2 e grande ≥ 10·`DATA`);
2. paralelizar por processo (`multiprocessing`), **uma cópia da pasta da base por processo** (os temporários e o catálogo gravam);
3. rodar as repetições 2 e 3 só da contagem se o tempo da 1ª já estiver estável (justifique).

`--resume`: pule os `exp_id`/`rep` que já estão no CSV, porque a rodada final vai cair no meio.

---

## 7. Estimado × medido (`compare_cost.py`)

Para cada `exp_id`: `ratio = medido / estimado` (leituras e gravações separadas), com uma coluna **motivo** preenchida por regra:

| Situação | Motivo esperado |
| :--- | :--- |
| `ratio < 0.9` e `bp.DATA.hits` alto | reaproveitamento do buffer (ex.: interno do BNL cabe em `DATA`) |
| `writes` medido < estimado com temporários | páginas temporárias descartadas sem ir ao disco (P2 §5.4) |
| seleção por faixa com `ratio` longe de 1 | distribuição não uniforme (estimativa por min/max) |
| B+ com altura medida ≠ `expected_height` | ocupação real ≠ fill (bulk × incremental) |
| `variant` = `skew_fallback` ou recursão inesperada | skew de chaves |

Linhas que nenhuma regra explique vão para a seção do relatório "diferenças não explicadas" e geram uma conversa com o dono do operador. Pode ser bug.

---

## 8. Testes de integração (`tests/integration/`)

- `scale = 0.002`, `B = 2048`, `M = 8`: a matriz completa (todas as linhas) roda, **todas** as `valid` saem `True` e `pinned() == 0`.
- Reabrir a base com outra política funciona sem gerar de novo.
- O gerador é determinístico (hash do conteúdo).
- `run.py --resume` não duplica linhas.

Esses testes são a rede de segurança do grupo: rode-os em todo PR que chega ao `main` a partir de 14/11.

---

## 9. Relatório técnico (enunciado §7.2.2)

`docs/relatorio/` (LaTeX ou Markdown → PDF). **Folha de rosto com nome completo e DRE de todos** (enunciado §7.3; a tabela está no `README.md`).

Estrutura:

1. **Introdução:** a pergunta do trabalho e o resumo das conclusões.
2. **Opções de projeto (item a):** colete as tabelas "Decisões" de cada parte, prontas nos arquivos P1 §2.1/§3.1, P2 §2.1/§5.1/§6.1, P3 §2 e P4/P5 (variantes): tamanhos de bloco, blocagem, registro e cabeçalhos, campos de ordenação, função de hash, ordem (fan-out) das árvores, buffer. Mais a descrição da base: esquema físico, volumes, `b`, `bfr`, `V(A)`, subconjuntos e a interpretação das ambiguidades do enunciado.
3. **Metodologia:** configuração-base, fatores, protocolo de medição, repetições, validação SQLite.
4. **Resultados (item b):** tabelas e gráficos com **medido ao lado do estimado** (`est.formula`). Mínimo:
   - por tipo de join: barras agrupadas, algoritmo × configuração a–e, uma figura por combinação de tamanho;
   - LRU × MRU/Clock no BNL com *flooding*;
   - seleções: caminho × seletividade;
   - memória (`WORK`) × passadas/recursão;
   - tamanho de bloco;
   - ocupação antes e depois da manutenção, bulk × incremental.
5. **Conclusão (item c):** comparação geral e **tabela de recomendações**: "para tal carga, use tal organização primária + tais índices + tal configuração de buffer". Exemplos de linha: seleção pontual frequente por chave; faixas largas em NOT UNIQUE; join PK-FK grande; tabela pequena × grande; memória escassa.

Os gráficos saem de `bench/plots.py` lendo `results/summary.csv`. Nenhum número é digitado à mão.

---

## 10. Armadilhas (suas e da IA). Procure ativamente

- Medir **sem** `eng.cold()` (cache quente da repetição anterior).
- Não consumir o iterador inteiro (o operador é lazy; medir só a criação dá ~0 I/O).
- Validação, checksum do SQLite ou `analyze()` **dentro** da janela medida.
- Gerador não determinístico (`random` global, `set` iterado, `hash()` do Python).
- Heap gerado em ordem de chave: resultados de scan bons demais.
- "Tabela menor" que não cabe em `M − 2` blocos: a combinação de tamanhos fica errada sem ninguém perceber. Confira pelo `stats()`.
- Experimentos com fatores misturados (mudar a política **e** o bloco ao mesmo tempo).
- Matriz que pula combinações em silêncio. N/A tem que aparecer no CSV com o motivo.

Registre o uso de IA em `ai/P6/` (geral §11). O código bruto do orquestrador gerado pela IA, com os erros acima, é ótimo material para o post-mortem.
