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

## 28/11/2026 — Codex — desempate da primária e roteiro atual

- **Pedido de Gabriel:** registrar a escolha de desempate interno para a primária NOT UNIQUE, esclarecer os três casos de B+ e iniciar um roteiro atual se útil. Antecipar a próxima decisão no chat enquanto os arquivos são atualizados.
- **Atuação:** D-P3-03, roteiro parcial `P3_PLANO_ATUAL.md`, estudo e estado. Identificador interno não determina a referência pública; a próxima discussão compara referência física/lógica. Não aprovar silenciosamente seq de 8 B, geração de IDs, referência, layout ou preenchimento.
- **Limite:** sem código de implementação, commit, push ou alteração de issues. Guia antigo não usado como fundamento técnico. Roteiro novo registra obrigações e pendências, não funcionamento já entregue.

## 28/11/2026 — Codex — referência física e mapa no Notion

- **Pedido:** Gabriel aprovou bloco + slot para referência da primária UNIQUE/NOT UNIQUE e pediu database dos mesmos itens da lista agrupada, ao fim de CBD — P2/P3: decisões e comparações; preservar descrições, origem, estado e espaço para notas.
- **Atuação:** D-P3-04 e atualização do roteiro/estado/estudo. Database no Notion com 23 registros, mesmos cinco grupos, colunas de origem/escopo/decisão/pendências/notas e estados coloridos; implementação separada. Conteúdo anterior preservado.
- **Referências:** E&N §17.6.1; P2 D-P2-10 e formato existente; consumidores P4 tratam Ptr como opaco. Sem mudança de código compartilhado, commit, push ou escrita no GitHub. Próxima decisão: representação do identificador interno NOT UNIQUE.

## 28/11/2026 — Codex — representação do identificador

- **Pedido:** Gabriel aprovou registrar no repositório e no mapa Notion a proposta de geração/armazenamento do identificador de desempate; perguntou sobre formato comum dos nós internos e capacidade com registros variáveis.
- **Atuação:** D-P3-05: contador por árvore na carga desde 1, u32 de 4 B big-endian; somente primária NOT UNIQUE. Atualização de roteiro/estado/estudo e registro existente de duplicatas no Notion, preservando Notas e grupos.
- **Limite:** nó interno compartilhado ainda é proposta; nenhum layout novo aprovado automaticamente. Sem implementação, commit, push ou alteração de código de colegas. Próximo debate: nós internos/separadores.

## 28/11/2026 — Codex — formato dos internos

- **Pedido:** Gabriel aprovou registrar o formato proposto dos internos e pediu exemplos de tamanhos reais para DRE/CR; esclarecimento de que k é variável em bytes, não kilobytes. Autorizou atualizar o item de layout no Notion, mantendo Parcial e ajustando Falta definir. Sem commit.
- **Atuação:** D-P3-06, resumo/roteiro/estado/estudo, fórmula e exemplos 4/8/12 B com fan-out 512/341/256 para B4096. Não aprovar layouts de folhas, metadados completos, ocupação ou construção dos últimos grupos.
- **Fontes:** enunciado §3.4.2; SKS §§14.3.1 e inserção p642; E&N §17.3.2; codecP2. Os livros não determinam o contador u16 nem offsets: são escolhas aprovadas do projeto.

## 28/11/2026 — Codex — terminologia de D-P3-06

- **Pedido:** Gabriel pediu consistência entre separador/ponteiro para filho e as explicações simplificadas que usavam limite/destino.
- **Atuação:** padronizados os nomes dos componentes nas explicações de estudo e no cartão. Preservado limite para faixa, capacidade e domínio, onde tem outro significado. Sem mudança técnica, staging ou commit.

## 28/11/2026 — Codex — folhas primárias com formato de P2

- **Pedido:** após fazer o commit anterior, Gabriel aprovou registrar a reutilização de SlottedPage/RecordCodec nas folhas primárias UNIQUE e NOT UNIQUE, com justificativa concisa nos livros e responsabilidade já exercida por P2.
- **Atuação:** D-P3-07 no resumo/cartão; NOT UNIQUE prefixa ID4, UNIQUE só registro; sem chave duplicada. Atualizados roteiro/estado/estudo e acompanhamento Notion, preservando estado Parcial do layout amplo.
- **Limite:** sem código compartilhado modificado, implementação B+, commit ou push. Preenchimento, metadados completos e secundário continuam abertos.
