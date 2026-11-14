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
