# Estado da parte P3 — atualizado em 28/11/2026

## Pronto

- Branch local `p3/44-btree-bulk-loading` criada sobre `baae948`; comparação pelo conector GitHub confirmou que esse commit coincidia com `main` no início da sessão.
- `docs/decisoes/P3.md`: D-P3-01 (§3.4.4: bulk loading; somente leitura após construção como limitação adicional), resumo, premissas de P1/P2, referências e pendências. É documentação, não prova de implementação.
- D-P3-02 registrada: §3.4.1, um campo da tabela por B+ primária UNIQUE/NOT UNIQUE.
- D-P3-03 registrada: primária NOT UNIQUE com desempate interno. Tamanho/geração/codificação do identificador definidos depois em D-P3-05; referência pública escolhida depois em D-P3-04. Primária UNIQUE e agrupamento do secundário não recebem essa obrigação.
- `docs/implementacao/P3_PLANO_ATUAL.md`: especificação operacional parcial para implementação incremental, com formatos aprovados, comportamentos, invariantes, entregas e lacunas L01–L05. Separada do guia histórico desatualizado; não é código pronto.
- Guia P3 com aviso do escopo revisto e resumo atualizado; `docs/estudo/P3.md` com explicação e perguntas de fixação.

- D-P3-04 registrada: referência física bloco + slot, 6 B no formato de P2, para primária UNIQUE e NOT UNIQUE; codificação e fetch isolados implementados, integração pública pendente.
- Notion: mapa editável com 23 itens em cinco grupos, estados coloridos e implementação separada; base collection://4269ab02-1b6d-81f0-a41a-000bb0ca0dda.

- D-P3-05: contador global por árvore desde 1, u32 de 4 B big-endian, somente primária NOT UNIQUE. Persistir IDs nas entradas; sem contador de próximas inserções. Layout e implementação pendentes.

- D-P3-06 registrada: layout compacto dos internos comum aos três usos; k por perfil, fan-out calculado, sem irmãos/pai/slots. Codec de página e roteamento isolados agora implementados; árvore persistente pendente. Layout amplo continua parcial.

- D-P3-07 registrada: folhas primárias com SlottedPage/codecP2; UNIQUE só registro, NOT UNIQUE ID 4 B + registro. Sem cópia extra da chave; limites B−14/B−18. Codec de entradas implementado; builder e consultas públicas pendentes.

- D-P3-08 registrada: folha secundária com chave/quantidade total/início bloco-slot e auxiliares SlottedPage compartilhadas/encadeadas. Quantidade delimita grupos, não páginas. Largura/codificação fechadas por D-P3-09: u32 LE de 4 B; secundário sem implementação.

## Em andamento

- Issue #44: layout e decisões §3.4. Método de carga (§3.4.4) e campo único (§3.4.1) registrados; critérios de layout/implementação ainda não concluídos.

## Falta

- Decidir conteúdo/validação do bloco 1, árvore vazia e ocupação/preenchimento. L01 fechada em D-P3-09: quantidade total secundária u32 LE de 4 B. Organização das listas auxiliares definida em D-P3-08. Internos/fan-out máximo D-P3-06 têm testes isolados; não há builder multinível.
- Alinhar rejeição de manutenção com P2 e retirada da variante incremental com P6. Nenhum desses ajustes foi realizado.
- Implementar `BTreeFile` e `BTreeIndex` com os acessos necessários, persistência, estatísticas e testes.
- Atualizar critérios das issues afetadas com autorização de Gabriel antes de qualquer escrita no GitHub.

## Requisitos do enunciado cobertos

| Enunciado § | Onde está | Teste/prova |
| :--- | :--- | :--- |
| §3.4.4 e justificativa §7.2.2.a | `docs/decisoes/P3.md`, D-P3-01 | Escolha documentada; bulk loading ainda não implementado |
| §3.4.1 | `docs/decisoes/P3.md`, D-P3-02 | Codec usa um campo; não há árvore pública funcional |
| §3 / parte de §3.4.2: duplicatas da primária | D-P3-03/05/07 e `btree_node.py` | `test_btree_node.py` prova prefixo e comparação; `test_btree_partial.py` preserva duplicatas em folhas de teste |
| Parte de §3.4.2: internos/fan-out | D-P3-06 e `_InternalPage` | Bytes literais k4/8/12, nós cheios e roteamento; construção permanece pendente |
| Referências físicas e parte dos acessos §5 | D-P3-04, `_fetch_primary`, `_descend` | Fetch frio = 1 folha; altura3 = 2 internos + 1 folha nos testes isolados |
| §3.4.3, §4.2 e acessos públicos §5 | Pendentes | Sem alegação de construção, secundário ou integração completos |
| §6.1.1 manutenção | Limitação do recorte registrada em D-P3-01 | Sem alegação de manutenção dinâmica implementada |

## Desvios ou dúvidas sobre o contrato

- D-P3-04: alinhar P4 fetch_cost (access.py:112–114), ainda calculado pela altura para BTREE apesar de a referência física acessar diretamente a página. Comentário Ptr em contracts.py ainda prevê chave/seq; não foram alterados consumidores nesta sessão.

