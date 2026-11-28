# Revisão do código gerado por IA — P5

## R-P5-09 — Contabilização dentro de assert (27/11/2026)

Em `raw/hash_join_primary_v1.py`, no laço `_block_join`, a inclusão no
orçamento após troca de lote estava dentro de `assert budget.add(record)`.
Em Python -O a chamada desaparece e o próximo lote pode exceder a página.
Encontrado na autorrevisão antes da correção. Mover a chamada para fora do
assert e manter a contabilização também no modo otimizado.

## R-P5-10 — Prefixo errado na asserção de temporários

Na primeira versão de `tests/integration/test_p5_joins.py`, a fixture
buscava `__tmp*`, mas `Catalog.temp` usa `__temp`. O teste de abandono
comparava todo o diretório e passou. A conferência do catálogo revelou que
a asserção adicional da fixture não detectaria um arquivo temporário órfão.
Corrigido para o prefixo utilizado pela camada de armazenamento.

## Revisão independente da entrega (27/11/2026)

| Foco | Evidência e decisão |
| :--- | :--- |
| Corretude | 94 testes sem estresse e 1 de estresse. Revisor também relatou 360 consultas na pilha real comparadas ao laço duplo, com quatro variantes, payload variável, duplicatas, NULL e zero pins/temporários. Sem novo defeito confirmado. |
| Recursos | 95 testes no estado examinado. Conferiu WORK, bytes, fan-out, encerramento e estimativas. Sem novo defeito confirmado. B+ real ausente. |
| Contratos | 83 testes. Guia antigo prescrevia ValueError para M insuficiente no sort-merge, enquanto operador e estimador sinalizam NotApplicable. D-P5-11 preserva a exceção da bancada e corrige o guia, com teste de M=3 e dois heaps. |
| Testes | 96 testes antes da ampliação. Reproduziu duas lacunas com mutações em memória. Os testes anteriores aceitavam rejeitar sort-one e ignorar HASH se só s estivesse organizado. Acrescentados ambos os lados em sort-one e HASH exclusivamente em s. |

### R-P5-11 — Cobertura de orientação física

As mutações de cobertura do revisor afetavam `_plan` nos dois operadores. O
caso misto agora roda com M=3 e um sort real do P4 em ambas as orientações.
O caso HASH em s valida multiconjunto, variante e temporários só em r com
ambas as orientações do construtor. São regressões da cobertura, sem alteração
da classificação de produção.

O revisor repetiu os quatro casos após a ampliação. Todos passaram.
O mutante que rejeita sort-one falhou nas duas orientações, e o mutante
que ignora HASH em s falhou com build r e s. As mudanças foram só em memória.
Em execução com Python -O, os cenários reais de overflow e estresse passaram
(2 testes), verificando que a contabilização da R-P5-09 é executada.

## R-P5-08 — Precedência do atalho direto sem hashes incompatíveis (24/11/2026)

- **Categoria:** qualidade dos testes; gravidade média.
- **Origem:** revisor independente `hash65_test_setup`, somente leitura e contexto limpo.
- **Arquivo antes da correção:** `tests/test_hash_join.py:606–615`, `test_in_memory_precedes_primary_hash_reuse`.
- **Lacuna:** o teste de precedência só usa N/seed iguais. Os casos incompatíveis usam M=4 e não cabem no atalho; uma classificação que verifique incompatibilidade antes de capacidade não seria detectada.
- **Cenário detector:** mesmo registro/caminho PRIMARY HASH nos dois lados, M=64, mas s com N=13 ou seed=19. D-P5-01/07 requerem in-memory e nenhum temporário/bucket scan.
- **Correção de cobertura planejada antes de alterar:** preservar o caso original e acrescentar N e seed incompatíveis ao teste de precedência; conferir também custo sem escrita. Não é um defeito confirmado no núcleo atual, que ainda não implementa as variantes da #65.
- **Verificação:** implementação correta com **80 passed in 0.23s**. A mutação que antecipa incompatibilidade foi detectada nos dois novos casos (**2 failed, 1 passed, 77 deselected in 0.15s**), sem editar produção. O revisor conferiu a correção e executou os sete casos novos com **7 passed, 73 deselected**. Suíte completa: **216 passed, 3 skipped in 36.58s**. Nenhum novo achado nessa alteração; variantes físicas ainda pendentes.

