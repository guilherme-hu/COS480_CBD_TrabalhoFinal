# Opções de projeto

O enunciado (§7.2.2.a) pede a justificativa de **todas** as opções adotadas, com
a comparação com as preteridas. Cada parte registra as decisões em cartões
(`docs/decisoes/Px.md`), no formato: contexto, solução dos livros, opções
consideradas com o custo em blocos, escolha, impactos e efeito nas medições.
Este capítulo resume esses cartões por camada.

> **A preencher (#25):** consolidar a tabela "Resumo das decisões" de cada parte
> quando ela for fechada (issues de fechamento de P1 a P6).

## Gerente de arquivos e buffer pool (P1)

Tamanho de bloco, cabeçalho do arquivo, alocação, contagem sequencial ×
aleatória, tamanho e áreas do buffer, políticas de substituição, toss-immediate,
leitura antecipada e gravação forçada (enunciado §2.1 e §2.2).

## Registros, heap, hash e catálogo (P2)

Formato do registro e dos campos variáveis, nulos e listas, slotted page,
blocagem, função de hash, número de buckets, fator de carga e overflow
(enunciado §3.1, §3.2 e §3.5).

## Árvore B+ (P3)

Chave e comparação, ordem (fan-out) dos nós, ocupação mínima, split, fusão e
redistribuição, carga em massa e o índice NOT UNIQUE sem repetição de chave nas
folhas (enunciado §3.4 e §4.2).

## Seleção, ordenação externa e nested-loop (P4)

Caminhos de acesso, fórmulas de custo, ordenação dos ponteiros no índice
secundário, tamanho dos runs e fan-in da ordenação, escolha do laço externo
(enunciado §5).

## Sort-merge e hash join (P5)

Divisão da memória nas fusões, plano de partições, recursão, tratamento de skew
e aproveitamento das organizações hash e B+ (enunciado §5.1).

## Bancada de teste (P6)

Volumes da base, combinações de tamanho, carga de trabalho, número de buckets
dos pares do bucket-join (D-P6-01), protocolo de medição e regra de repetições.
