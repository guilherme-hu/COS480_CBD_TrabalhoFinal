# P3 — Roteiro atual de implementação

Atualizado em 28/11/2026. **Roteiro parcial, não implementação nem especificação completa do formato em disco.** Mantido à medida que Gabriel aprova decisões. Não usar o guia antigo `P3_arvore_bplus.md` como referência técnica atual.

## Fontes e escolhas aprovadas

Requisitos: enunciado vigente; teoria: SKS/E&N; integração: código e interfaces verificados de P1/P2/P4. As justificativas completas estão em [decisões P3](../decisoes/P3.md).

| Caso | Construção e consulta | Representação aprovada / pendente |
| :--- | :--- | :--- |
| Primária UNIQUE | Bulk loading e leitura; um campo da tabela | Registros completos nas folhas; chave do campo única; recusar repetição. Referência física bloco + slot por D-P3-04; layout completo pendente |
| Primária NOT UNIQUE | Bulk loading e leitura; um campo da tabela | D-P3-03: ordem interna `(valor, identificador)`. Tamanho/geração/codificação do identificador pendentes; referência física por D-P3-04 |
| Secundária NOT UNIQUE | Bulk loading e leitura; uma coluna indexada | §4.2: uma entrada por chave na folha, associada a referências. Representação do grupo e grupos grandes pendentes |

D-P3-01 corresponde ao método de carga de §3.4.4; somente leitura é limitação adicional. D-P3-02 corresponde a §3.4.1. D-P3-03 deriva de §3 e do layout de §3.4.2; não impõe desempate ao secundário. D-P3-04 define RID físico bloco u32 + slot u16 (6 B, formato de P2) para a primária UNIQUE e NOT UNIQUE.

## Interfaces a fornecer

As classes ainda não existem. As chamadas abaixo são consumidores reais no [catálogo](../../cbd/files/catalog.py), não implementações prontas:

- `BTreeFile.create/open(bp, name, schema, spec)`; `bulk_load(recs)` recebe registros ordenados pelo campo. Fornecer `scan/search/range` com pares `(referência, registro)`, `fetch`, codificação da referência, `stats` e `close`.
- `BTreeIndex.create/open(bp, name, spec, key_field, data)`; `bulk_load(pairs)` recebe pares ordenados `(chave, referência)`. `search` devolve referências e `range` devolve `(chave, referência)`; fornecer `stats` e `close`.
- Os contratos atuais ainda incluem mutação/reorganização. Definir e alinhar a recusa antes de qualquer modificação parcial; o roteiro não altera contratos nem consumidores automaticamente.

## Base reutilizada

- P1: configuração adotada B=4096, buffer, pin/unpin, dirty, alocação e contagem física de I/O. Acesso aos dados sempre pelo buffer.
- P2: `RecordCodec`, `key_size/encode_key/decode_key`, `SlottedPage` para as folhas (cabeçalho 10 B, slots 4 B, `next`), codificação das referências heap/hash.
- Catálogo de P2 ordena a preparação e cria/reabre estruturas. P4 acessa os registros via interfaces, inclusive `data.fetch(ptr)` após consultar um secundário.
- Uma referência pública e uma chave interna têm funções distintas. Não fixar `(chave, identificador)` como referência apenas porque foi aprovado como desempate de ordenação.

## Sequência de decisões e implementação

| Etapa | O que falta fechar | Comportamento a implementar/verificar depois |
| :--- | :--- | :--- |
| Referência aprovada | D-P3-04: bloco + slot; implementar codificação e fetch | `scan/search` produzem referências que `fetch` recupera corretamente |
| Próxima decisão: identificador | Domínio, geração, tamanho, codificação e persistência | Ordem sem empates, recuperação após reabertura, erro em limites excedidos |
| Layout | Entradas de folhas, nós internos, separadores, metadados/tipos e árvore vazia | Criar, carregar uma folha, percorrer e reabrir usando páginas reais |
| Construção completa | Preenchimento/ocupação, últimos grupos e memória usada; calcular capacidades | Vários níveis e folhas encadeadas; nenhuma página excede B |
| Buscas | Convenções dos separadores e limites pelo campo original | Igualdade/faixas; duplicatas em várias folhas; limites inclusivos/exclusivos |
| Secundário | Grupos de referências e páginas auxiliares | Uma chave por entrada; todas as referências recuperadas, inclusive grupos grandes |
| Integração e métricas | Recusa de manutenção com P2; bancada bulk-only com P6; estatísticas; fetch_cost de P4 ainda pressupõe descida pela altura | Testes reais com consumidores, pins liberados, cache frio e custos explicados |

São marcos de implementação, não uma exigência de decorar todo o layout antes de começar. Entregas parciais devem declarar seus limites; métodos incompletos nunca devem aparentar sucesso. Não criar abstrações genéricas só para antecipar formatos que ainda não foram escolhidos.

## Pendências explícitas

Sem defaults novos de 8 B para identificador, 90% de carga, 50% de ocupação ou limiares de overflow. Sem decisão de reconstrução automática, recarga no mesmo objeto, nó interno compartilhado ou diferente por uso. Cada escolha será justificada antes do código correspondente.

## Validação planejada

Usar codec/páginas reais e buffer; testes de ordenação, duplicatas, igualdade/faixas, persistência, limites de entrada e liberação de pins. No secundário, conferir ausência de chave repetida nas folhas e recuperar todos os registros por suas referências. Não há testes de B+ executados nem implementação entregue por este documento.

## Acompanhamento editável

[Mapa de decisões no Notion](https://app.notion.com/p/4269ab021b6d81508de1d7317021236f): mesmos 23 itens da lista agrupada, com estado da decisão separado da implementação. Próxima discussão: representação do identificador da primária NOT UNIQUE.
