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
