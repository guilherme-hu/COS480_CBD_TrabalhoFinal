# Pedidos e uso de IA — P3

## 28/11/2026 — Codex

- **Pedido de Gabriel:** conferir suas issues, criar uma branch para o trabalho de P3 e registrar em `docs/decisoes/P3.md` a escolha de construir B+ exclusivamente por bulk loading. Usar resumo inicial e cartões no padrão do grupo; separar premissas de P1/P2 e detalhes ainda pendentes. Não abrir PR somente para a documentação; branch também destinada à implementação posterior.
- **Contexto da escolha:** debate nesta conversa sobre carga estática de SELECT/JOIN, alternativas de manutenção, trechos do PDF e SKS/E&N. O pedido atual confirma o registro da escolha; não delega ao agente as demais decisões do layout.
- **Atuação:** criação de `p3/44-btree-bulk-loading`, registro D-P3-01 e premissas herdadas; aviso no guia P3; nenhuma implementação de árvore nem alteração dos contratos/código dos colegas.
- **Limite do registro:** este é o primeiro pedido registrado nesta pasta; não é um log integral retroativo de toda a conversa anterior. Nenhum código bruto foi gerado nesta sessão.

## 28/11/2026 — Codex — chave simples (§3.4.1)

- **Pedido de Gabriel:** após debater chave simples/composta, registrar como decisão o uso de um campo da tabela por B+ primária. Justificar pelo recorte de operações do enunciado e das consultas adotadas, não apenas por P6 já usar uma coluna. Indicar o vínculo com §3.4 e o avanço da issue #44.
- **Verificação:** PDF vigente §§3.4.1, 5.1, 5.2 e exemplos de 6.4; SKS §§14.2.5 e 14.6.2; consultas, preparação e configuração atuais. Acesso por prefixo e outras utilidades de chaves compostas foram considerados; não se afirmou que exigem sempre dois filtros simultâneos.
- **Atuação:** D-P3-02 no resumo e cartão; separação entre campo único e identificação de duplicatas; atualização do guia, estudo e estado. Sem alteração de implementação ou escrita no GitHub; issue #44 permanece aberta.
- **Separação solicitada:** Gabriel pediu commit somente de D-P3-01 antes de continuar D-P3-02. Commit local `a05df15`; alterações de D-P3-02 mantidas fora dele, ainda sem commit.

## 28/11/2026 — Codex — referência explícita de D-P3-01

- **Pedido de Gabriel:** destacar que a decisão de bulk loading corresponde ao §3.4.4, assim como D-P3-02 identifica §3.4.1.
- **Atuação:** referência explícita no título, resumo, abertura do cartão, acompanhamento, guia e estudo. Mantida a distinção entre escolha do método de carga e limitação adicional de manutenção. Alteração documental sem novo commit nem alteração do commit anterior.