## R-P5-01 — Referência do laço retém registro do lote anterior (23/11/2026)

- **Categoria:** memória/corretude da contabilização lógica.
- **Arquivo e linha da versão bruta:** `ai/P5/raw/hash_join_grace_v1.py:177` (laço) e `:181` (troca de lote), que permanece imutável.
- **Arquivo de produção:** `cbd/exec/hash_join.py`, mesma função na primeira versão.
- **Como foi achado:** autorrevisão identificou que a variável `match` sobrevive ao `for`; reprodução com `test_block_join_releases_previous_chunk_records`, usando tuplas com acompanhamento de vida útil e entrada gerada sob demanda.
- **Entrada → defeito:** M=4, bfr=1, seis registros no construtor, lotes de dois. Na abertura das três varreduras do probe havia **[2, 3, 3]** registros vivos do construtor; o limite era dois. `chunk.clear()` sozinho não libera a referência retida em `match`.
- **Teste antes da correção:** **1 failed, 27 passed in 0.23s** em `tests/test_hash_join.py`; o teste compara com [2,2,2] e falhou.
- **Correção planejada (registro antes de alterar):** liberar explicitamente a referência de `match` ao terminar cada sondagem, antes de limpar/substituir o lote. Preservar a versão bruta.
- **Verificação após correção:** `test_block_join_releases_previous_chunk_records` passou com [2,2,2]; arquivo inteiro com **33 passed in 0.18s** (inclui os novos testes de custo).

## R-P5-02 — Cartões novos sem incorporação ao relatório (23/11/2026)

- **Categoria:** aderência documental/integração.
- **Arquivos:** `docs/decisoes/P5.md` (novos D-P5-01 a D-P5-04), `docs/relatorio/02_opcoes_de_projeto.md` e índice gerado `docs/relatorio/gerado/decisoes.md`.
- **Como foi achado:** suíte completa após acrescentar os cartões: **2 failed, 167 passed, 3 skipped in 40.89s**. Falharam `test_generated_parts_are_up_to_date` e `test_every_decision_card_is_justified_in_the_design_chapter`.
- **Defeito:** os cartões estavam registrados, mas ainda sem justificativa citada no capítulo 2; o índice versionado ainda tinha 23 cartões, enquanto o gerador encontrou 27. O enunciado §7.2.2.a exige justificativa de todas as opções no relatório.
- **Correção planejada (antes de alterar):** acrescentar ao capítulo 2 a seção das escolhas P5 já confirmadas e regenerar o índice com o comando existente. Preservar texto e decisões dos outros integrantes e não alterar os testes.
- **Verificação após correção:** acrescentada a seção P5 confirmada ao capítulo 2 e regenerado o índice pelo comando existente. `tests/test_report.py`: **16 passed, 1 skipped in 0.19s** (quatro cartões nesta rodada; regenerar novamente se houver decisões posteriores).

## R-P5-03 — Vazio com bloco alocado rejeitado (23/11/2026)

