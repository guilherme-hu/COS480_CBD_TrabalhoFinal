# Post-mortem — P2 (Guilherme En Shih Hu)

Parte P2 do SGBD didático: formato de registro, slotted page, função de hash, heap, temporário, hash estático
primário, índice hash secundário, `Table` e `Catalog`. Issues #39, #40, #41, #43, #46, #48, #52, #83, #55 e #62,
de 22/11 a 27/11. Ferramenta: Claude Code (Opus 5.5), em sessões interativas; uma noite em loop autônomo (#43).

## O que foi construído

| Módulo | Arquivo | O que faz | Decisões |
| :--- | :--- | :--- | :--- |
| Codec | `cbd/records/codec.py` | registro em bytes (fixos primeiro, variáveis com prefixo u16, NULL por sentinela, LIST com itens u8) e chaves de tamanho fixo que preservam a ordem | D-P2-01 a D-P2-06 |
| Slotted page | `cbd/records/slotted_page.py` | cabeçalho de 10 B, diretório de 4 B por slot, compactação preguiçosa, `insert_at`/`remove_at`/`next` para a B+ | D-P2-07, D-P2-08 |
| Hashing | `cbd/records/hashing.py` | `hash_value` estável (FNV-1a + splitmix64), injetivo em texto e tupla | D-P2-14 |
| Heap e temporário | `cbd/files/heap.py` | meta, mapa de espaço livre de 1 byte por bloco, RID de 6 B, `reorganize` por comando, `TempHeap` em WORK, prefetch no scan | D-P2-09 a D-P2-11, D-P2-19 |
| Hash primário | `cbd/files/hash_file.py` | bucket i no bloco 2 + i com cadeia pelo `next`, N pelo fator de carga 0,8, `bucket_of`, `scan_bucket` para o P5 | D-P2-14 a D-P2-17, D-P2-19 |
| Índice hash | `cbd/files/hash_index.py` | um `HashFile` de registros (chave, bloco, slot) na área INDEX | D-P2-18 |
| Tabela e catálogo | `cbd/files/table.py`, `cbd/files/catalog.py` | 4 relações em heaps CATALOG (Fig. 13.12 do SKS), forçadas em ordem; `Table` mantém heap e índices juntos | D-P2-12, D-P2-13 |

Números finais em escala 1, B = 4096: `curso` 2 blocos, `disciplina` 21, `aluno` 1 664, `inscricao` 3 449;
`aluno__hs_dre` com N = 2 084 e 0 overflow (busca = 1 leitura); índice hash de `aluno.dre` = 2 leituras por busca.
Testes da P2: 477 (unitários, integração, E2E e estresse, com os 10 da revisão final), mais 18 E2E com os operadores
do P4 na branch da #55; a suíte do grupo passa com 770 (27/11, antes dos E2E do P4).

## Prompts-chave e o código bruto devolvido

1. **Heap completo em loop autônomo (24/11, #43).** "Implementar o heap completo (HeapFile e TempHeap), revisar e
   corrigir até não achar mais nada relevante." Bruto: [`raw/heap_v1.py`](raw/heap_v1.py). Foi o componente com
   mais defeitos achados depois (contadores fora da meta, RID errado com MRU, pin preso com RID de tipo errado,
   mapa no meio dos dados): a revisão automática da própria IA não substituiu os revisores independentes.
2. **Hash estático primário (25–26/11, #48).** Debate de 4 decisões (D-P2-14 a 17) e depois a implementação.
   Bruto: [`raw/hash_file_v1.py`](raw/hash_file_v1.py). O bruto escolhia o ramo do `bucket_of` pelo tipo do
   valor, e não da coluna, embora o `ESTADO.md` da sessão anterior avisasse exatamente isso.
3. **Hash ingênuo, sem guia (26/11, #55).** Subagente com contexto limpo: "Implemente em Python um arquivo hash
   estático [...] Bucket cheio ganha blocos de overflow", sem acesso a `docs/` nem a `cbd/files/`. Bruto:
   [`raw/hash_file_naive_v1.py`](raw/hash_file_naive_v1.py). Acertou o overflow, errou o contrato e o custo.

## Defeitos que a IA cometeu

125 achados registrados em [`review.md`](review.md) até a revisão final da #62. A maior parte veio dos revisores
independentes (subagentes com contexto limpo); o resto, do `/code-review` e do `/ponytail-review`, de testes que
falharam, de medições e de autorrevisão. Os mais instrutivos:

| Defeito | Categoria | Como foi achado | Correção | Teste que impede a volta |
| :--- | :--- | :--- | :--- | :--- |
| `reorganize` apagava o original antes de a cópia estar segura: uma falha no meio perdia o arquivo | perda de dados | `/code-review high` do PR #102 | o temporário só é apagado depois da recarga; numa falha, fica no disco e a exceção diz o nome | `test_failed_reorganize_keeps_the_temp_copy` |
| `insert` do heap lia `page.bno` depois do `unpin`; com MRU o frame era reciclado e o RID devolvido apontava para outro bloco | RID errado (regressão) | 2ª revisão independente do heap | o bloco é lido antes do `unpin` | `test_insert_returns_the_right_rid_when_the_frame_is_recycled` |
| `insert_at` com uma view da própria página: o `compact` movia os bytes antes da cópia | dado corrompido em silêncio | 2 revisores da #40 | `data = bytes(data)` antes de reservar espaço | `test_insert_at_with_a_view_of_the_same_page_that_compacts` |
| `hash_value((1, (2, 3))) == hash_value(((1, 2), 3))`: a codificação de tupla e texto não era injetiva | codificação ambígua | revisão da #41 | texto com prefixo de tamanho, tupla com marca e contagem | `test_encoding_is_injective` |
| `bucket_of` escolhia o ramo pelo tipo do valor: `search(5.0)` numa coluna INT ia a outro bucket | bucket errado | autorrevisão da #48 | o ramo segue o tipo da coluna | `test_bucket_follows_the_column_type` |
| `load_table` em massa com índice UNIQUE e chave repetida carregava o heap e só falhava no índice | heap e índice incoerentes | revisor de corretude da #52 | a carga confere as chaves antes de tocar no heap | `test_bulk_load_with_a_repeated_key_leaves_nothing` |
| `drop_table` interrompido deixava linhas órfãs que voltavam numa tabela nova de mesmo nome | linha órfã ressuscita | revisores da #46 | as órfãs são apagadas ao abrir | `test_orphans_are_removed_on_open_and_do_not_leak_into_a_new_table` |
| a tabela `x__reorg` era truncada pelo `reorganize` de `x`; o índice `a.b` nascia em cima da tabela `a__b` | nome colide e trunca | revisores da #46 | nomes reservados e temporário com nome livre | `test_table_and_index_file_names_do_not_collide` |
| a meta do heap só era gravada no `close`: abrir sem fechar via 1 registro em vez de 10 | estado inconsistente | revisor de corretude da #43 | a meta é regravada a cada insert e delete | `test_open_without_close_sees_current_counters` |
| `delete(RID(b, "0"))` levantava `TypeError` com a página fixada | pin sem unpin | revisor da #43 | `_locate` valida os tipos e o delete usa `with bp.page` | `test_bad_rid_types_raise_keyerror_without_leaking_pins` |
| o `TempHeap` guardava uma lista com um int por bloco, fora das M páginas | memória fora da área WORK | issue #105 (revisão do P6) | contador de blocos | `test_temp_keeps_no_list_that_grows_with_the_blocks` |
| com prefetch, `scan_bucket` de 1 bloco lia 9; o scan do heap lia as páginas do mapa | leitura à toa | revisores da #48 e da #83, medido | `scan_bucket` sem cursor; os últimos k blocos de cada faixa sem cursor | `test_scan_bucket_does_not_prefetch_other_buckets`, `test_prefetch_does_not_read_the_map_pages` |
| o 1º insert do hash sem `buckets` estimava o N só com aquele registro (N = 4 e 248 blocos de overflow) | N mal dimensionado | revisores da #48 | recusa sem `buckets` nem `expected_records`; LIMITAÇÃO documentada | `test_insert_without_buckets_or_expected_records_is_refused` |
| depois do `reorganize`, o estresse refazia o oráculo a partir do próprio arquivo | teste tautológico | 3 revisores da #55, por mutante | conteúdo guardado antes e comparado depois | `test_p2_stress_100k_ops_match_dict` (mata o mutante R1) |
| `load_table` em massa com um registro grande demais no meio deixava o heap com os anteriores, o índice vazio e a tabela travada | estado inconsistente | revisor de corretude da revisão final (#62) | o `load_table` codifica e confere todos os registros antes de tocar no arquivo | `test_bulk_load_failure_leaves_the_table_empty_and_usable` |
| `reorganize` e `analyze` forçavam a linha do catálogo com os dados ainda sujos no buffer | ordem contra a D-P2-13 | revisores de contratos e de corretude da revisão final (#62) | o `analyze` grava os arquivos antes | `test_reorganize_writes_the_files_before_the_catalog_row` |

Padrão dos defeitos:
- **Estado entre camadas.** Os mais graves são de estado: o que fica no disco depois de uma falha no meio
  (perda de dados, órfãs, heap × índice) e o que vale depois de um `unpin` (o RID).
- **Testes que a própria IA escrevia.** Eles provavam menos do que diziam: 13 de 38 mutantes sobreviveram no hash
  da #48, 11 de 41 no índice da #52, e a 1ª versão do estresse comparava o arquivo com ele mesmo.
- **Docstrings.** Prometiam custos que o código não cumpria: "1 página fixada por iterador", insert e delete sem
  a leitura da meta.

### Revisão final (#62, 27/11)

Quatro revisores independentes sobre o código final: nenhum achado crítico ou alto. Os de I/O e de contratos
mediram e conferiram tudo; o de corretude achou 1 defeito médio (a carga em massa que falhava no meio) e 4 baixos;
o de testes plantou 123 mutantes, um por vez: 104 mortos, 7 equivalentes e 12 lacunas, cobertas por 10 testes novos
(`tests/test_p2_final_review.py`). Os achados confirmados foram corrigidos com um teste que falhava antes, menos
dois, registrados com o motivo: o `Table.delete` que não é atômico entre índices (só com um índice já corrompido) e
os ramos BTREE do catálogo (esperam a B+ do P3).

## Refatorações e por quê

- **Desempenho (CPU, não blocos):** `_first_dead` da slotted page passou de um `struct.unpack` por slot a um
  `unpack_from` do diretório inteiro (500 mil inserts de `inscricao`: 7,4 s antes).
- **Corretude:** `_rebuild` único para o `reorganize` do heap e do hash (ordem segura de apagar e recarregar);
  catálogo apagando órfãs ao abrir; `Table.insert` desfazendo o que entrou quando um índice recusa.
- **Contagem de blocos:**
  - a 1ª página do mapa passou a nascer com a meta (bloco 2), para o scan ter 1 seek;
  - o prefetch ficou só nos scans completos (D-P2-19);
  - a meta do heap passou a ser regravada a cada operação (1 pin, nenhum I/O a mais com a página no buffer).
- **Formato (revisões de decisão):** INT de 4 B (D-P2-05, −22% de blocos em `inscricao`) e cabeçalho de página
  de 28 para 10 B (D-P2-07). As duas vieram de perguntar "por que isso existe?" a escolhas que só seguiam o
  contrato ou o guia.

## Evidência: ingênuo × refatorado

Tabela completa em [`stress.md`](stress.md). Resumo (20 000 inserts + deletes, B = 4096, DATA = 3 frames):

| | Ingênuo (#55) | Raw v1 (#48) | Refatorado |
| :-- | :-- | :-- | :-- |
| estresse funcional (UNIQUE e NOT UNIQUE) | passa | passa | passa |
| chaves de `stats()` da arquitetura §6.3 | faltam 4 | todas | todas |
| abrir o arquivo, cache frio | 127 leituras | 1 | 1 |
| `insert` UNIQUE, cache frio | 2 leituras, 1 gravação | 3 / 2 | 3 / 2 |

E o estresse da P2 inteira: 100 mil inserts + deletes no heap e no hash (5 configurações), 36 combinações de
política × toss × B, `Table` + `Catalog` + índice com reabertura e `reorganize`, todos contra um `dict`. 17
mutantes plantados: 16 mortos, 1 equivalente no escopo.

## Estimado × medido

| Operação | Estimado | Medido (cache frio) | Bateu? |
| :-- | :-- | :-- | :-- |
| scan do heap | b blocos, 1 seek | b leituras, 1 seek (`aluno` 1 664) | sim, depois de mover a 1ª página do mapa para o bloco 2 (antes: 2 seeks num heap carregado por insert) |
| scan com prefetch k = 8 | b leituras, ⌈b / 9⌉ misses | idem (`aluno` 185 misses) | sim, depois de impedir a pré-leitura do mapa |
| busca no hash primário (`aluno.dre`) | 1 bloco (sem overflow) | 1 leitura | sim |
| busca pelo índice hash + fetch | 1 bloco do índice + 1 do heap | 2 leituras (1 INDEX + 1 DATA) | sim |
| insert e delete no hash | 1 leitura + 1 gravação (docstring da 1ª versão) | +1 leitura e +1 gravação (a meta, bloco 1) | não: a docstring e a D-P2-17 foram corrigidas |
| insert UNIQUE no hash | a cadeia do bucket | meta + cadeia inteira (checa duplicata) e 2 gravações | sim, com a meta |
| `scan_bucket` com prefetch | blocos do bucket | 9 blocos para um bucket de 1 (1ª versão) | não: o `scan_bucket` ficou sem cursor |
| `TempHeap` | gravar e reler todos os blocos | menos, quando a run cabe na área WORK (as páginas só vão ao disco ao sair do buffer) | documentado: o medido fica abaixo da fórmula |

## Decisões que mudaríamos hoje

*(Proposta do agente, a confirmar com o Guilherme.)*

- **D-P2-18, entrada do índice de 12 B.** Hoje o codec tem INT de 4 B; um inteiro de 2 B para o slot daria 10 B
  e 31 blocos a menos no índice de `aluno.dre`. Não muda nenhuma busca, que lê 1 bloco do índice de qualquer jeito.
- **Começar a desconfiar do guia mais cedo.** O INT de 8 B e o cabeçalho de 28 B vinham do contrato e do guia sem
  cartão; as duas revisões (23/11) mudaram números publicados e exigiram PRs separados e avisos ao P3 e ao P6.
- **Escrever o estresse antes, não no fim.** Ele exercitou caminhos (mapa com várias páginas, cadeia com chaves
  misturadas) que os testes por issue não cobriam.

## Lições sobre usar IA nesta parte

*(Proposta do agente, a confirmar com o Guilherme.)*

- **Revisão independente.** O revisor com contexto limpo achou o que o autor não via: 63 dos 116 achados vieram de
  subagentes revisores, inclusive no código que a IA já tinha "revisado até não achar mais nada" no loop autônomo.
- **Testes da IA.** Precisam de mutação: os testes que a mesma IA escreve junto com o código tendem a confirmar o
  código (oráculo tautológico, caminho que nunca roda). Plantar mutantes foi o que mostrou isso, com números.
- **Pedir a fonte.** A IA afirmava coisas "de memória" sobre os livros e sobre os SGBDs; abrir o PDF (página
  impressa) e a documentação oficial mudou recomendações (D-P2-11, D-P2-12).
- **Decidir antes de codar.** Debater cada decisão antes do código deixou cada escolha explicável, mas o custo foi
  tempo: 19 decisões em 6 dias, com um site de estudo por decisão.
- **Falhas do ambiente.** A ferramenta também erra no ambiente: heredocs do Bash que estragam barras invertidas,
  revisores que alteravam o repositório enquanto outro teste rodava. Isolar (scripts em arquivo, cópias fora do
  repositório) evitou resultados falsos.
