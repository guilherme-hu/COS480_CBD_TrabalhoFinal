## Volumes e cardinalidades

Gerado de `results/base_stats.csv`. Tamanho médio do registro em bytes, no formato do P2. V(A) só nas colunas usadas em junções e seleções, sem contar nulos.

| Tabela | n | Registro (B) | V(A) |
| :--- | ---: | ---: | :--- |
| curso | 50 | 156,2 | codigo = 50 |
| disciplina | 1.000 | 103,2 | curso = 50 |
| turma | 100.000 | 51,0 | — |
| aluno | 50.000 | 152,4 | cr = 922, curso = 50, dre = 50.000 |
| inscricao | 500.000 | 33,0 | dre = 49.999 |
| detalhes | 50.000 | 120,8 | — |

## Blocos e fator de blocagem estimados

Empacotamento heap estimado com o layout do P2 (cabeçalho de 28 bytes e slot de 4 bytes). **Não são medidas do SGBD**: as medidas físicas entram quando a carga pela pilha real existir (#14).

| Tabela | b (B = 2048) | b (B = 4096) | b (B = 8192) | bfr (B = 2048) | bfr (B = 4096) | bfr (B = 8192) |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| curso | 5 | 2 | 1 | 10,0 | 25,0 | 50,0 |
| disciplina | 55 | 27 | 14 | 18,2 | 37,0 | 71,4 |
| turma | 2.778 | 1.370 | 676 | 36,0 | 73,0 | 147,9 |
| aluno | 4.037 | 1.962 | 968 | 12,4 | 25,5 | 51,7 |
| inscricao | 9.260 | 4.588 | 2.273 | 54,0 | 109,0 | 220,0 |
| detalhes | 3.216 | 1.565 | 772 | 15,5 | 31,9 | 64,8 |

## Adequação das combinações de tamanho

Critério (P6 §2.3): a tabela menor cabe em M − 2 = 62 blocos e a maior tem pelo menos 10 × DATA = 1280 blocos (WORK = 64, DATA = 128).

| Combinação | B | Situação |
| :--- | ---: | :--- |
| small_small | 2048 | ok |
| small_large | 2048 | ok |
| large_large | 2048 | ok |
| small_small | 4096 | ok |
| small_large | 4096 | ok |
| large_large | 4096 | ok |
| small_small | 8192 | ok |
| small_large | 8192 | aluno: 968 blocos, esperado >= 1280 |
| large_large | 8192 | aluno: 968 blocos, esperado >= 1280 |