- **Categoria:** corretude/contrato; gravidade média.
- **Origem:** revisor independente `review_contracts`, contexto limpo e somente leitura.
- **Arquivo e linhas antes da correção:** `cbd/exec/hash_join.py:106`, `_capacity` em `:93–94`, guarda de `_join_pair` em `:191–192`.
- **Reprodução confirmada:** tabela com zero registros, um bloco alocado e bfr=0; `test_empty_with_allocated_block` levanta ValueError em vez de devolver []. Estatística é válida após deletes sem liberar o bloco.
- **Correção proposta antes de alterar:** reconhecer vazio por n_records no mesmo plano usado por operador e custo, evitando scans; escolha apresentada ao integrante.
- **Verificação anterior:** falha reproduzida na rodada **14 failed, 42 passed in 0.53s** (inclui demais cenários novos de revisão).
- **Verificação após correção:** escolha D-P5-05 confirmada; cardinalidade propagada em `_Source`, plano `empty` comum ao operador e custo. Regressão passou; suíte N1 final **73 passed in 0.21s**, suíte completa **209 passed, 3 skipped in 35.91s**. Revisão final de contratos sem novo achado N1.

## R-P5-04 — Média fracionária não admite nem um registro (23/11/2026)

- **Categoria:** memória/corretude; gravidade média.
- **Origem:** revisor independente `review_correctness`, contexto limpo e somente leitura.
- **Arquivo e linhas antes da correção:** `cbd/exec/hash_join.py:95–97`.
- **Reprodução confirmada:** M=3, bfr=0,5, uma linha de cada lado e dois blocos por lado. `test_sparse_fractional_bfr` deveria emitir [(1,100,1,200)], mas o fallback levanta ValueError porque floor(1×0,5)=0.
- **Causa:** bfr é ocupação média do arquivo, não o inverso do tamanho físico de um registro. Uma tabela esparsa pode ter menos de um registro por bloco e ainda aceitar um registro não estendido em uma página.
- **Correção proposta antes de alterar:** rever D-P5-03 com mínimo de um registro por página para bfr positivo menor que um. A decisão foi apresentada ao integrante; aguardar escolha, sem tratar como já aprovada.
- **Verificação anterior:** falha reproduzida na rodada **14 failed, 42 passed in 0.53s**.
- **Verificação após correção:** Enzo confirmou D-P5-06; capacidade usa floor((M−2)×max(1,bfr)), mantendo rejeição de bfr não positivo/não finito para construtor não vazio. Casos de bfr=0,125/0,5/0,99/1/1,5 passaram; suíte N1 final **73 passed in 0.21s**. Capacidade em bytes permanece pendente de N2.

## R-P5-05 — Custo positivo para join vazio sem leitura (23/11/2026)

- **Categoria:** contagem/modelo de custo; gravidade média.
- **Origem:** revisor independente `review_resources`, contexto limpo e somente leitura.
- **Arquivo e linha antes da correção:** `cbd/exec/hash_join.py:258–260`.
- **Reprodução confirmada:** r vazio, s=30 registros/15 blocos, M=4. Operador faz zero scans e temporários; custo retorna 15 transferências em auto/r e 123 com build=s. Diferença independe do cache.
- **Correção proposta antes de alterar:** mesmo plano reconhecer entrada vazia e devolver Cost(0), também com orientação forçada; escolha apresentada ao integrante.
- **Verificação anterior:** 12 casos parametrizados em `test_cost_and_operator_empty_input` falharam na rodada **14 failed, 42 passed in 0.53s**, por custo incorreto ou ausência de atalho quando há bloco alocado.
- **Verificação após correção:** D-P5-05 confirmada; custo retorna reads=0, writes=0 e seeks=0 para o mesmo plano empty que não abre scans/temporários. Todos os 12 casos passaram nas duas orientações e com zero/um bloco retido; suíte completa final verde.

## R-P5-06 — Lacunas dos testes de fronteira e metadados (23/11/2026)

