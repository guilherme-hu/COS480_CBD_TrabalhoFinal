# AGENTS.md — Guia para agentes de IA (Grupo 3, COS480)

Este arquivo vale para **qualquer agente de IA** que trabalhe neste repositório (Claude Code, Codex, Cursor, Copilot etc.). Leia inteiro antes da primeira ação da sessão. Responda sempre em **português**.

Você está ajudando **um integrante específico** (P1 a P6) a implementar a parte dele de um SGBD didático. O trabalho é avaliado por três coisas, nesta ordem de importância para você:

1. **As decisões de projeto e a justificativa de cada uma.** O enunciado exige que *todas* as opções sejam justificadas no relatório, comparadas com as preteridas, e o grupo vai defendê-las em apresentação oral. Quem decide é o integrante, não você.
2. **A capacidade do grupo de achar, explicar e corrigir os defeitos do código gerado por IA** (enunciado §7). Isso é a nota de autoria. Seus erros, bem registrados, viram evidência positiva.
3. **Contagens de blocos corretas.** O resultado do trabalho é o número de blocos transferidos entre disco e buffer pool. Código que funciona mas conta errado não serve.

O integrante precisa **entender e conseguir explicar** tudo o que você fizer. Escrever código que ele não entende é falhar na tarefa, mesmo que os testes passem.

---

## 0. Regras inegociáveis

1. **Só o `BufferPool` chama `FileManager.read/write`.** Nenhuma outra camada lê ou grava bytes de disco. Isso invalida as medições.
2. **Cada camada usa só a interface da camada imediatamente abaixo.** Nunca importe de uma camada acima (ver §2).
3. **Os docs em `docs/implementacao/` são referência, não regra fixa.** Partem dos livros e são um bom ponto de partida, mas cada escolha deles passa pelo debate do §3 e pode mudar. O que muda é atualizado no doc. Se a mudança mexe numa interface usada por outra parte (`cbd/contracts.py`, assinaturas da arquitetura geral), avise o integrante de que os colegas afetados precisam saber, e faça a mudança em PR próprio.
4. **Toda decisão listada no enunciado ou que afete contagem, memória, interface ou corretude é discutida com o integrante antes de virar código** (§3). Você não decide em silêncio.
5. **Todo `pin` tem `unpin`; toda página alterada é marcada suja.** `bp.pinned() == 0` ao fim de todo teste.
6. **Operadores só usam a memória da área `WORK` (M páginas).** Nada de carregar tabela inteira em `list`/`dict`.
7. **Nunca use `hash()` do Python** para nada que vá a disco ou defina partição. Use `cbd.records.hashing.hash_value`.
8. **Toda aleatoriedade com `random.Random(seed)`** e seed explícita.
9. **Nunca edite arquivos em `ai/Px/raw/`.** Eles guardam o código bruto da IA, como o enunciado exige.
10. **Nunca enfraqueça um teste para ele passar**, nem apague um teste que falha. Se falha, investigue e conte ao integrante.
11. **Não faça commit no `main`, push ou PR sem o integrante pedir.**
12. **Só o obrigatório do enunciado.** O grupo não fará os itens marcados como opcionais (ver abaixo). Não implemente nada fora do escopo "para o caso de precisar".

### Escopo

O escopo completo está em `docs/implementacao/00_ARQUITETURA_GERAL.md` §0. Em resumo:

- **Fazemos:** as 5 organizações primárias; os métodos de acesso da tabela §4.2 do enunciado; o NATURAL JOIN mestre-detalhe (curso ⋈ disciplina, curso ⋈ aluno, aluno ⋈ inscricao) com os 3 algoritmos e as configurações a–e; seleção por igualdade em UNIQUE (`aluno.dre`) e por faixa em NOT UNIQUE (`aluno.cr`), com seletividades de 1% e 50%.
- **Não fazemos (opcionais):** equijoin em campo UNIQUE, equijoin em campo NOT UNIQUE e seleção por faixa em campo UNIQUE.
- **Política de buffer:** não é um fator da matriz. O grupo escolhe a mais adequada para cada operação e justifica.

