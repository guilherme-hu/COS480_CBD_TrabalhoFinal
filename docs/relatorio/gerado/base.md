## Volumes e cardinalidades

Gerado de `results/base_stats.csv`. Tamanho médio do registro em bytes, no formato do P2. V(A) só nas colunas usadas em junções e seleções, sem contar nulos.

| Tabela | n | Registro (B) | V(A) |
| :--- | ---: | ---: | :--- |
| curso | 50 | 132,5 | codigo = 50 |
| disciplina | 1.000 | 80,2 | curso = 50 |
| turma | 10.000 | 38,0 | — |
| aluno | 50.000 | 129,7 | cr = 922, curso = 50, dre = 50.000 |
| inscricao | 500.000 | 24,0 | dre = 49.997 |
| detalhes | 50.000 | 111,8 | — |

## Blocos e fator de blocagem estimados

Empacotamento heap estimado com o codec do P2 e a slotted page (cabeçalho de 10 bytes e slot de 4 bytes), enchendo cada bloco na ordem de carga, como o `HeapFile.bulk_load`. **Não são medidas do SGBD**: as medidas físicas entram quando a carga pela pilha real existir (#14).

| Tabela | b (B = 2048) | b (B = 4096) | b (B = 8192) | bfr (B = 2048) | bfr (B = 4096) | bfr (B = 8192) |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| curso | 4 | 2 | 1 | 12,5 | 25,0 | 50,0 |
| disciplina | 43 | 21 | 11 | 23,3 | 47,6 | 90,9 |
| turma | 209 | 104 | 52 | 47,8 | 96,2 | 192,3 |
| aluno | 3.378 | 1.664 | 825 | 14,8 | 30,0 | 60,6 |
| inscricao | 6.945 | 3.449 | 1.713 | 72,0 | 145,0 | 291,9 |
| detalhes | 2.950 | 1.443 | 715 | 16,9 | 34,7 | 69,9 |

## Adequação das combinações de tamanho

Critério (P6 §2.3): a tabela menor cabe em M − 2 blocos e a maior tem pelo menos 10 × DATA blocos. As áreas do buffer têm a mesma memória em bytes em todos os tamanhos de bloco (D-P6-02), então M e DATA, em páginas, dependem de B:

| B | WORK = M | DATA | menor ≤ | maior ≥ |
| ---: | ---: | ---: | ---: | ---: |
| 2048 | 128 | 256 | 126 | 2.560 |
| 4096 | 64 | 128 | 62 | 1.280 |
| 8192 | 32 | 64 | 30 | 640 |

| Combinação | B | Situação |
| :--- | ---: | :--- |
| small_small | 2048 | ok |
| small_large | 2048 | ok |
| large_large | 2048 | ok |
| small_small | 4096 | ok |
| small_large | 4096 | ok |
| large_large | 4096 | ok |
| small_small | 8192 | ok |
| small_large | 8192 | ok |
| large_large | 8192 | ok |
