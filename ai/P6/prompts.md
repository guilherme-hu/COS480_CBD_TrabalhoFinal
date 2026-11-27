# Registro de uso de IA — preparação da base

Data: 04/11/2026. Ferramenta: Codex, nesta sessão do projeto.

Pedido do usuário:

> Gostaria de que voce implementasse apenas a parte do banco de dados e da
> carga de trabalho (carga de dados de aluno e turma por exemplo) usando o que
> tiver sobre essa parte no inicio do pdf da proposta e no md que comeca com
> P6. Faça tudo necessario para deixar esses dados prontos pros testes

Contexto consultado: primeiras três páginas de `proposta.pdf` (extraídas
localmente com PDFKit), transcrição `proposta.md`, guia P6, arquitetura geral
e trechos do P2 sobre codec, hashing, catálogo e layout de páginas.

Escopo aplicado: esquemas, contratos compartilhados copiados literalmente,
geração determinística, integridade relacional, subconjuntos, SQLite de
referência, consultas e assinaturas, planos e adaptador de carga física,
Engine contratual, CLI, documentação e testes. As demais camadas ainda não
existem no repositório; não foram implementados algoritmos ou medições fictícias.

---

# Sessão de 08/11/2026 — backlog e implementação da parte P6

Ferramenta: Claude Code, modelo Claude Opus 5.5.

1. Pedido do usuário:

   > verificasse o que ja tem feito no repositorio e criasse as issues no formato
   > proposto pela skill de forma a organizar as tarefas que eu tenho que cumprir
   > para a minha parte do trabalho. [...] o esquema da base de dados e carga eu
   > creio que ja tenha sido feito mas vale voce verificar e possivelmente
   > melhorar/aumentar a carga

   Resultado: backlog de 25 issues (#3–#27) e 4 milestones sem data no GitHub.
   Achado: com B = 8192, `aluno` estimado em 968 blocos (< 1 280); virou a #6.

2. Pedido do usuário: "Pode ir resolvendo as issues de forma a implementar minha parte".

   - #4: carga de trabalho reduzida ao escopo novo (3 joins mestre-detalhe,
     20 igualdades em `dre`, faixas em `cr` com metas 1% e 50%). Alteração
     pequena em código existente; sem versão bruta separada em `raw/`.
   - #5: removidos subconjuntos e variantes fora do escopo (23 variantes);
     `distinct = V(A)` nas variantes hash NOT UNIQUE; regra do N comum dos pares
     bucket-join (D-P6-01, provisória). Defeito da 1ª versão registrado em `review.md`.
   - #7: docstrings no `Engine` e `tests/test_engine.py` com dublês da camada 4.
     Teste de mutação manual: inverter a ordem de `cold()` (zerar antes do
     `clear`) faz 2 testes falharem. O teste com a pilha real fica `skip` até
     P1/P2 estarem no `main`.
   - #9: `bench/matrix.py` com as 117 linhas de join + 4 de lado forçado.
     Versão bruta salva em `ai/P6/raw/matrix_joins_v1.py` antes de refinar.
     Operadores referenciados por nome (resolvidos no orquestrador). Política
     por algoritmo fica LRU até o experimento da #16.
   - #10: seleções na matriz (80 igualdades + 10 faixas), valores lidos do
     `workload.json`. Bruto em `ai/P6/raw/matrix_selects_v1.py`.
   - #11: `bench/run.py` (protocolo de medição, CSV §6.3, cache de Engine).
     Bruto em `ai/P6/raw/run_v1.py`. Refinamento após autorrevisão: exceção
     genérica do operador abortava a rodada inteira; agora vira `status=error`
     (pino vazado continua abortando). Registrado em `review.md`.
   - #12: coluna `valid` comparando `(count, checksum)` da saída com o
     `workload.json` (calculado no SQLite por `bench.build`), fora da janela.
   - #13: repetições (3 a 10, até < 5% em I/O e tempo), `--resume`,
     `summary.csv` com medianas. Ajuste após teste: linha `valid=False` era
     repetida 3 vezes; agora para na primeira (saída determinística).
   - Fim da sessão: `ai/P6/ESTADO.md` e `docs/estudo/P6.md` criados.

3. Pedido do usuário: "pode fazer push e abrir PR sim [...] acho interessante ter
   uma issue pra mim que depende de P4/5 de forma que eu consiga adequar minha
   parte a isso".

   Resultado: PRs empilhados #28–#35 (um por issue) e issue #36 (adequar a
   bancada aos operadores reais de P4/P5); #15 passou a depender da #36.