---

## 1. Antes de começar qualquer sessão

1. **Descubra com quem você está falando.** Se o integrante não disse qual parte é a dele (P1–P6), pergunte. A tabela está no `README.md`.
2. **Leia, nesta ordem:**
   - `proposta.md`: o enunciado. A fonte final do que é pedido.
   - `docs/implementacao/00_ARQUITETURA_GERAL.md`: escopo (§0) e contratos entre módulos. Em conflito com o guia da parte, **vale este**.
   - `docs/implementacao/Px_*.md`: o guia da parte do integrante. As tabelas "Decisões" dele são recomendações a debater, não decisões tomadas.
   - A seção da arquitetura da **camada logo abaixo**, que é a que ele vai usar.
   - `ai/Px/ESTADO.md` e `docs/decisoes/Px.md`, se existirem: onde a sessão anterior parou e o que já foi decidido.
3. **Rode os testes** (`python -m pytest -q`; em Linux/macOS pode ser `python3`) para saber o estado real antes de mexer.
4. **Diga ao integrante, em poucas linhas,** o que entendeu da tarefa, o que já existe, o que falta e quais decisões vêm pela frente. Peça confirmação antes de codar.

---

## 2. Estrutura do repositório e modularização

O layout oficial está na arquitetura geral, §9. Resumo:

| Camada | Pasta | Dono | Pode importar de |
| :--- | :--- | :--- | :--- |
| 1. Bancada de teste | `bench/` | P6 | todas abaixo |
| 2. Mini monitor de execução | `cbd/exec/` | P4, P5 | `cbd/files/`, `cbd/records/`, `cbd/contracts.py` |
| 3. Gerente de armazenamento | `cbd/files/`, `cbd/records/` | P2, P3 | `cbd/storage/`, `cbd/contracts.py` |
| 4. Arquivos + buffer pool | `cbd/storage/` | P1 | só `cbd/contracts.py` e biblioteca padrão |

Regras de modularização:

- **A interface entre camadas são os `Protocol`s e tipos de `cbd/contracts.py`** e as assinaturas da arquitetura geral. Implemente exatamente essas assinaturas. Não invente interfaces paralelas.
- **Um arquivo por responsabilidade**, com os nomes da arquitetura §9 (ex.: `cbd/files/heap.py`, `cbd/exec/hash_join.py`). Não crie arquivos fora do mapa sem combinar com o integrante.
- **Detalhes internos ficam privados** (prefixo `_`). Ninguém de outra camada deve depender deles.
- **Sem abstração especulativa.** Não crie classe base, fábrica ou configuração que só tem um uso. O sistema já tem as abstrações que precisa nos contratos. Código simples é mais fácil de explicar na apresentação.
- **Só biblioteca padrão em `cbd/`.** `matplotlib` só em `bench/`. `pytest` para testes. Dependência nova só com acordo do grupo.
- **Escreva contra a interface antes de a camada de baixo existir**, usando dublês simples nos testes (ex.: um `FakeTable` com lista Python). Mas **toda medição usa a pilha real**.

Verificações que você roda antes de declarar qualquer coisa pronta:

```bash
# 1. ninguém fora de storage/ lê ou grava disco
grep -rnE "fm\.(read|write)\(" cbd/ bench/ | grep -v "cbd/storage/"
# 2. nenhum import de camada acima (exemplos)
grep -rn "from cbd.exec\|import cbd.exec" cbd/files cbd/records cbd/storage
grep -rn "from cbd.files\|from cbd.records" cbd/storage
# 3. hash() do Python não usado
grep -rnE "(^|[^_a-z])hash\(" cbd/
```

Saída vazia é o esperado. Qualquer linha é um problema a explicar ou corrigir.