- Guias/contratos e algumas issues ainda pressupõem insert/delete dinâmicos. D-P3-01 é decisão de P3; ainda falta alinhamento/alteração de consumidores. Não fingir conformidade integral com a API de mutação.
- P2 já oferece SlottedPage com HEADER=10 e sem USER, chaves INT=4 B/FLOAT=8 B e RID heap/hash=6 B. Não recuperar layouts antigos sem nova decisão.

## Decisões provisórias a rever

- Nenhuma nova escolha técnica provisória. D-P3-01 a D-P3-09 têm escolhas aprovadas; L01 fechada; detalhes pendentes estão enumerados nos cartões e não devem ser preenchidos silenciosamente.

## Testes e validação — histórico documental

- Nenhuma implementação B+ foi entregue nesta sessão. Não usar este documento para afirmar que a B+ já funciona.
- `git diff --check`: sem erros. Links relativos dos documentos novos conferidos.
- Revisor independente, somente leitura, conferiu as premissas atribuídas a P1/P2 contra cartões e código e não apontou divergências.
- `python -X utf8 -m pytest -q` foi iniciado como referência, mas interrompido sem resumo final: a suíte inclui estresse de um milhão de operações de buffer. Não há resultado completo aprovado nesta sessão; a entrega altera apenas documentação.

## Primeira implementação parcial — 28/11/2026

- Autorizada por Gabriel: componentes aprovados e esqueletos para lacunas conhecidas, sem escolher resultados/formatos pendentes. Nenhum código de P1/P2/P4/P6 modificado.
- Arquivos `cbd/files/btree_node.py` e `btree_core.py`: internos, codec de entradas, RID, descida por contexto explícito e fetch físico. Interfaces públicas em `btree_file.py` e `btree_index.py` permanecem esqueletos, exceto codificação do RID.
- Primeiras versões completas preservadas em `ai/P3/raw/*_v1.py` antes de refinamentos. Não editar raw.
- Quatro focos de revisão independente: corretude, contratos, recursos e testes. Sem bugs concretos nos componentes; lacunas de cobertura corrigidas e registradas em review.md.
- NÃO afirmar que colegas já podem usar a B+. `create/open` falham em L02 antes de I/O; `test_access_with_real_btree` agora é ativado pelo módulo e falha nessa dependência. Nenhum teste antigo alterado.
- Validação final desta entrega será registrada abaixo; sem commit/push automático.

### Resultado desta entrega parcial

- `python -m pytest -q tests/test_btree_node.py tests/test_btree_partial.py`: **46 passaram**. Testes de bytes/formato, componentes com P1/P2 reais e falha explícita dos esqueletos; não provam uma árvore pública utilizável.
- `python -X utf8 -m pytest -q -m 'not stress'`: **758 passaram, 3 falharam, 1 ignorado, 57 desmarcados**. Falha B+ esperada em `test_access_with_real_btree` por create/L02. Duas falhas em `test_report.py`: parser rejeita os cartões P3, cujos títulos/status já estavam fora de seu formato no HEAD anterior à implementação. Documentação dos cartões e relatório não alterados nesta entrega.
- Primeira execução sem `-X utf8` também revelou falhas por leitura cp1252 de fontes UTF-8; repetir em modo UTF-8 removeu esses erros sem mudar arquivos de colegas. Usar esse modo nas próximas verificações em Windows.
- Suíte completa iniciada, mas interrompida sem resumo final; não declarar que o estresse completo passou. Esta entrega parcial não implementa construção, manutenção ou concorrência de iteradores.
- Revisão independente e simulação de regressão em nós cheios concluídas; `git diff --check` e fronteiras de camada conferidos. Nenhum commit/push.

## D-P3-09 — Contador secundário aprovado, 28/11/2026

- Quantidade total do grupo: u32 little-endian, 4 B, 1…2³²−1, sem wrap. Entrada K+10 B; máximos físicos em B4096: 227 para K4, 185 para K8. Não é preenchimento nem medição. Formato dos nós/folhas decidido; bloco1 segue item separado.
- Site de P2 confirma LE nos controles e explica BE nas chaves. Não foi encontrada justificativa comparativa para LE ou ganho nativo; não atribuir isso a Guilherme. Livro fundamenta os grupos externos, não endianness/contador.
- Documentação e plano atualizados; código, testes e raw da entrega parcial não alterados. Novas alterações ficam somente na árvore de trabalho, sem stage/commit. Commit da implementação anterior fica a cargo de Gabriel.

## Sincronização do mapa Notion — 28/11/2026

- Após D-P3-09, formato dos nós/folhas e grupos secundários passaram de Parcial para Aprovada. O item de formato indica Base disponível (componentes isolados), não árvore completa. Removidas pendências de largura também do item de grupos maiores que uma página.
- Metadados: raiz, altura e primeira folha (Bloco1) continuam Pendentes no grupo4; corpo desse registro foi alinhado à convenção de localização já aceita. Nenhuma decisão nova de metadados tomada. Próxima prioridade proposta: L02.
