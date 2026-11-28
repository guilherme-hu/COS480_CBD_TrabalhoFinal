# Revisões — P3

## 28/11/2026 — Revisão documental de D-P3-03

- **Achado confirmado antes da correção:** `docs/estudo/P3.md`, pergunta de fixação sobre `seq` na seção de D-P3-02, ainda dizia que o uso do desempate seria decidido. D-P3-03 já aprova seu uso na primária NOT UNIQUE.
- **Categoria:** documentação desatualizada; gravidade baixa. Encontrado por revisor independente somente leitura e confirmado pela comparação dos cartões.
- **Correção prevista:** atualizar a resposta para distinguir uso aprovado de geração, tamanho e codificação pendentes. Nenhum código/teste de execução afetado; validação por leitura e `git diff --check`.

## 28/11/2026 — Revisão documental de D-P3-04

- **Achado confirmado antes da correção:** cbd/exec/access.py:112–114 ainda estima fetch de BTREE pela altura, valor consumido por selection.py:225,250. D-P3-04 prevê referência física e acesso direto à página, mas o cartão não explicitava esse ajuste de estimador.
- **Categoria:** omissão documental de integração com P4. Achado por revisor independente e confirmado por leitura do código.
- **Correção:** registrar pendência no cartão, roteiro, estado e item correspondente do Notion; não modificar implementação de P4 sem alinhamento. Nenhum teste de execução alterado; validação por leitura e diff.

## 28/11/2026 — Revisão documental de D-P3-05

- **Achado confirmado antes da correção:** resumo D-P3-03 e respostas antigas em docs/estudo/P3.md ainda tratavam geração/tamanho/codificação como pendentes depois da aprovação de D-P3-05.
- **Categoria:** pendências documentais desatualizadas, gravidade baixa; revisor independente conferiu cartão e codec de P2.
- **Correção:** remeter esses resumos ao fechamento em D-P3-05, preservando pendências de offsets/layout e implementação. Verificação por leitura e git diff --check; nenhum teste de execução necessário para esta mudança documental.

## 28/11/2026 — Revisão documental de D-P3-06

- **Achado confirmado antes da correção:** acompanhamento do desempate ainda dizia que a disposição nos separadores estava pendente, embora D-P3-06 já fixe seus bytes e offsets nos internos.
- **Categoria:** pendência documental desatualizada, gravidade baixa; revisor independente também conferiu fórmula, offsets e escopo.
- **Correção:** restringir a pendência atual às entradas das folhas. Conferidos limites aritméticos para k4/8/12 em B4096: 512/341/256 filhos, sem implementação B+ alegada.