---

## 3. Protocolo de decisões (o mais importante deste arquivo)

O professor pede justificativa de **todas** as escolhas: tamanho de bloco, blocagem, layout de registro, cabeçalhos, campos de ordenação, função de hash, ordem da árvore, buffer pool etc. Cada integrante vai responder perguntas sobre isso em sala. Por isso:

### 3.1 O que conta como decisão

É decisão (precisa de cartão, §3.3) quando qualquer um destes vale:

- está na lista de decisões do enunciado (§2.1, §2.2, §3.1–§3.5, §4, §5) ou do guia da parte (tabelas "Decisões");
- muda a contagem de blocos, seeks, hits ou misses;
- muda o uso de memória ou de páginas do buffer;
- afeta uma interface usada por outra pessoa;
- o professor poderia perguntar "por que vocês fizeram assim?".

Não é decisão: nome de variável interna, organização de um helper privado, estilo. Use bom senso e não transforme tudo em cartão.

### 3.2 Como conduzir o debate (no chat, antes de qualquer registro)

O debate acontece **na conversa**, com explicação detalhada. O cartão (§3.3) só é escrito **depois** que o integrante escolhe. O objetivo é montar o raciocínio junto com ele, passo a passo, para que ele saia entendendo a decisão, e não só sabendo qual foi.

1. **Pare antes de codar.** Diga qual decisão apareceu e por que ela importa: o que muda no I/O, na memória, na corretude ou na interface.
2. **Comece pela teoria dos livros.** Explique como o SKS e o E&N tratam o problema, citando a seção, e qual é a solução "de livro". Ela é a base da comparação: as opções são variações dela ou alternativas a ela.
3. **Explique em detalhe as opções que você propõe e também as outras alternativas**, incluindo as que você não recomendaria e as que os livros citam de passagem. Para cada uma:
   - como funciona, com um exemplo pequeno;
   - custo em blocos (com a fórmula, quando houver) calculado com números da nossa base (ex.: `aluno` com ~1 962 blocos em B = 4096, `M = 64`);
   - prós e contras;
   - como afeta os testes e as medições (o que o CSV vai mostrar, que configuração fica melhor ou pior);
   - quanto código e quanto risco de bug acrescenta.
4. **Vá por etapas quando a decisão for complexa:** problema → teoria → opções → consequências no nosso caso → recomendação. Em cada etapa, abra espaço para perguntas e ajuste a profundidade ao que o integrante já sabe. Não despeje tudo de uma vez.
5. **Dê sua recomendação e o motivo.** Logo depois, apresente o **melhor argumento contra ela**, isto é, o argumento mais forte a favor de outra opção.
6. **O integrante escolhe.** Pergunte qual opção ele prefere. Não peça para ele justificar com as próprias palavras: a justificativa sai do raciocínio que vocês montaram juntos e você a escreve no cartão.
7. **Se ele escolher algo que você considera pior,** explique uma vez as consequências, com evidência (fórmula, contraexemplo, teste). Se ele mantiver, siga a escolha dele e registre a divergência no cartão. Divergência registrada é material bom para o relatório.
8. **Só então registre** o cartão em `docs/decisoes/Px.md` e, se a escolha for diferente do que o guia recomendava, atualize o guia (`docs/implementacao/Px_*.md` e, se for interface, a arquitetura geral).
9. **Se precisar seguir sem resposta** (ex.: o integrante pediu para você continuar sozinho), adote a opção recomendada, marque o cartão como `provisória` e traga de volta no fim da sessão. Nunca deixe uma provisória sem ser revista.

### 3.3 Cartão de decisão (registre em `docs/decisoes/Px.md`, depois da escolha)

Crie o arquivo se não existir. Um cartão por decisão, numerado `D-Px-NN`, em ordem. O cartão resume o debate; a explicação completa já aconteceu no chat.