---

# Sessão de 09/11/2026 — backlog do grupo e fluxo de issues

Ferramenta: Claude Code, modelo Claude Opus 5.5.

1. Pedido do usuário:

   > Abra as issues nesse formato para todos os membros do grupo (Px) e adiciona
   > no agents md que os outros membros do grupo devem, quando forem comecar a
   > fazer algo no repositorio eles se identifiquem como Px e possam pegar e
   > resolver as issues atribuidas a eles e que possam complementar tambem as
   > issues para si e para os outros membros

   Resultado: 37 issues (#37–#73) para P1–P5 e o grupo, tiradas dos testes
   obrigatórios e armadilhas de cada guia, atribuídas aos donos; labels
   `parte:P1`–`P5`, `parte:grupo` e áreas de camada; milestones C1–C3 (marcos
   da arquitetura §10.1, sem data). Bloqueios de #14, #16 e #36 ligados às
   issues novas. Seção §10.1 do `AGENTS.md` com o fluxo de issues (issue #74).
   Erro do agente: ao ligar a #27 à #72 criou um ciclo de dependência (a #72
   já dependia da #27); desfeito na hora.

2. Pedido do usuário: "pode atribuir todas as issues de P1 e fazer Push e PR sim".

   Resultado: issues do P1 já estavam com a dona; interpretado como as do P6,
   atribuídas a `bepo7` (#3–#36). Push da branch e PR #75 do `AGENTS.md`.

3. Pedido do usuário: registrar esta sessão aqui (esta entrada).

4. Pedido do usuário: "veja os comentarios deixados nos PRs abertos e resolva".

   Resultado: achados do P2 nos PRs #28, #32, #33, #35 e #75 corrigidos com teste
   que falhava antes (registro em `review.md`); pilha rebaseada. Force-push
   bloqueado pela permissão do modo automático: fica com o integrante.
   D-P6-01 (#29) aguarda debate com P2 e P5.

5. Pedido do usuário: "pode seguir essa ordem" (#24, depois #22 e #23).

   - #24: esqueleto do relatório em `docs/relatorio/` e `bench/report.py`
     (capa do README, tabelas de `base_stats.csv`, contagem da matriz). Bruto em
     `ai/P6/raw/report_v1.py`. Refinamentos depois do primeiro PDF: decimal com
     vírgula, alinhamento das colunas de texto, contagem de seleções sem depender
     de base preparada, fonte forçada que não existia no tectonic, símbolo ⋈ sem
     glifo na fonte padrão (trocado por `$\bowtie$`).
   - #22: `bench/compare_cost.py` (razão medido/estimado, motivos por regra do
     P6 §7, lista de não explicadas). Bruto em `ai/P6/raw/compare_cost_v1.py`;
     a versão bruta passou nos testes sem refinamento.
   - #23 (parcial): `bench/plots.py` com joins e seleções, medido ao lado do
     estimado; `matplotlib` como dependência opcional `.[plots]`. Bruto em
     `ai/P6/raw/plots_v1.py`; defeito de parsing do `exp_id` achado por teste
     (`review.md`). Desenho conferido com summary sintético (não é medição).

6. Debate da #6 (volumes): opções A (aumentar a base), B (manter e justificar),
   C (memória constante em bytes no experimento de bloco). Pedido do usuário:
   "Vai de C". Resultado: D-P6-02, `areas_for_block`, `size_checks_for_block`,
   base sem `--rows turma=100000`, `base_stats.csv` regenerado (adequado nos 3 B).

7. "veja o comentario deixado no meu PR, corrige e mergeia em seguida" (09/11,
   Claude Code, Opus 5.5): PR #75, detalhe opcional da revisão (onde fica o acordo
   para pegar issue alheia) resolvido; PR mergeado por rebase, #74 fechada.

8. "tem como fazer mais alguma issue atribuida a mim?" (09/11): conferidos os
   bloqueadores de todas as issues P6. Só a #19 estava liberada (bloqueada só
   pela #11, já feita; o P1 implementou toss e prefetch no PR #2). Resultado:
   `factor_experiments` em `bench/matrix.py` (70 linhas: bloco 54, toss 6,
   prefetch 4, WORK 6, níveis do P6 §4.4), `--only factors` em `bench/run.py`,
   tabela no relatório gerado. Bruto em `ai/P6/raw/matrix_factors_v1.py`; a
   versão bruta passou nos testes escritos antes.

---

# Sessão de 12/11/2026 — issues destravadas

Ferramenta: Claude Code, modelo Claude Opus 5.5.

1. Pedido do usuário: "tem alguma issue minha que possa ser resolvida com o que
   tem ate agora na main (de pull)".

   Resultado: `main` atualizado; destravadas #3, #6, #8, #19, #22, #23 e #24.
   O `main` ainda não tem `cbd/storage`, `cbd/files` nem `cbd/exec`.

2. Pedido do usuário: "pode ir fazendo elas".

   Resultado: o agente encontrou a pilha local da sessão de 09/11
   (`p6/24` → `p6/22` → `p6/23` → `p6/6` → `p6/19`, não enviada) já cobrindo
   #24, #22, #23, #6 (D-P6-02) e #19. Faltavam #3 e #8: cartões D-P6-03 a
   D-P6-08 (gerador e referência) e D-P6-09 (índice secundário clustering),
   todos `provisória`, porque o integrante pediu para seguir sem debate.
   Testes novos: `test_heap_order_is_not_key_order`,
   `test_signature_detects_swapped_duplicate_with_same_count`.

---

# Sessão de 13/11/2026 — PRs da pilha e revisão do PR #2

Ferramenta: Claude Code, modelo Claude Opus 5.5.

1. Pedido do usuário: "tem algum pr meu que possa ser revisado e mergeado? tem algum
   outro PR pra abrir?" e depois "pode fazer ambos".

   Resultado: nenhum PR do P6 aberto. Abertos os PRs empilhados #76–#82
   (#24, #22, #23, #6, #19, #3 e Refs #8). Revisão do PR #2 (P1) como consumidor,
   numa worktree descartável com o PR mesclado sobre a pilha: 130 testes passam;
   sonda do Engine real (FileManager + BufferPool do PR, catálogo dublê) confere
   `cold()`, `snapshot()` com todas as colunas do CSV, `eng.M`, contadores por área
   e reabertura com MRU. Nenhum comentário no GitHub sem o ok do integrante.
   Erro do agente na sonda: supôs blocos a partir de 0; o contrato diz bno ≥ 1.

2. Pedido do usuário (17/11): "faz tudo o que der (mas da pull na main antes)".

   Resultado: `main` sem mudanças desde 12/11. D-P6-10 (provisória): fator prefetch
   passa a repetir também o INL maior ⋈ maior (`b_s_btree`), com os scans como
   controle; pilha #80–#82 rebaseada localmente (o force-push foi bloqueado pelo
   modo automático e ficou para o integrante autorizar). Comentário no PR #2 com
   os achados de consumidor e issue nova para o P2 (`allow_prefetch`).

---

# Sessão de 19/11/2026 — merge da pilha P6 e da camada 4

Ferramenta: Claude Code, modelo Claude Opus 5.5.

1. Pedido do usuário: "queria que voce resolvessse meus PRs abertos, desse pull na main,
   mergeasse meus prs e visse se tem mais issue atribuida a mim para fazer".

   Resultado: a pilha local #80–#82 (D-P6-10 e o rebase de 17/11) não estava no GitHub;
   suíte verde nas três branches (87–89 passaram, 2 pulados), envio com
   `--force-with-lease`. Pilha #76–#82 mesclada em ordem com merge commit, cada PR
   reapontado para o `main` antes do merge. Fechadas #24, #22, #23, #6, #19, #3; a #8
   fica aberta (espera o professor). Issue desbloqueada: #25.

2. Pedido do usuário: "revise e mergeia" (o único PR aberto era o #2, do P1) e depois
   "resolve, mescla e documenta".

   Resultado: revisão do `f64e3e3` mesclado com o `main`, reproduzindo os itens da
   revisão do P2 de 12/11. Prefetch entre cursores e prefetch com MRU corrigidos
   (80 e 600 leituras, como esperado); `lost_free` e página suja perdida no
   `fm.close` corrigidos; área errada no prefetch e toss × MRU ficaram como contrato
   documentado (D-P1-12, D-P1-09). Faltava o marcador `stress`, que o `fb54146`
   dizia registrar. O primeiro envio para a branch do P1 foi bloqueado pelo modo
   automático; com o ok do integrante, foram enviados o merge do `main` (conflito
   numa linha do guia P6 §4.4) e o registro do marcador. Comentário de revisão no
   PR #2, merge, `main` com 130 passaram e 2 pulados. Registro em `ai/P6/ESTADO.md`
   ("Camada 4 no `main`").

3. Pedidos do usuário: "pode comentar na #16 e começar a #25", "A" (capítulo 2 por item do
   enunciado §7.2.2.a) e "pode postar os comentários das lacunas".

   Resultado: comentário na #16 (toss × MRU). Capítulo 2 reescrito por item do enunciado, com
   escolha, preterida, motivo e cartão; índice gerado dos cartões (`decision_cards`,
   `decisions_section`) com teste que exige citar todo cartão no capítulo. Versão bruta em
   `ai/P6/raw/report_decisoes_v1.py`. Dois revisores independentes: código e testes (8 falhas
   reproduzidas e corrigidas: cartão que sumia em silêncio, status fora do vocabulário, ID
   duplicado, nota contando como justificativa) e fidelidade às fontes (16 achados aceitos,
   incluindo erro no D-P6-07 sobre CPF válido). Registro em `ai/P6/review.md`. Comentários de
   lacuna em #62, #63, #71 e #67.

4. Pedidos do usuário: "pode abrir o PR, mesclar e começar os provisórios", "pode ir de B"
   (D-P6-01), "pode escolher a que achar melhor" (D-P6-10) e "pode escolher as que achar
   melhor em todas" (D-P6-03 a D-P6-08). Ferramenta: Claude Code (Claude Opus 5.5).

   Resultado: PR #85 (#25) mesclado. D-P6-01 debatida e confirmada pelo integrante (B), com
   comentário em `bench/build.py`. D-P6-10 debatida (opções A–D, evidência da camada 4 no
   `main`) e D-P6-05 apresentada; o integrante delegou as escolhas ao agente, que manteve a
   recomendação de cada cartão (D-P6-03 A, 04 D, 05 C, 06 B, 07 A, 08 A, 10 C). Só a D-P6-09
   segue provisória, à espera do professor. Corrigido o "Em palavras simples" da D-P6-10
   (prefetch 8 traz 9 páginas e poupa 8 voltas, não 7). Índice gerado: 22 cartões, 1
   provisório. Suíte: 142 passaram, 2 pulados.

5. Pedidos do usuário: "pode abrir o PR e mesclar apos revisao", "pode corrigir o plural no
   gerador também" e "nao existe como falar coom o professor. pode decidir e documentar a
   decisao". Ferramenta: Claude Code (Claude Opus 5.5).

   Resultado: PR #86 mesclado depois de um revisor independente (5 achados aceitos, ver
   `ai/P6/review.md`); plural do índice corrigido com teste. D-P6-09 decidida: A (ponteiros
   agrupados) com a explicação C, delegada ao agente porque não há canal com o professor. Para
   cobrir o melhor argumento contra (leitura B, do E&N), o capítulo 2 diz qual linha mede o
   *clustering index* do livro (B+ primária em `cr`). Nenhum cartão da P6 segue provisório.

6. Pedidos do usuário: "pode começar a #5" e "pode escolher a que achar melhor". Ferramenta:
   Claude Code (Claude Opus 5.5).

   Resultado: a #5 já estava fechada (plano físico mesclado em 12/11); faltava o cartão.
   Debate do D-P6-11 (A: todas as combinações organização × coluna; B: uma por tabela pela PK;
   C: sem `aluno.cr`; D: sem hash em `cr`), com a contagem das 23 variantes e a conferência de
   que toda variante é usada pela matriz, menos `aluno__bt_dre_inc`, que é da #17. O integrante
   delegou; o agente manteve A. Capítulo 2 cita o cartão no lugar de "cartão pendente (P6)".

7. Pedidos do usuário: "tem algo que seja atribuido a mim que ainda precise ser feito?" e
   "vai la" (resumo das decisões). Ferramenta: Claude Code (Claude Opus 5.5).

   Resultado: levantamento das 11 issues abertas da P6; todas bloqueadas pelas camadas 3 e 2
   (P2, P3, P4, P5), exceto a parte já feita da #25. Resumo das decisões da P6 (AGENTS §9.1,
   parte da #27) no topo de `docs/decisoes/P6.md`: tabela dos 11 cartões, como as decisões se
   combinam, perguntas prováveis do professor e quais cartões foram delegados sem debate.