- **Categoria:** qualidade dos testes; alta para limite do construtor, média para posições de coluna e auto escolher s.
- **Origem:** revisor independente `review_tests`, contexto limpo e somente leitura; relatou três mutações em memória que sobreviveram aos 33 testes iniciais.
- **Arquivos:** `tests/test_hash_join.py`, fixture `case`, `test_orientation` e `test_underestimated_build_restarts_before_output` na primeira versão.
- **Cenários descobertos:** duplicar a capacidade do build ainda causava excesso no caso antigo de 25 registros, não expondo a regressão; todas as colunas de join estavam na posição zero; nenhum caso não vazio obrigava auto a escolher s.
- **Correções de cobertura:** testes novos de limite exato (quinto registro versus capacidade quatro), colunas em posições/nomes diferentes nos dois lados, auto escolhendo s e caso em que isso evita temporários. Nenhum teste antigo foi enfraquecido ou removido.
- **Verificação:** repetidas as três mutações só em memória, sem alterar produção: dobrar capacidade foi detectado pelo teste de fronteira (1 failed, 57 deselected); forçar coluna zero foi detectado nos quatro casos (4 failed, 54 deselected); forçar auto=r foi detectado (1 failed, 57 deselected). As falhas são deliberadas para confirmar que os testes rejeitam os mutantes. As falhas pendentes na produção continuam sendo R-P5-03/04/05.

**Atualização posterior:** R-P5-03/04/05 corrigidos após confirmação; a rodada final passou. A frase anterior registra o estado durante a execução dos mutantes.

## R-P5-07 — Fronteira fracionária não distingue floor de ceil (23/11/2026)

- **Categoria:** qualidade dos testes/memória; gravidade média.
- **Origem:** revisor independente `final_tests`, contexto limpo e somente leitura.
- **Arquivo antes da correção:** `tests/test_hash_join.py`, `test_underestimated_build_boundary`, com apenas bfr=2 e capacidade quatro.
- **Como foi achado:** mutação somente em memória que troca floor por ceil em `_capacity` passou nos 66 testes. Com produto inteiro, os dois arredondamentos coincidem; os casos bfr<1 exercitavam só uma linha.
- **Cenário detector:** M=4 e bfr=1,6: limite floor(2×1,6)=3. O quarto registro deve causar migração de in-memory para GRACE; ceil permitiria quatro.
- **Correção de cobertura planejada antes de alterar:** parametrizar a fronteira com capacidades esperadas calculadas à mão, preservando o caso antigo bfr=2. Nenhuma mudança na implementação ou na escolha D-P5-03/06.
- **Verificação:** a implementação correta passou com **69 passed in 0.20s** após os primeiros três casos novos; o mutante floor→ceil foi detectado em três fronteiras (**3 failed, 1 passed, 65 deselected in 0.15s**). A parametrização foi ampliada para confirmar o teto exato e passou com **73 passed in 0.21s**. O revisor final de testes conferiu a alteração, confirmou que preserva o caso antigo e relatou novamente que os três excessos fracionários rejeitam o mutante.

## Revisão final da etapa N1 — 23/11/2026

Quatro revisores independentes, contexto limpo e somente leitura, após as
correções de produção:

| Revisor | Foco | Resultado |
| :--- | :--- | :--- |
| `final_correctness` | Corretude | Sem novo defeito N1; 66 testes passaram na versão de cobertura então disponível. Relatou 400 casos aleatórios adicionais comparados com laço duplo, sem gravar arquivos. |
| `final_resources` | I/O e memória | Sem novo defeito N1; conferiu reservas, referências, cursores, temporários e custo/plano; 66 testes passaram. |
| `final_contracts` | Contratos e escopo | Sem violação confirmada no marco N1; 66 testes passaram. |
| `final_tests` | Qualidade dos testes | Encontrou R-P5-07; após ampliar a fronteira confirmou o tratamento sem enfraquecer a cobertura e executou 73 testes passando. |

Os ensaios aleatórios do revisor são informação de revisão, não bateria N5
persistida. Nenhum desses resultados comprova memória física ou I/O real. A
suíte final executada pelo agente principal teve **209 passed, 3 skipped in
35.91s**. Todos os achados confirmados deste marco foram tratados; as limitações
de integração continuam declaradas, sem marcar a issue #61 como concluída.
