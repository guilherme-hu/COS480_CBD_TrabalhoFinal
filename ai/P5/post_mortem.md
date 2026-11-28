# Post-mortem — P5 (Enzo de Carvalho Sampaio)

## O que foi construído

Partition-hash join, GRACE recursivo, fallback em blocos, reaproveitamento
de HashFile nas variantes prepartitioned e bucket-join, sort-merge PK→FK e
estimativas de custo pelos mesmos planos. Integração com heap, HashFile,
codec, TempHeap, WORK, adaptador de acesso e external sort reais.

## Prompts-chave e código bruto

1. Implementar as issues atribuídas, começando pelo GRACE. A lógica N1 está
   em [hash_join_grace_v1.py](raw/hash_join_grace_v1.py), e a primeira
   estimativa em [hash_join_cost_v1.py](raw/hash_join_cost_v1.py).
2. Seguir o aceite da #65 e usar GRACE com HASH incompatíveis. O primeiro
   reaproveitamento integrado está em [hash_join_primary_v1.py](raw/hash_join_primary_v1.py).
3. Completar P5 com decisões delegadas, livros e estudo para a prova.
   Primeira fusão em [sort_merge_v1.py](raw/sort_merge_v1.py) e comparação de
   memória em [hash_join_ingenuo.py](raw/hash_join_ingenuo.py).

## Defeitos da IA

| Defeito | Categoria | Como foi achado | Correção | Regressão |
| :--- | :--- | :--- | :--- | :--- |
| Referência do laço retinha registro antigo entre lotes | Memória | N1 observou [2,3,3] objetos em limite de dois | Limpar referências antes de carregar lotes, com função de sondagem isolada | `test_block_join_releases_previous_chunk_records` |
| Custo supunha scans para vazio e bfr zero era rejeitado com bloco alocado | Corretude e modelo | Revisor comparou resultado e estimador | Reconhecer cardinalidade zero no plano comum | `test_cost_and_operator_empty_input` |
| Limite zero em tabela esparsa com 0<bfr<1 | Corretude | Revisão e M=3 | Piso de um registro por página mais limite em bytes | `test_sparse_fractional_bfr` |
| Cartões ausentes no índice do relatório | Documentação | Dois testes de relatório falharam | Citar escolhas P5 e regenerar índice | `test_report.py` |
| Contabilização de registro no assert | Memória/execução | Autorrevisão da primeira variante integrada | Executar a contabilização também em Python otimizado | Testes de bytes e skew |
| Prefixo incorreto para detectar temporário órfão | Qualidade de teste | Conferência da fixture com o catálogo real | Usar __temp e comparar o diretório no abandono | Fixture de integração e `test_abandon_real_temp_files` |

Revisores também detectaram lacunas de teste: limite chão/teto, ordem de
escolha in-memory, orientação física e sort-one. [review.md](review.md)
registra reproduções e confirmação após ampliar a cobertura.

## Refatorações e por quê

O GRACE inicial confiava apenas no bfr do dublê. O integrado contabiliza
bytes codificados e slots, lê totais correntes do arquivo e mantém o teto
por registros. Par alinhado que excede WORK reinicia antes de emitir
resultados, usando só o laço em blocos. Fusões e sondagens fecham cursores
em vez de depender de coleta automática.

## Evidência de estresse

Na pilha real, com M=3, skew total e 400 registros no construtor, a versão
ingênua excede WORK em 399 registros e o GRACE cabe com lotes de um.
Ambas conferiram com SQLite. A ingênua lê 700 blocos e não escreve.
GRACE lê 14.515 e escreve 1.714. Essa diferença é o preço de respeitar o
limite de memória neste caso, não um ganho em contagem de blocos.
Ver [stress.md](stress.md) para o sort e cenários restantes.

## Estimado × medido

GRACE uniforme, 1.668 estimados e 1.620 medidos, diferença −2,88%.
A folga de 48 transferências da fórmula não se concretizou porque já há
um registro por página. Sort-merge, 2.700 estimados e 2.700 medidos.
Bucket-join controlado, 22 leituras e zero escritas em ambos. As fórmulas
não preveem skew extremo, cache ou término antecipado.

## Decisões que mudaríamos

Desde o primeiro rascunho, testar no limite N−1/N/N+1 com registro variável,
e em ambos os lados físicos. O tamanho médio é útil para o plano, mas
insuficiente para o orçamento. Mediria também a economia de reaproveitar
um hash incompatível, após mudança explícita do aceite em outra etapa.

## Lições sobre usar IA

Oráculos que validam só a saída não detectam retenção de referências e
contagem incorreta. Fotografar a primeira versão, dar nomes aos defeitos e
testar mutações ajudou a provar o valor de cada regressão. O trabalho
restante é integrar a B+ da #49, medir as configurações físicas que
dependem dela e executar a suite após futura integração no main.
