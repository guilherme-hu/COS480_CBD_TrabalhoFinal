# Estresse — P4

## Reprodução

Na raiz do repositório, execute `python -m pytest -q -s -m stress`.
No Windows, a execução desta sessão usou `PYTHONUTF8=1`, redirecionamento
da entrada padrão e um `--basetemp` novo dentro de `.pytest_cache`.

`tests/stress/test_p4_stress.py` usa `random.Random(48064)`, `M = 3` e
blocos de 2.048 bytes (2.020 úteis antes dos slots). As duas tabelas têm
150 páginas previstas, 50 vezes `M`. São 450 cursos com PK distinta e
600 disciplinas cuja FK aponta para um único curso. O tamanho dos
registros foi montado para comportar três cursos ou quatro disciplinas
por página. O SQLite verifica as tuplas inteiras e a assinatura lógica
do join, além da sequência ordenada da disciplina.

## Ingênuo, primeira versão e versão revisada

As três versões fizeram o mesmo join `curso ⋈ disciplina` com `curso`
externo. O resultado foi **600 tuplas** e checksum **14220684193072682664**
nas três versões, com zero pins no dublê ao final.

| Versão | Pico observado de registros externos | Bytes codificados e slots | Passadas no interno | Leituras estimadas sem reuso | Medição física |
| :--- | ---: | ---: | ---: | ---: | :--- |
| Controle ingênuo [`nested_loop_unbounded_baseline.py`](raw/nested_loop_unbounded_baseline.py) | 450 | 242.550 | 1 | 300 | Pendente |
| Primeiro BNL histórico [`nested_loop_v1.py`](raw/nested_loop_v1.py) | 3 por lote | 1.617 por lote | 150 | 22.650 | Pendente |
| BNL revisado | 3 por lote | 1.617 por lote | 150 | 22.650 | Pendente |

O controle ingênuo foi criado para reproduzir o problema pedido na #64.
A versão histórica também foi executada e já respeitava o orçamento nesse
cenário. Comparam-se os registros retidos no lote, além da linha de entrada
mantida pelo cursor. O controle ingênuo ocupou **150 vezes** o tamanho do
lote revisado e excedeu a única página WORK disponível para o externo. O
controle varre as duas tabelas uma vez ao gastar memória fora do orçamento.
O revisado lê o externo uma vez e varre o interno por lote. O modelo de SKS
§15.5 dá `150 + 150 × 150 = 22.650` leituras sob ausência de reuso.

Tempos diagnósticos de uma execução com dublê e coleta da saída em memória:
6,63 ms (controle), 12,38 ms (histórico) e 12,41 ms (revisado). As versões
em blocos incluem a instrumentação do lote. A comparação de desempenho em
disco será feita na pilha real.

Com `disciplina` externa, todas as entradas do lote têm a mesma chave.
O BNL revisado devolveu as mesmas 600 tuplas em 150 passadas, mantendo no
máximo **4 registros por lote**, ou 1.852 bytes, com os cursores fechados.

O estresse adicional `test_p4_historical_bnl_extra_pass` executou o bruto
histórico e o revisado com `M = 64`, 501 registros em 62 blocos nominais e
um bloco interno. O produto `62 × (501/62)` em ponto flutuante derrubava o
teto de 501 para 500. O histórico fez **duas passadas** e o revisado fez
**uma**. As quatro tuplas bateram com SQLite nas duas versões. Sem reuso,
as passadas observadas correspondem a 64 e 63 leituras, respectivamente.
O estimador retornou 63 para ambas. O modelo também passou a considerar
o teto inteiro por registros no caso que exigia quatro passadas onde a
fórmula clássica previa três (11 vs. 9 leituras). Ver os testes
`test_block_cost_accounts_for_rounded_record_limit` e
`test_block_limit_does_not_round_down_twice`.

## Ordenação e estimado × medido

A ordem das 600 disciplinas bateu com o SQLite pela FK concentrada e pela
PK embaralhada. Na primeira execução o teste confere a estabilidade dos
empates. A ordenação observou **75 runs iniciais**, seis fusões
(`75 → 38 → 19 → 10 → 5 → 3 → saída`), pico de três páginas WORK no dublê e
todos os temporários liberados. Durante toda a ingestão, o teste permite
no máximo nove registros consumidos e ainda sem gravação (oito no lote
e um na entrada). O primeiro registro de saída chega com os três runs
finais ativos.

O estimador de SKS §15.4, adaptado à fusão final sem saída materializada,
calcula `150 × 7 = 1.050` leituras e `150 × 6 = 900` gravações. Páginas
temporárias ainda presentes no buffer podem virar hits ou ser descartadas
antes de uma gravação. Com tamanhos variáveis, a reordenação também pode
mudar a blocagem. A medição física e a diferença numérica aguardam P1/P2.

A rodada local de estresse teve **5 aprovações e 2 pulados**. Na tentativa
isolada com a camada 4 do PR #2 carregada, a integração parou em
`cbd.files.catalog`. Os testes físicos preparam cache frio por execução e
comparam as mesmas tuplas com SQLite. Veja [pendências externas](PENDENCIAS_EXTERNAS.md).