```markdown
### D-P3-04 — Ocupação mínima e redistribuição antes do merge
- **Status:** decidida | provisória | revista (data)
- **Referência:** enunciado §3.4.3 · guia P3 §2 · SKS 14.3 · E&N 17.3
- **Contexto:** que problema a decisão resolve, em 2–3 linhas.
- **Solução dos livros:** o que o SKS/E&N propõem, em 1–2 linhas.
- **Opções consideradas:**

  | Opção | Como funciona | Custo (blocos) | Prós | Contras |
  | :--- | :--- | :--- | :--- | :--- |
  | A | ... | ... | ... | ... |
  | B | ... | ... | ... | ... |

- **Recomendação do agente:** A, porque ...
- **Melhor argumento contra:** ...
- **Escolha:** B, por <integrante>, em DD/MM.
- **Por que B:** o raciocínio que levou à escolha, como foi montado no debate.
- **Impactos positivos:** ...
- **Impactos negativos:** ...
- **Efeito nos testes e medições:** que números mudam, em que configurações, e o que esperar no CSV.
- **Como verificamos:** teste ou medição que confirma o efeito esperado.
- **Em palavras simples:** explicação sem jargão, ou com cada termo explicado.
```

**As recomendações dos guias também passam por esse processo.** Mesmo quando o integrante fica com o que o guia sugere (ex.: 1 página = 1 bloco, blocagem unspanned, áreas fixas por tipo de página), a decisão é debatida e ganha cartão: o relatório tem de justificar todas as escolhas, comparando com as preteridas. Os docs podem mudar depois: se uma decisão for revista, marque o cartão como `revista`, explique o motivo e atualize o doc correspondente.

Na resposta ao integrante, quando houver decisão no código, deixe um comentário curto apontando o cartão: `# D-P3-04: redistribui antes de fundir (ver docs/decisoes/P3.md)`.

---

## 4. Como explicar o que você fez

O grupo precisa estudar e fixar o conteúdo. Ao concluir cada passo relevante (um componente, um algoritmo, uma correção de bug), responda neste formato:

1. **O que foi feito:** arquivos e funções, em 2–4 linhas.
2. **Raciocínio:** por que desse jeito, passo a passo. Mostre a cadeia, não só a conclusão.
3. **Trade-offs:** o que se ganhou, o que se perdeu, e quando a outra opção seria melhor.
4. **Em palavras simples:** a mesma ideia para quem viu o assunto uma vez, com uma analogia ou um exemplo pequeno com números. Todo termo técnico é explicado em uma linha na primeira vez que aparece.
5. **Ligação com a teoria:** a seção do livro (SKS = Silberschatz–Korth–Sudarshan, E&N = Elmasri–Navathe) e a fórmula de custo, com os valores do nosso caso.
6. **Perguntas de fixação:** 2 ou 3 perguntas que o professor poderia fazer sobre isso, com resposta curta no fim.
7. **Pendências:** decisões abertas e o próximo passo.

Acrescente o item 4 e as perguntas do item 6 em `docs/estudo/Px.md` (um arquivo acumulado por integrante, para revisão antes da apresentação).

Seja honesto sobre incerteza. Se não tem certeza de que uma fórmula se aplica, ou de que um teste cobre o caso, diga.

---

## 5. Documentação interna

Escreva pensando em dois leitores: o colega que vai usar o seu módulo e o próximo agente de IA que vai abrir o arquivo sem contexto.

**Docstring de módulo** (no topo de cada arquivo de `cbd/`):

```python
"""Heap file (organização primária não ordenada). Camada 3, dono: P2.

Implementa: DataFile (cbd/contracts.py). Usa: BufferPool, SlottedPage.
Decisões: D-P2-05 (free-space map), D-P2-06 (compactação). Ver docs/decisoes/P2.md.
Invariantes: no máximo 1 página fixada por iterador; FSM coerente com free_space().
Custo esperado: scan = b_r leituras; insert = 1 leitura + 1 gravação (com FSM em cache).
"""
```

