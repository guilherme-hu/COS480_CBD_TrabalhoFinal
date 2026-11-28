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

## 28/11/2026 — Primeira implementação parcial: revisão dos testes

- **Achado antes da correção:** testes preenchiam internos k4/k8/k12, mas não chamavam child_for nesses nós cheios; roteamento só usava até dois separadores. Uma regressão exclusiva para count>2 passaria. Categoria: lacuna de cobertura, gravidade média; revisão independente.
- **Achado adicional:** bytes literais independentes apenas para k4; leitura/escrita simétricas poderiam esconder erro específico de k8/k12. Categoria: cobertura baixa.
- **Correção planejada:** consultas com oráculo bisect_right em nós cheios e exemplos de bytes montados independentemente para k8/k12. Não é relato de bug encontrado no algoritmo; é fortalecimento das provas. Resultado registrado após execução.
- **Resultado:** 46 testes P3 passaram. Em processo isolado, substituí child_for por uma mutação que retorna filho0 para count>2: os novos testes detectaram a falha nos três perfis. Código em disco não foi alterado pela simulação. Bytes independentes k8/k12 também passam.
- **Demais revisões:** corretude, contratos e recursos não apontaram bugs concretos nos componentes atuais. Validação pública de corrupção/NULL e orçamento da construção seguem L05/L04, não foram fechados nesta entrega.
- **Integração incompleta conhecida:** criar btree_file.py faz importorskip deixar de ignorar o teste real de P4; create levanta NotImplementedError(L02). Essa falha não foi escondida nem corrigida com metadados inventados. Não é evidência de integração funcional.
- **Falha anterior à implementação, detectada na suíte:** `bench.report.decision_cards` exige títulos `### D-Px-NN` e status reconhecido; cartões P3 existentes usam títulos `##` e D08 inicia status por “organização decidida”. A leitura do mesmo arquivo no HEAD anterior confirma a incompatibilidade. Dois testes de relatório falham por essa causa; não foi alterado relatório ou conteúdo das decisões nesta entrega. É pendência documental de integração, não defeito da navegação B+.
