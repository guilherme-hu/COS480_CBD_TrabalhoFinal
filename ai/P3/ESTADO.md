# Estado da parte P3 — atualizado em 28/11/2026

## Pronto

- Branch local `p3/44-btree-bulk-loading` criada sobre `baae948`; comparação pelo conector GitHub confirmou que esse commit coincidia com `main` no início da sessão.
- `docs/decisoes/P3.md`: D-P3-01 (bulk-only, somente leitura após construção), resumo, premissas de P1/P2, referências e pendências. É documentação, não prova de implementação.
- Guia P3 com aviso do escopo revisto e resumo atualizado; `docs/estudo/P3.md` com explicação e perguntas de fixação.

## Em andamento

- Issue #44: layout e decisões §3.4. A decisão de carga está registrada, mas os demais critérios não estão concluídos.

## Falta

- Decidir layout de meta/nós, duplicatas e referências, fan-out, ocupação/preenchimento e listas de referências do índice.
- Alinhar rejeição de manutenção com P2 e retirada da variante incremental com P6. Nenhum desses ajustes foi realizado.
- Implementar `BTreeFile` e `BTreeIndex` com os acessos necessários, persistência, estatísticas e testes.
- Atualizar critérios das issues afetadas com autorização de Gabriel antes de qualquer escrita no GitHub.

## Requisitos do enunciado cobertos

| Enunciado § | Onde está | Teste/prova |
| :--- | :--- | :--- |
| §3.4.4 e justificativa §7.2.2.a | `docs/decisoes/P3.md`, D-P3-01 | Escolha documentada; nenhum teste de B+ ainda |
| §3.4.1–3, §4.2 e acessos §5 | Pendentes | Nenhum teste de B+ ainda |
| §6.1.1 manutenção | Limitação do recorte registrada em D-P3-01 | Sem alegação de manutenção dinâmica implementada |

## Desvios ou dúvidas sobre o contrato

- Guias/contratos e algumas issues ainda pressupõem insert/delete dinâmicos. D-P3-01 é decisão de P3; ainda falta alinhamento/alteração de consumidores. Não fingir conformidade integral com a API de mutação.
- P2 já oferece SlottedPage com HEADER=10 e sem USER, chaves INT=4 B/FLOAT=8 B e RID heap/hash=6 B. Não recuperar layouts antigos sem nova decisão.

## Decisões provisórias a rever

- Nenhuma nova escolha técnica provisória. D-P3-01 é decidida; seus detalhes pendentes estão enumerados no cartão e não devem ser preenchidos silenciosamente.

## Testes e validação

- Nenhuma implementação B+ foi entregue nesta sessão. Não usar este documento para afirmar que a B+ já funciona.
- `git diff --check`: sem erros. Links relativos dos documentos novos conferidos.
- Revisor independente, somente leitura, conferiu as premissas atribuídas a P1/P2 contra cartões e código e não apontou divergências.
- `python -X utf8 -m pytest -q` foi iniciado como referência, mas interrompido sem resumo final: a suíte inclui estresse de um milhão de operações de buffer. Não há resultado completo aprovado nesta sessão; a entrega altera apenas documentação.