**Docstring de função pública:** o que faz, parâmetros com unidade (blocos, bytes, registros), o que levanta, quantos blocos lê/grava no caso típico.

**Comentários:** explicam o *porquê*, não repetem o código. Marque decisões com `# D-Px-NN:` e simplificações conscientes com `# LIMITAÇÃO:` dizendo o teto e como melhorar.

**`ai/Px/ESTADO.md`** (documento para a IA da próxima sessão). Atualize ao fim de toda sessão:

```markdown
# Estado da parte Px — atualizado em DD/MM
## Pronto (com teste que prova)
## Em andamento
## Falta
## Requisitos do enunciado cobertos
| Enunciado § | Onde está | Teste que prova |
## Desvios ou dúvidas sobre o contrato
## Armadilhas conhecidas / bugs abertos
## Decisões provisórias a rever
```

A tabela "Requisitos do enunciado cobertos" é obrigatória: é como o grupo confere que a proposta foi atendida.

---

## 6. Registro de uso de IA (exigido pelo enunciado §7.2.3)

Já definido na arquitetura geral §11. Você mantém isso **durante** o trabalho, não no fim:

| Arquivo | O que você faz |
| :--- | :--- |
| `ai/Px/prompts.md` | registre cada pedido relevante do integrante, com data e ferramenta/modelo |
| `ai/Px/raw/` | **antes de refinar**, salve a primeira versão completa de cada componente importante como você a gerou (ex.: `raw/btree_core_v1.py`). Deve ser executável, para o teste ingênuo × refatorado. Nunca edite depois |
| `ai/Px/review.md` | cada defeito achado no código da IA: arquivo e linha, categoria, como foi achado, correção. Registre **antes** de corrigir |
| `ai/Px/stress.md` | testes de estresse e resultado ingênuo × refatorado |

Se você errar e o erro for achado (por teste, revisor, contagem que não bate), isso vai para `review.md`. Não esconda nem "corrija de passagem".

---

## 7. Verificação: testes em níveis

Cada módulo passa pelos níveis abaixo, em ordem. Não pule para o nível seguinte com o anterior falhando.

| Nível | O que testa | Como |
| :--- | :--- | :--- |
| **N1. Unitário isolado** | a lógica do módulo sozinha | esquemas pequenos próprios; fixture `bp` (só camada 4) ou dublês da camada de baixo; casos de borda: vazio, 1 registro, bloco cheio, chaves repetidas, chave inexistente, valores nulos |
| **N2. Integração com a camada de baixo real** | o módulo usando a interface verdadeira | fixture `eng`; **contagens exatas** com cache frio (`eng.cold()`), comparadas com cálculo feito à mão e escrito no comentário do teste |
| **N3. Com a base de dados** | resultados corretos em dados realistas | gere uma base pequena com `python -m bench.build --scale 0.002 --output data/tiny --results results/tiny` (sem `--results`, o comando sobrescreve o `results/base_stats.csv` versionado), carregue pelo catálogo e compare com a referência SQLite (`SQLiteReference.result` × `output_signature`, ver `docs/dados_e_carga.md`) |
| **N4. Estimado × medido** | o algoritmo faz o que a teoria diz | compare a função `*_cost` com `eng.snapshot()`. Toda diferença relevante precisa de explicação (ex.: hits no buffer, overflow, arredondamento) |
| **N5. Estresse** | robustez | `@pytest.mark.stress`, conforme guia P1 §6: muitas operações aleatórias, M mínimo, skew total, oráculo simples (dict, lista ordenada, SQLite) |

Em todos os níveis:

- `bp.pinned() == 0` ao fim (a fixture `eng` já verifica).
- Testes ficam em `tests/test_<módulo>.py`; integração em `tests/integration/`.
- Teste que prova contagem tem o número esperado calculado e **explicado** no comentário. Teste do tipo "roda sem erro" não prova nada.
- Rode a suíte inteira (`python -m pytest -q`) antes de dizer que algo está pronto, não só o arquivo novo.
- Relate o resultado real ao integrante: quantos passaram, quais falharam, com a saída.

---

## 8. Verificação: code review e revisores independentes

### 8.1 Autorrevisão

Antes de entregar qualquer componente, revise o próprio diff contra:

1. O checklist do enunciado §7.1 / arquitetura §11: leitura fora do buffer; pin sem unpin; página alterada sem dirty; busca binária sobre registros em vez de blocos; B+ sem ocupação mínima, sem folhas encadeadas ou sem split até a raiz; hash que ignora overflow; joins com estruturas Python maiores que a área reservada; offsets de campos variáveis fora do bloco.
2. A seção "Armadilhas da IA" do guia da parte.
3. As verificações por `grep` do §2.
4. As assinaturas da arquitetura geral: nomes, parâmetros, tipos de retorno, exceções.

### 8.2 Revisores independentes (subagentes)

Depois da autorrevisão, **lance subagentes revisores independentes**. Eles existem para achar o que você não vê, por isso precisam ser neutros:

- **Contexto limpo.** Não passe seu raciocínio, suas conclusões nem "eu acho que está certo". Passe só: o enunciado, a arquitetura geral, o guia da parte, os arquivos alterados e a tarefa do revisor.
- **Só leitura.** O revisor não corrige nada; ele aponta.
- **Um foco por revisor**, rodando em paralelo quando a ferramenta permitir:
  1. **Corretude e bugs:** casos de borda, erros de lógica, exceções, estados inconsistentes.
  2. **Aderência à proposta e aos contratos:** o código faz o que o enunciado e a arquitetura pedem? Falta algum requisito? Alguma assinatura diverge?
  3. **Contagem de I/O e memória:** cada leitura passa pelo buffer? Os contadores batem com as fórmulas? Há pin vazando? Alguma estrutura Python passa de M páginas?
  4. **Qualidade dos testes:** os testes provam o que dizem provar? Que bug passaria por eles?

Modelo de pedido ao revisor:

```text
Você é um revisor independente e cético de um SGBD didático (COS480, UFRJ).
Não confie em comentários nem docstrings: verifique no código.
Leia: proposta.md, docs/implementacao/00_ARQUITETURA_GERAL.md, docs/implementacao/<Px>.md.
Revise SOMENTE estes arquivos: <lista>.
Foco: <um dos quatro focos acima>.
Para cada problema, informe: arquivo:linha, gravidade (crítico/alto/médio/baixo),
o que está errado, cenário concreto que reproduz (entrada -> resultado errado),
e qual trecho do enunciado ou do contrato é violado, se houver.
Se possível, escreva um teste pytest que falha por causa do problema.
Não altere arquivos. Se não achar nada, diga o que verificou.
```

No Claude Code, use a ferramenta de subagentes. Em ferramentas sem subagentes, abra uma sessão nova, sem o histórico desta, e use o mesmo modelo de pedido.

### 8.3 Depois da revisão

1. **Verifique cada achado** antes de aceitar: reproduza com um teste. Revisor também erra.
2. Achado confirmado: registre em `ai/Px/review.md`, escreva o teste que falha, corrija, mostre o teste passando.
3. Achado descartado: diga ao integrante por que foi descartado.
4. **Apresente os achados ao integrante** e explique cada bug no formato do §4: por que aconteceu, como foi achado, como foi corrigido. É exatamente isso que o professor avalia.

---

## 9. Fechamento de cada parte: resumo das decisões e post-mortem

### 9.1 Resumo das decisões

Quando o integrante considerar a parte implementada (ou um marco grande dela), **mostre no chat um resumo de todas as decisões tomadas** e grave a mesma tabela no topo de `docs/decisoes/Px.md`:

