# Estado da parte P3 — atualizado em 28/11/2026

## Pronto

- Branch local `p3/44-btree-bulk-loading` criada sobre `baae948`; comparação pelo conector GitHub confirmou que esse commit coincidia com `main` no início da sessão.
- `docs/decisoes/P3.md`: D-P3-01 (§3.4.4: bulk loading; somente leitura após construção como limitação adicional), resumo, premissas de P1/P2, referências e pendências. É documentação, não prova de implementação.
- D-P3-02 registrada: §3.4.1, um campo da tabela por B+ primária UNIQUE/NOT UNIQUE.
- D-P3-03 registrada: primária NOT UNIQUE com desempate interno. Tamanho/geração/codificação do identificador definidos depois em D-P3-05; referência pública escolhida depois em D-P3-04. Primária UNIQUE e agrupamento do secundário não recebem essa obrigação.
- `docs/implementacao/P3_PLANO_ATUAL.md`: roteiro parcial atualizado pelas decisões aprovadas, separado do guia histórico desatualizado.
- Guia P3 com aviso do escopo revisto e resumo atualizado; `docs/estudo/P3.md` com explicação e perguntas de fixação.

- D-P3-04 registrada: referência física bloco + slot, 6 B no formato de P2, para primária UNIQUE e NOT UNIQUE; ainda sem implementação.
- Notion: mapa editável com 23 itens em cinco grupos, estados coloridos e implementação separada; base collection://4269ab02-1b6d-81f0-a41a-000bb0ca0dda.

- D-P3-05: contador global por árvore desde 1, u32 de 4 B big-endian, somente primária NOT UNIQUE. Persistir IDs nas entradas; sem contador de próximas inserções. Layout e implementação pendentes.

- D-P3-06 registrada: layout compacto dos internos comum aos três usos; k por perfil, fan-out calculado, sem irmãos/pai/slots. Código ainda inexistente. Layout amplo continua parcial.

- D-P3-07 registrada: folhas primárias com SlottedPage/codecP2; UNIQUE só registro, NOT UNIQUE ID 4 B + registro. Sem cópia extra da chave; limites B−14/B−18. Ainda sem implementação.

- D-P3-08 registrada: folha secundária com chave/quantidade total/início bloco-slot e auxiliares SlottedPage compartilhadas/encadeadas. Quantidade delimita grupos, não páginas. Largura/codificação dessa quantidade ainda pendente; sem implementação.

## Em andamento

- Issue #44: layout e decisões §3.4. Método de carga (§3.4.4) e campo único (§3.4.1) registrados; critérios de layout/implementação ainda não concluídos.

## Falta

- Decidir largura/codificação da quantidade total no secundário e conteúdo/validação do bloco 1 (próximas discussões), árvore vazia e ocupação/preenchimento. Organização das listas auxiliares definida em D-P3-08. Internos/fan-out máximo definidos em D-P3-06, ainda sem testes de implementação.
- Alinhar rejeição de manutenção com P2 e retirada da variante incremental com P6. Nenhum desses ajustes foi realizado.
- Implementar `BTreeFile` e `BTreeIndex` com os acessos necessários, persistência, estatísticas e testes.
- Atualizar critérios das issues afetadas com autorização de Gabriel antes de qualquer escrita no GitHub.

## Requisitos do enunciado cobertos

| Enunciado § | Onde está | Teste/prova |
| :--- | :--- | :--- |
| §3.4.4 e justificativa §7.2.2.a | `docs/decisoes/P3.md`, D-P3-01 | Escolha documentada; nenhum teste de B+ ainda |
| §3.4.1 | `docs/decisoes/P3.md`, D-P3-02 | Escolha documentada; nenhum teste de B+ ainda |
| §3 / parte de §3.4.2: duplicatas da primária | `docs/decisoes/P3.md`, D-P3-03 | Desempate escolhido; formato completo e testes ainda pendentes |
| §3.4.2–3, §4.2 e acessos §5 | Pendentes | Nenhum teste de B+ ainda |
| §6.1.1 manutenção | Limitação do recorte registrada em D-P3-01 | Sem alegação de manutenção dinâmica implementada |

## Desvios ou dúvidas sobre o contrato

- D-P3-04: alinhar P4 fetch_cost (access.py:112–114), ainda calculado pela altura para BTREE apesar de a referência física acessar diretamente a página. Comentário Ptr em contracts.py ainda prevê chave/seq; não foram alterados consumidores nesta sessão.

- Guias/contratos e algumas issues ainda pressupõem insert/delete dinâmicos. D-P3-01 é decisão de P3; ainda falta alinhamento/alteração de consumidores. Não fingir conformidade integral com a API de mutação.
- P2 já oferece SlottedPage com HEADER=10 e sem USER, chaves INT=4 B/FLOAT=8 B e RID heap/hash=6 B. Não recuperar layouts antigos sem nova decisão.

## Decisões provisórias a rever

- Nenhuma nova escolha técnica provisória. D-P3-01 a D-P3-08 têm escolhas aprovadas; D-P3-08 ainda depende da largura do contador total; detalhes pendentes estão enumerados nos cartões e não devem ser preenchidos silenciosamente.

## Testes e validação

- Nenhuma implementação B+ foi entregue nesta sessão. Não usar este documento para afirmar que a B+ já funciona.
- `git diff --check`: sem erros. Links relativos dos documentos novos conferidos.
- Revisor independente, somente leitura, conferiu as premissas atribuídas a P1/P2 contra cartões e código e não apontou divergências.
- `python -X utf8 -m pytest -q` foi iniciado como referência, mas interrompido sem resumo final: a suíte inclui estresse de um milhão de operações de buffer. Não há resultado completo aprovado nesta sessão; a entrega altera apenas documentação.
