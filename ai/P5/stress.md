# Ensaios e comparação de versões — P5

## Pilha real, 27/11/2026

Seed 480, bloco de 2.048 B, uma linha com CHAR(1200) por bloco. O mestre
tem 300 linhas únicas, o detalhe tem 400 linhas com a mesma chave.
Ambos os lados de cada operação excedem ou igualam 50×M em blocos.
Oráculo SQLite por multiconjunto, zero pins ao final e ausência de
temporários órfãos. Execução em `test_skew_50_times_work_against_sqlite`.

| Versão | M | Registros do construtor retidos por lote | Leituras | Gravações |
| :--- | ---: | :--- | ---: | ---: |
| Hash ingênuo | 3 | 400, pico observado na versão bruta | 700 | 0 |
| GRACE limitado | 3 | teto 1 na única página reservada, derivado da blocagem e validado em teste de fronteira | 14.515 | 1.714 |
| Sort-merge, dois sorts | 6 | orçamento de 3 páginas por sort, sem acumular grupo na fusão | 5.710 | 5.300 |

O GRACE retorna os mesmos 400 pares, usa três níveis e entra em laço por
skew total. Ao impor o limite de memória, faz mais I/O que a versão que
mantém toda a tabela na RAM. Os valores acima são do simulador real com
heap e temporários. M=3 não admite dois sorts concorrentes do P4. O teste
de caminho misto com um cursor B+ simulado verifica o único sort real
com M=3 em ambas as orientações, sem usar esse dublê em medições.

Os custos controlados com relações de 180 e 360 blocos estão em
[`variantes.md`](variantes.md). A rodada tiny usou o gerador/catálogo da
bancada e confirmou 2, 100 e 1.000 pares por join para os dois operadores.

## Situação em 23/11/2026

**N5 ainda não executado.** Os ensaios abaixo são N1 com dublês, não medições
físicas, nem a bateria SQLite/50×M da #69. A versão ingênua que carrega o build
inteiro e a comparação exigida pela #69 continuam pendentes.

## Regressão de memória — versão bruta × corrigida

Caso reproduzido em processo Python em 23/11: M=4, bfr=1, seis registros gerados
sob demanda no construtor; lote reservado de dois registros; uma linha no probe.
Tuplas acompanhadas por sua vida útil permitem observar referências retidas.

| Versão | Registros do construtor vivos ao abrir cada probe | Resultado | Objetos restantes | Reserva restante |
| :--- | :--- | :--- | :--- | :--- |
| `ai/P5/raw/hash_join_grace_v1.py`, `_block_join` | [2,3,3] | [(0,0,0,100)] | 0 | 0 |
| `cbd/exec/hash_join.py`, `_block_join` após R-P5-01 | [2,2,2] | [(0,0,0,100)] | 0 | 0 |

O teste de regressão é `test_block_join_releases_previous_chunk_records`.
O resultado coincide nas duas versões; o defeito é exceder o orçamento entre
lotes. Não há número de transferência de blocos atribuído a esse ensaio.

## Execuções da suíte isolada até a revisão

| Etapa | Resultado real |
| :--- | :--- |
| Versão inicial + teste da referência retida | 1 failed, 27 passed in 0.23s |
| Correção R-P5-01 + testes iniciais do custo | 33 passed in 0.18s |
| Casos dos revisores antes de corrigir bordas de vazio/bfr | 14 failed, 42 passed in 0.53s |

Skew total e M=3 constam dos testes N1; isso não equivale ao estresse N5 com a
pilha real. As rodadas finais e a revisão das correções serão registradas no
fechamento da sessão.

## Testes de mutação N1 — lacunas indicadas pelo revisor

Cada mutação foi aplicada apenas em memória, em um processo separado, sem editar
arquivos de produção ou snapshots. O revisor relatou que os três mutantes
passavam nos 33 testes iniciais. Os novos testes detectaram todos:

| Mutação | Teste detector | Resultado deliberadamente falho |
| :--- | :--- | :--- |
| Duplicar bfr somente na construção, dobrando a capacidade lógica | `test_underestimated_build_boundary` (quinto registro versus capacidade quatro) | 1 failed, 57 deselected in 0.18s |
| Forçar todas as colunas de chave para posição zero | `test_join_columns_at_different_positions` | 4 failed, 54 deselected in 0.17s |
| Forçar auto a escolher r sempre | `test_auto_chooses_in_memory_s` | 1 failed, 57 deselected in 0.11s |

Não são resultados de estresse físico nem falhas introduzidas no arquivo final.

## Fechamento das regressões N1 — 23/11/2026

| Verificação | Resultado real |
| :--- | :--- |
| Após corrigir vazio/esparso, antes de ampliar a cobertura final | 58 passed in 0.13s |
| Hash join + relatório com novas bordas e documentação | 82 passed, 1 skipped in 0.37s |
| Suíte completa antes das últimas fronteiras fracionárias | 202 passed, 3 skipped in 38.50s |
| Hash join final, teto exato e um registro além | 73 passed in 0.21s |
| Suíte completa final | 209 passed, 3 skipped in 35.91s |

Mutação adicional somente em memória: substituir floor por ceil foi detectado
pelos três excessos fracionários (**3 failed, 1 passed, 65 deselected in 0.15s**
na primeira ampliação). O revisor de testes repetiu a verificação após incluir o
teto exato e confirmou a detecção. Não houve edição da produção para esses
mutantes, nem alteração nos snapshots.

O revisor de corretude relatou 400 casos aleatórios adicionais corretos com
dublês; não há script N5 persistido desse ensaio e ele não substitui a #69.

SHA-256 dos snapshots preservados nesta sessão:

- `hash_join_grace_v1.py`: `62E15FB13ED7D7163AA46F09B91CF3A119301B9BD29C1CFAB7A73C4C0D067FB4`
- `hash_join_cost_v1.py`: `4571507162CCD7614B73A0ADD895BFD65DD68918CF1A668DA9B90E62CA8464C7`

## Preparação da #65 — 24/11/2026

- Baseline da #61: **209 passed, 3 skipped in 38.46s**.
- Worktree `data/worktrees/p5-65`, somente dublê HashFile e testes adicionais da política N/seed incompatíveis: **78 passed in 0.25s** no arquivo isolado; **214 passed, 3 skipped in 38.36s** na suíte inteira.
- Esses testes ainda executam o núcleo GRACE da #61. Não provam reaproveitamento físico, bucket-join sem escritas reais ou as economias de prepartitioned; variantes aguardam escolhas adicionais.
- Snapshots copiados mantêm os hashes acima. Nenhuma medição física ou N5 acrescentada.
- Depois de tratar R-P5-08: **80 passed in 0.23s** isolados e **216 passed, 3 skipped in 36.58s** na suíte completa. Mutante de precedência foi detectado: **2 failed, 1 passed, 77 deselected in 0.15s**, somente em memória.
- Revisor confirmou que a alteração preserva o caso original e cobre N e seed distintos; sete novos casos passaram em sua verificação direcionada.
