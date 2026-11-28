# Variantes P5 na matriz (#68)

O hash join começa pelos atalhos `empty` e `in-memory`. A tabela abaixo
descreve os demais planos. Para o sort-merge, índice secundário HASH não
entrega ordem e B+ secundária entrega ordem com uma busca por ponteiro.

| Configuração | Sort-merge | Hash join, se o construtor não cabe |
| :--- | :--- | :--- |
| a. SCAN nos dois lados | sort-both | GRACE comum |
| b. PRIMARY em um lado | B+ ordena só o outro. HASH exige ambos os sorts. | PRIMARY HASH na chave dá prepartitioned. B+ dá GRACE. |
| c. PRIMARY nos dois lados | duas B+: ordered. B+ e HASH: sort-one. Dois HASH: sort-both. | Dois HASH com N/seed iguais: bucket-join. Incompatíveis: GRACE. Exatamente um HASH: prepartitioned. Duas B+: GRACE. |
| d. PRIMARY e SECONDARY | cada B+ oferece sua ordem. Cada HASH exige sort. | PRIMARY HASH: prepartitioned. PRIMARY B+: GRACE, inclusive com HASH secundário. |
| e. SECONDARY nos dois lados | cada B+ oferece ordem. HASH secundário exige sort. | GRACE. O hash join não pesquisa índices secundários. |

Em b/d, as duas orientações seguem a mesma regra. O par de saída continua
`r_rec+s_rec`. O CSV de bancada pode usar `variant`, `index_used`,
`sorted_r`, `sorted_s`, `levels`, `partitions`, `passes` e `runs`.

## Comparação no cenário controlado / 27 de novembro

2048 B, M=20, 180 e 360 blocos, um registro por bloco. Cache frio e nenhuma
gravação forçada da saída. Confirmação por multiconjunto SQLite.

| Operador | Leituras est. | Escritas est. | Leituras med. | Escritas med. | Diferença em transferências |
| :--- | ---: | ---: | ---: | ---: | ---: |
| GRACE, uma passada | 1.104 | 564 | 1.080 | 540 | −2,88% |
| Sort-merge, dois sorts | 1.620 | 1.080 | 1.620 | 1.080 | 0% |

Em outro cenário, N=19, M=16, 19 blocos do HASH e 39 do HEAP.
`prepartitioned` mediu 96 leituras e 38 gravações nas duas orientações.
Estimativas: (99, 41) para r construtor, (101, 43) para s. Um registro
do HEAP era NULL e não entrou nos temporários. A folga do modelo é
2 ou 4 blocos por leitura/escrita. Somente o HEAP gerou arquivos.

No GRACE, as 48 transferências de diferença são exatamente a folga `4n_h`
do modelo. Cada registro já ocupa uma página, então não houve
arredondamento de páginas parciais neste cenário. Em outras distribuições,
evicções, cache e recursão podem alterar o resultado.

As organizações reais utilizadas foram HEAP e HASH. Os testes B+ usam
cursores da interface P3 enquanto #49 impede a matriz física completa.

Na rodada exploratória da base universitária `scale=0.002`, B=2048 e M=6,
o ponto de entrada `bench.run.measure` executou 38 combinações P5 que não
dependem de B+. Todas tiveram `status=ok` e assinatura SQLite válida.
Foram 8 a, 12 b, 12 c e 6 d. Pela variante da bancada: 18 sort-both,
13 in-memory, 3 GRACE, 3 prepartitioned e 1 bucket-join. As combinações
que dependem de B+ não foram carregadas. O cenário reduzido valida
integração e resultado, enquanto a comparação controlada acima valida o I/O.