```markdown
## Resumo das decisões — Px (atualizado em DD/MM)
| Decisão | Escolha | Preterida(s) | Impactos positivos | Impactos negativos | Como afeta os testes e medições |
| :--- | :--- | :--- | :--- | :--- | :--- |
| D-P3-01 Fan-out e tamanho da chave | chave fixa, n = ... | chave variável | ... | ... | altura medida = ..., busca custa h blocos |
```

Depois da tabela, explique em poucos parágrafos como as decisões se combinam: quais reforçam umas às outras, quais têm custo escondido, quais o integrante deve esperar que o professor questione. Esse resumo é a base do que cada um apresenta em sala e do item a do relatório técnico.

### 9.2 Post-mortem

Em seguida, escreva com o integrante `ai/Px/post_mortem.md`. O P1 consolida todos no `POST_MORTEM.md` da raiz (guia P1 §7). Estrutura:

```markdown
# Post-mortem — Px (<nome do integrante>)
## O que foi construído
## Prompts-chave (até 3) e o código bruto devolvido (link para ai/Px/raw/)
## Defeitos que a IA cometeu
| Defeito | Categoria | Como foi achado | Correção | Teste que impede a volta |
## Refatorações e por quê (desempenho, corretude, estresse)
## Evidência: ingênuo × refatorado (tabela de ai/Px/stress.md)
## Estimado × medido: onde bateu, onde não bateu e por quê
## Decisões que mudaríamos hoje
## Lições sobre usar IA nesta parte
```

Escreva com fatos e números. Inclua o que deu errado: é a parte mais valiosa.

---

## 10. Git

Conforme arquitetura geral §10.2:

- Uma branch por pessoa e funcionalidade: `p3/btree-split`, `p5/hash-join`.
- Commits pequenos, com mensagem dizendo o que e por quê. Referencie cartões de decisão quando houver (`D-P3-04`).
- PR para o `main` com revisão de quem consome a interface. `python -m pytest -q` passando.
- Mudança em `contracts.py` ou na arquitetura geral: PR separado, avisado ao grupo.
- Não versione `data/`, `refs/` (livros, direitos autorais) nem bases geradas.

---

## 11. Checklist de fim de sessão

Antes de encerrar, confira e diga ao integrante o que ficou de fora:

- [ ] Decisões da sessão debatidas no chat e registradas em `docs/decisoes/Px.md`, sem provisórias esquecidas; guias atualizados onde a escolha divergiu deles.
- [ ] Se a parte (ou um marco) foi concluída: resumo das decisões mostrado no chat e gravado (§9.1).
- [ ] Código com docstrings e marcas `# D-Px-NN` onde houver decisão.
- [ ] Testes dos níveis aplicáveis escritos e a suíte inteira passando (ou falhas relatadas).
- [ ] Verificações por `grep` do §2 limpas.
- [ ] Revisores independentes rodados e achados tratados.
- [ ] `ai/Px/prompts.md`, `raw/` e `review.md` atualizados.
- [ ] `ai/Px/ESTADO.md` atualizado, incluindo a tabela de requisitos cobertos.
- [ ] Resumo de estudo acrescentado em `docs/estudo/Px.md`.
- [ ] Explicação final no formato do §4.

---

## 12. O que não fazer

- Decidir sozinho algo que o §3.1 classifica como decisão.
- Entregar código que você não consegue explicar linha a linha ao integrante.
- Inventar números, medições ou resultados de teste. Se não rodou, diga que não rodou.
- Medir com dublês. Dublê é só para testar lógica.
- Ler blocos "só para conferir" por fora do buffer, inclusive em testes de contagem.
- Implementar a parte de outro integrante. Se a camada de baixo falta, use um dublê e avise.
- Reescrever código de outro integrante sem ele saber. Aponte o problema e deixe a correção para ele.
- Apagar ou reescrever o histórico de `ai/`.
