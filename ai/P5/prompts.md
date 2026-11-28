# Registro de prompts — P5

## 27/11/2026 — Entrega integrada, Codex (GPT-6)

- Pedido de Enzo: completar a parte P5 e tomar as decisões por ele, consultando os dois livros anexos e usando o Trabalho 3 para estudo de apresentação. Cada escolha anterior e nova deve estar justificada em Enzo_decisoes.md.
- Consultados SKS 7ª ed. cap. 15/17–19 e E&N 7ª ed. §§18.2/4/7, §19.5 e caps. 20–22, além dos seis temas do Trabalho 3. Os PDFs não foram incluídos no repositório.
- D-P5-08 a D-P5-14 decididas sob delegação. Preservados snapshots anteriores, e adicionados os primeiros arquivos de hash primário, sort-merge e comparação ingênua.
- Integração em p5/entrega sobre baae948. Nenhuma mudança em P1–P4 ou em assinaturas. As decisões, as diferenças entre estimado/medido e a revisão estão nos documentos da entrega.
- Pedido posterior de continuar. Consolidação concluída após a suíte completa (868 passaram, 3 ignorados) e 38 chamadas de bancada com assinatura SQLite válida na base tiny.

## 24/11/2026 — Início isolado da #65, Codex (GPT-6)

- Enzo escolheu “Seguir a issue: GRACE quando N/seed diferirem” (D-P5-07), após comparar o aceite com a alternativa do guia de aproveitar um hash incompatível com o outro.
- Reconsultadas #61, #65 e #48: continuam abertas; #65 depende de #61/#48. Esta etapa é preparação N1, não aceite integrado.
- Baseline na branch #61: **209 passed, 3 skipped in 38.46s**, em modo UTF-8.
- Worktree criada em `data/worktrees/p5-65`, branch `p5/65-hash-primario`, a partir de origin/main atualizado (`a9738f0`). Copiada a dependência N1 não commitada da #61 sem modificar a worktree original e preservando snapshots.
- Nenhum commit/push/PR ou escrita no GitHub. O diff desta worktree inclui a base N1 da #61; separar a dependência ao preparar commits/PR, somente quando solicitado.
- Apresentadas escolhas adicionais para alinhamento bucket_of%n_h (teto também N) e fallback do bucket excessivo por laço sem escritas. Aguardando respostas, não adotadas como confirmadas neste registro.
- Preparação dos testes revisada independentemente. R-P5-08 registrado antes de tratar a lacuna; sete casos novos em relação à base. Resultado final: **80 testes isolados passaram; 216 passaram e 3 foram ignorados na suíte completa**. Nenhuma variante de produção #65 implementada ainda.

## 23/11/2026 — Codex (GPT-6), diagnóstico inicial

- **Pedido do integrante:** “Faça as issues atribuidas a mim no repositório https://github.com/guilherme-hu/COS480_CBD_TrabalhoFinal/issues”.
- **Identificação confirmada:** P5 — EnzoSamp (Enzo de Carvalho Sampaio).
- **Trabalho realizado:** leitura do AGENTS.md, proposta, arquitetura e guia P5; consulta das issues atribuídas e dependências; preparação de ambiente virtual e execução da suíte inicial.
- **Issues atribuídas e abertas:** #61, #65, #66, #68, #69 e #71.
- **Proposta apresentada no chat:** começar a lógica da #61 contra as interfaces, com dublês de teste enquanto #43 e #54 estão abertas. Discutida execução em memória quando o construtor cabe em M−2 páginas, com GRACE nos demais casos, comparada com GRACE obrigatório e hash híbrido.
- **Escolha do integrante:** aguardando resposta; nenhuma decisão registrada como aprovada e nenhum componente gerado.
- **GitHub:** somente consultas; nenhuma escrita, push ou PR.

## 23/11/2026 — Confirmações de projeto, Codex (GPT-6)

- Enzo confirmou começar a #61 com dublês e execução direta quando o construtor couber em M−2 páginas, GRACE nos demais casos (D-P5-01).
- Enzo escolheu a estratégia do guia para fan-out, nova seed a cada nível, até três níveis e depois laço em blocos (D-P5-02).
- Enzo autorizou o limite lógico floor((M−2)×bfr) nos testes isolados, herdando o bfr do dublê nas partições enquanto as dependências reais não chegam (D-P5-03).
- Criada a branch local `p5/61-hash-join` a partir de `origin/main`, confirmado em `a9738f06ed6ac797b0e77cb28a50728ce08f6125`. Sem commit/push/PR.
- Enzo confirmou estimar custo pelo mesmo plano, distribuição uniforme e arredondamento explícito 4×n por rodada, declarando limites para skew/cache (D-P5-04).
- Quatro revisores independentes em contexto limpo e somente leitura foram executados conforme AGENTS.md §8.2. Os achados foram registrados em `review.md`; lacunas dos testes foram tratadas e verificadas com mutações em memória.
- Foram apresentadas duas escolhas corretivas após reproduzir os achados: reconhecer vazio por n_records no plano/custo e usar floor((M−2)×max(1,bfr)) para bfr positivo fracionário. **Resposta ainda pendente neste registro; não adotadas automaticamente.**

## 23/11/2026 — Confirmação das correções de borda, Codex (GPT-6)

- Enzo respondeu “Confirmo tratar vazio no plano e custo”, inclusive com blocos alocados (D-P5-05).
- Enzo respondeu “Confirmo o mínimo de um registro por página” para o limite lógico floor((M−2)×max(1,bfr)) em N1 (D-P5-06, revisão de D-P5-03).
- Reproduções e defeitos R-P5-03/04/05 já estavam registrados antes da correção. Os snapshots existentes em raw/ serão preservados.
- Corrigidas as bordas confirmadas; segunda revisão independente nos quatro focos. Achado adicional de cobertura (floor versus ceil) tratado e verificado pelo revisor de testes, sem mudar a produção.
- Rodada final: hash join 73 testes passando; suíte completa **209 passed, 3 skipped in 35.91s**. N1 concluída; integração física da #61 permanece pendente.
- Para a próxima etapa (#65), foi apresentada a divergência guia×issue sobre dois HASH com N/seed diferentes. Recomendação: seguir o aceite da issue e usar GRACE comum nesse caso. Alternativa discutida: aproveitar um dos hashes, economizando I/O, mas exigindo propor alteração do aceite. Escolha ainda pendente; nenhuma implementação #65 iniciada e nenhuma escrita no GitHub autorizada por essa pergunta.
