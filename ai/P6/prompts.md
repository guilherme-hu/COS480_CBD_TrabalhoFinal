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
