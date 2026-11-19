# Metodologia

## Carga de trabalho

- **Junções:** os três NATURAL JOINs mestre-detalhe, um por combinação de tamanho
  do enunciado (§5.1.2): curso $\bowtie$ disciplina (menor $\bowtie$ menor), curso $\bowtie$ aluno
  (menor $\bowtie$ maior) e aluno $\bowtie$ inscricao (maior $\bowtie$ maior). O mestre é sempre `r`.
- **Seleções:** 20 igualdades em `aluno.dre` (campo UNIQUE), com valores sorteados
  com seed fixa; e duas faixas em `aluno.cr` (campo NOT UNIQUE), `cr ≥ q` com meta
  de 1% e `cr ≤ q` com meta de 50%, em que `q` é o quantil calculado nos dados
  carregados. A seletividade real fica registrada em cada linha.

## Configurações físicas

Cada junção roda nas configurações a–e do enunciado (§5.1), com os três
algoritmos. Nas configurações b e d, o lado organizado (B+ ou hash) é medido nas
duas orientações, no mestre e no detalhe. Na configuração c, há B+ com B+, hash
com hash com número de buckets e seed diferentes, e hash com hash com os mesmos
parâmetros (bucket-join). As seleções passam pelos caminhos a (varredura), b
(organização primária B+ ou hash na coluna) e c (índice secundário), e na faixa
também pelo índice B+ secundário sobre os arquivos organizados por B+ e por hash
(enunciado §4.2). No índice secundário, a faixa é medida com e sem a ordenação
dos ponteiros por bloco.

## Configuração-base e fatores

Todas as linhas usam a mesma configuração-base, e cada fator é variado um de cada
vez (enunciado §6): bloco de 4096 bytes, buffer com as áreas CATALOG = 8,
INDEX = 64, DATA = 128 e WORK = 64 páginas, toss-immediate desligado, sem leitura
antecipada e carga em massa. A política de substituição de cada operação é a que
teve menos I/O no experimento de escolha da política.

> **A preencher (#16, #19):** política escolhida por operação e os níveis de cada
> fator (tamanho de bloco, toss-immediate, leitura antecipada, WORK).

## Protocolo de uma medição

1. O custo estimado (`*_cost` de P4/P5) é calculado **antes** da janela medida.
2. `eng.cold()` grava as páginas sujas, esvazia o buffer e zera os contadores.
3. O operador roda e **toda** a saída é consumida dentro da janela (os operadores
   são preguiçosos: medir só a criação daria zero I/O). O laço calcula a contagem
   e o checksum da saída, o que custa CPU, mas não I/O.
4. `eng.snapshot()` lê os contadores de blocos e do buffer.
5. Ao fim, nenhuma página pode continuar fixada; se sobrar, a rodada aborta.

Estatísticas das tabelas e a validação contra a referência ficam fora da janela.
Um operador que recusa uma combinação gera uma linha `N/A` com o motivo; uma
exceção no operador ou na função de custo gera uma linha `error`, e a rodada
continua.

## Repetições

Cada experimento roda no mínimo 3 e no máximo 10 vezes, até a variação do tempo
ficar abaixo de 5% (`max/min − 1`). Os contadores de blocos e do buffer têm de
ser **idênticos** entre as repetições: com a mesma base e o cache frio, a
contagem é determinística, e qualquer diferença indica um defeito (no
esvaziamento do buffer, na política ou na leitura antecipada). Uma repetição
divergente é marcada como não determinística e bloqueia o relatório. O resumo
usa a mediana das repetições.

## Validação

Cada resultado é comparado com a referência calculada no SQLite sobre os mesmos
dados: número de linhas e checksum da saída (soma, módulo 2^64, do hash
determinístico da chave lógica de cada linha, que não depende da ordem). Uma
linha inválida bloqueia o relatório até o dono do operador corrigir.

**Limitação:** o checksum cobre só as chaves lógicas. Um join que devolve as
chaves certas com outra coluna errada passaria.

## Reprodutibilidade

Cada linha do CSV bruto registra a base usada (12 hexadecimais do SHA-256 do
`workload.json`), e o resumo nunca mistura bases. Uma rodada interrompida é
retomada sem repetir as medições já gravadas.
