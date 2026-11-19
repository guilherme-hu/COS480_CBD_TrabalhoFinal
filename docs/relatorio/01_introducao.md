# Introdução

O trabalho responde à pergunta do enunciado: **qual configuração do SBD é mais
adequada para processar uma dada carga de trabalho sobre uma determinada base de
dados?** A resposta vem de medições, não de estimativas: o resultado principal é
o número de blocos transferidos entre a memória secundária e o buffer pool em
cada operação, ao lado do custo estimado pelas fórmulas dos livros
(Silberschatz–Korth–Sudarshan e Elmasri–Navathe).

Para isso o grupo construiu, com auxílio de IA, um SGBD didático em quatro
camadas, em que cada camada usa só a interface da camada de baixo:

1. **Bancada de teste:** gera a base, monta as configurações físicas, roda a
   matriz de experimentos e grava os resultados em CSV.
2. **Mini monitor de execução:** seleções, ordenação externa e os três
   algoritmos de junção (nested-loop em blocos e indexado, sort-merge e
   partition-hash).
3. **Gerente de armazenamento:** registros, slotted page, heap, hash estático,
   árvore B+, índices secundários e catálogo.
4. **Gerente de arquivos e de buffer pool:** disco simulado em blocos, contadores
   de leituras e gravações e políticas de substituição.

O escopo segue a parte obrigatória do enunciado: as cinco organizações
primárias, os métodos de acesso do §4.2, o NATURAL JOIN mestre-detalhe
(curso $\bowtie$ disciplina, curso $\bowtie$ aluno e aluno $\bowtie$ inscricao) com os três algoritmos e
as configurações a–e, a seleção por igualdade em campo UNIQUE (`aluno.dre`) e por
faixa em campo NOT UNIQUE (`aluno.cr`) com seletividades de 1% e 50%. Os itens
opcionais (equijoin em UNIQUE e em NOT UNIQUE, faixa em UNIQUE) ficaram de fora.

**Organização do relatório.** O capítulo 2 justifica as opções de projeto de cada
camada, comparando com as preteridas. O capítulo 3 descreve a base e a carga de
trabalho. O capítulo 4 descreve a metodologia de medição. O capítulo 5 traz os
resultados, sempre com o medido ao lado do estimado, e o capítulo 6, a análise e
as recomendações de configuração física.

> **A preencher (#26):** resumo das conclusões, depois da rodada final.
