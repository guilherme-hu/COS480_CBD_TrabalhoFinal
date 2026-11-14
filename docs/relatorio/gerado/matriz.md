## Composição da matriz

Contada a partir de `bench/matrix.py`.

| Configuração | Linhas de junção |
| :--- | ---: |
| a | 9 |
| b | 36 |
| c | 27 |
| d | 36 |
| e | 9 |

Total: 117 linhas de junção (3 combinações de tamanho × 3 algoritmos × 13 configurações físicas), mais 4 com o lado externo ou construtor forçado.


| Seleção | Configuração | Linhas |
| :--- | :--- | ---: |
| eq_unique | a | 20 |
| eq_unique | b | 40 |
| eq_unique | c | 20 |
| range_nonunique | a | 2 |
| range_nonunique | b | 4 |
| range_nonunique | c | 12 |

Total: 98 linhas de seleção (20 igualdades em `dre` e 2 faixas em `cr`).


| Fator único | Linhas |
| :--- | ---: |
| tamanho de bloco | 54 |
| toss-immediate | 6 |
| prefetch | 4 |
| memória WORK | 6 |

Total: 70 linhas de fator único, cada uma igual a uma linha da matriz principal com um só fator mudado.
