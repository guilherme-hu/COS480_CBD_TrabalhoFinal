# P3 — Especificação atual para implementação incremental

Atualizada em 28/11/2026. Destinatário: agente que implementará P3 com Gabriel. **Especificação parcial, baseada nas escolhas aprovadas D-P3-01 a D-P3-10. Componentes isolados implementados; árvore completa ainda indisponível.** Substitui o roteiro anterior deste arquivo. O guia histórico `P3_arvore_bplus.md` não é fonte técnica atual.

## 1. Como usar esta especificação

1. Ler os [cartões P3](../decisoes/P3.md) para justificativas e limites; este documento operacionaliza essas escolhas, não cria outras.
2. Antes de usar uma função de P1/P2, conferir no código seu nome, os parâmetros que recebe e o que devolve: [contracts.py](../../cbd/contracts.py), [catálogo P2](../../cbd/files/catalog.py), [codec](../../cbd/records/codec.py), [SlottedPage](../../cbd/records/slotted_page.py) e [buffer P1](../../cbd/storage/buffer_pool.py). As descrições deste plano não substituem essa conferência.
3. Implementar uma unidade pequena de comportamento e seus testes, preservando os formatos aprovados. Não implementar tudo num único passo.
4. **Não preencher uma lacuna com um default silencioso.** Consultar a seção 8; uma proposta não aprovada não pode virar formato persistente. Uma lacuna dependente de nova decisão não impede código independente dela.
5. Quando autorizado a começar código, registrar a primeira versão dos componentes importantes em `ai/P3/raw/` antes de refinar; nunca reescrever raw. Registrar revisão/defeitos conforme AGENTS.md.
6. Ao terminar cada unidade, atualizar este documento e [ESTADO](../../ai/P3/ESTADO.md) com comportamento entregue, provas e lacunas remanescentes. Novas decisões precisam primeiro de aprovação e cartão.

**Código incompleto permitido:** quando Gabriel pedir implementação incremental, partes não decididas podem ter lacunas explícitas. Um método bloqueado pode levantar `NotImplementedError` indicando a lacuna; isso é provisório de desenvolvimento, não a API final de rejeição de manutenção. Não devolver resultados fictícios, não retornar iterador vazio por falta de implementação e não anunciar integração pronta. Evitar alocar/gravar estado persistente antes de descobrir uma lacuna que impede completar a operação. Não conectar uma classe incompleta à bancada como se funcionasse.

## 2. Escopo e responsabilidades

| Caso | Folha | Ordem e leitura |
| :--- | :--- | :--- |
| Primária UNIQUE | Registro completo codificado por P2 | Campo escolhido; duplicata recusada; devolve `(RID, registro)` |
| Primária NOT UNIQUE | ID u32 de 4 B big-endian + registro P2 | `(campo, ID)`; devolve `(RID, registro)` |
| Secundária NOT UNIQUE | Chave codificada + quantidade total + início do grupo | Uma entrada por valor; search devolve RIDs; range devolve `(chave, RID)` |
| Auxiliar do secundário | Um RID do arquivo de dados por slot | Grupos consecutivos por chave; percurso por slots/next limitado pela quantidade total |

- D-P3-01: bulk loading bottom-up, seguido somente por leitura; sem insert/delete dinâmicos, splits/redistribuições/fusões por manutenção. Não implementar reconstrução automática.
- D-P3-02: um campo da tabela por organização primária. ID auxiliar não é segundo campo escolhido da tabela.
- D-P3-03/05: desempate apenas na primária NOT UNIQUE; contador por árvore na ordem da carga, desde 1 até 2³²−1, sem reiniciar por chave. Gerador em memória; IDs persistidos nas entradas/separadores. Sem próximo contador para futuras inserções.
- D-P3-04: primária UNIQUE e NOT UNIQUE usam RID físico bloco u32 + slot u16, 6 B no formato little-endian de P2. Não gravar cópia do próprio endereço no registro. Após carga, não mover slots/registros de forma que invalide RIDs. Reconstrução pode invalidá-los; índices dependentes precisam ser refeitos.
- D-P3-06: internos compactos compartilhados; D-P3-07: folhas primárias reutilizam P2; D-P3-08: grupos externos compartilhados no secundário.

### Arquivos e interfaces públicas

Mapa previsto na arquitetura existente, agora com componentes parciais e esqueletos: `cbd/files/btree_node.py` (bytes/operações de nós), `btree_core.py` (navegação/construção comum), `btree_file.py` (DataFile) e `btree_index.py` (Index). Separar essas responsabilidades sem criar hierarquias genéricas ou APIs públicas novas para possibilidades hipotéticas. Helpers privados podem ser escolhidos durante a implementação.

| Classe | Chamadas reais do catálogo e contrato |
| :--- | :--- |
| `BTreeFile` | `create(bp, name, schema, spec)` / `open(...)`; atributos `schema`, `spec`, `ptr_size`; `bulk_load(recs)`, `scan`, `search(key)`, `range(lo, hi, lo_inc=True, hi_inc=True)`, `fetch(ptr)`, `encode_ptr`, `decode_ptr`, `stats`, `close` |
| `BTreeIndex` | `create(bp, name, spec, key_field, data)` / `open(...)`; atributo `spec`; `bulk_load(pairs)`, `search(key)`, `range(lo, hi, lo_inc=True, hi_inc=True)`, `stats`, `close` |

As assinaturas completas e retornos são os de contracts.py. `lo/hi=None` indicam extremos abertos. As interfaces existentes também incluem insert/delete/reorganize: sua rejeição/capacidade final está em L05, não removê-las unilateralmente. O código de P4 fecha cursores antecipadamente; implementar iteradores com liberação garantida de recursos.

## 3. Base de P1/P2 a reutilizar

- Configuração adotada: B=4096, uma página por bloco, registros unspanned. Código de P1 permite outros tamanhos; não transformar isso em nova comparação experimental. Calcular limites a partir de B.
- `RecordCodec.encode/decode`: respeitar esquema e campos variáveis/NULL; não alterar o codec. Os bytes do registro são diferentes da representação ordenável das chaves.
- `key_size/encode_key/decode_key`: INT/DATE=4 B, FLOAT=8 B, texto conforme size; encode_key preserva a ordem com os ajustes por tipo. Não comparar diretamente campos numéricos little-endian do registro.
- `SlottedPage`: cabeçalho 10 B, slots de 4 B, `next`, `get`, `insert_at`, `free_space`, `max_record`. Não implementar outra página de registros.
- Catálogo: ordena registros B+ por campo antes da carga e pares `(chave, referência)` para secundários; fornece esquema/spec e arquivo de dados ao abrir. Não assumir que isso resolve validação do conteúdo recebido por uma chamada direta.
- Acesso às páginas sempre pelo buffer; somente storage faz leitura/gravação física. P3 não conta manualmente I/O em substituição a P1.

## 4. Formatos em bytes aprovados

### 4.1 Internos — D-P3-06

Definir `s = quantidade usada de separadores`, `k = tamanho de um separador naquela árvore`.

| Offset | Bytes | Informação |
| :--- | :--- | :--- |
| 0 | 2 | Quantidade de separadores, u16 little-endian |
| 2 | 4 | Bloco do primeiro filho, u32 little-endian |
| 6 + i·(k+4) | k | Separador i, i=0…s−1 |
| 6 + i·(k+4) + k | 4 | Bloco do filho à direita desse separador, u32 little-endian |

Campos consecutivos; sem slots, pai, irmão interno ou cópia local do tamanho da chave. Filho é um bloco inteiro do mesmo arquivo, folha ou interno, nunca bloco+slot. A árvore aberta deve fornecer tamanho/perfil e nível conhecidos; origem persistente/validação desse contexto é L02.

- Primária UNIQUE e secundária NOT UNIQUE: `k = key_size(campo)`.
- Primária NOT UNIQUE: `k = key_size(campo) + 4`; separador = chave ordenável seguida de ID u32 big-endian.
- Separador é o mínimo da subárvore imediatamente à direita; comparação igual segue à direita. O mínimo de um filho interno é o mínimo de sua subárvore, não seu primeiro separador armazenado.
- Uso: `6 + s·(k+4) ≤ B`; `s_max=(B−6)//(k+4)`; fan-out máximo=`s_max+1`.
- Em B4096, k4/8/12: máximos de filhos 512/341/256. Não são ocupação mínima ou preenchimento.
- Exemplo k4, s2: contador nos bytes 0–1; filho0 em 2–5; separador0 em 6–9; filho1 em 10–13; separador1 em 14–17; filho2 em 18–21.

### 4.2 SlottedPage compartilhada — sem alterações em P2

| Offset | Bytes | Informação |
| :--- | :--- | :--- |
| 0 | 2 | n_slots |
| 2 | 2 | free_end |
| 4 | 2 | holes |
| 6 | 4 | next, bloco da próxima página na mesma sequência; zero=fim |
| 10 + 4·i | 4 | slot i: offset u16 + comprimento u16 |

Controle little-endian; diretório cresce do início, entradas ocupam espaço a partir do fim. Ordem lógica é a dos slots; não a ordem física dos bytes. Durante a construção normal sem remoções, holes=0. next das folhas liga folhas; next das auxiliares liga auxiliares, sem cruzar as duas sequências.

### 4.3 Folhas primárias — D-P3-07

- UNIQUE: entrada = bytes de `codec.encode(registro)`.
- NOT UNIQUE: entrada = ID u32 big-endian de 4 B + mesmos bytes do registro. Na leitura, retirar prefixo antes de `codec.decode`.
- Sem segunda cópia da chave, do RID ou do comprimento dentro da entrada. Acessar campo com o codec existente e obter sua chave ordenável; comparar ID depois na NOT UNIQUE.
- Preencher slots em ordem de campo/ID. `insert_at(n_slots, entrada)` permite append lógico; ele não sabe ordenar os dados por conta própria.
- Encaixe pela entrada inteira: máximo de registro `B−14` UNIQUE e `B−18` NOT UNIQUE; em B4096, 4082/4078 B. Erro RecordTooLarge para registro que ultrapasse o limite correspondente. Política de fechar página antes de cheia é L03.
- RID público vem do bloco/slot da entrada, inclusive quando a entrada possui prefixo; fetch devolve somente o registro decodificado.

### 4.4 Folhas secundárias e auxiliares — D-P3-08

Folha: SlottedPage, uma entrada por chave, com esta ordem lógica de bytes:

`encode_key(campo, valor) | quantidade TOTAL do grupo | início do grupo (bloco u32 + slot u16)`

**L01 fechada por D-P3-09:** quantidade total u32 little-endian, 4 B, domínio 1…2³²−1; recusar excesso sem wrap. Entrada de `K+10` B: chave em0…K−1, quantidade emK…K+3, bloco u32 LE emK+4…K+7, slot u16 LE emK+8…K+9. Em B4096, máximos físicos de 227 entradas K4 e 185 K8; preenchimento ainda é L03. Isso autoriza o formato, não comprova sua implementação.

Auxiliar: SlottedPage, um `data.encode_ptr(ptr)` por slot, com tamanho `data.ptr_size`. Para os arquivos atuais são 6 B; não reimplementar a codificação do arquivo de dados. Posição inicial bloco/slot aponta para o arquivo do índice; os RIDs armazenados apontam para o arquivo de dados. São contextos diferentes.

Grupos consecutivos por chave, preservando a ordem de entrada dentro do grupo. Catálogo atual ordena também por RID; a consulta não exige pesquisa binária por RID. Não exigir ordem global de RIDs entre chaves distintas.

Procedimento de leitura de um grupo (quantidade decodificada conforme D-P3-09):

1. Receber localização inicial e quantidade total já decodificadas.
2. Ir diretamente ao bloco/slot indicado; não começar no slot 0 se o grupo começa no meio da página.
3. Decodificar e produzir um RID por slot, diminuindo o número restante.
4. Ao acabar a página, seguir next e começar no slot 0 somente se ainda faltarem RIDs.
5. Parar ao produzir a quantidade total, mesmo se houver mais slots/next. Cadeia que acaba antes é inconsistente; não devolver sucesso parcial silencioso. Política exata de exceções/validação é L05.

Auxiliares podem conter fim de um grupo e começo de outro. Não precisam armazenar a chave em cada RID. Em B4096, RID6+slot4 e header10 permitem no máximo 408 RIDs por página; isso não decide preenchimento nem tamanho total do grupo.

### 4.5 Bloco 1 — conteúdo lógico aprovado, bytes ainda pendentes (D-P3-10)

Prefixo comum aos três perfis:

`magic_version | profile | key_idx | key_type | key_size | root | height | first_leaf | n_leaf_pages | n_internal_pages | n_leaf_entries`

Somente no secundário, acrescentar `n_aux_pages | n_references`. profile distingue PRIMARY_UNIQUE, PRIMARY_NOT_UNIQUE e SECONDARY_NOT_UNIQUE; esses nomes não são strings persistidas já escolhidas. Os primários compartilham o mesmo formato de metadados, distinguido pelo perfil.

- key_idx: posição desde zero no esquema da tabela; no secundário, esquema de data. key_type/key_size conferidos com o contexto recebido do catálogo; não copiar esquema inteiro. key_size exclui o ID; largura de separador derivada: +4 apenas na primária NOT UNIQUE.
- root/first_leaf são blocos do próprio arquivo, sem slot. height inclui folhas; não acrescentar is_leaf nos nós para substituir a altura.
- n_leaf_entries conta entradas das folhas: registros nos primários, chaves distintas no secundário. n_references conta todos os RIDs auxiliares; não presumir igualdade ao total da tabela se NULL foi excluído.
- Contagens por classe são resumos para estatísticas, não listas de endereços. Não duplicar high_water/livres/B de P1 nem contador de próxima inserção/ID. data.ptr_size e seus codecs continuam vindo do arquivo de dados.
- Ler meta via buffer ao abrir e manter contexto no objeto; não introduzir leitura automática do bloco1 a cada consulta. Conferência de chave/perfil não garante detectar alterações arbitrárias do resto do esquema ou associação errada do arquivo de dados.
- **Não criar struct ainda:** larguras/códigos/offsets, literal de identificação/versão e representação de vazio aguardam fechamento de L02. Nem strings de perfil nem proposta raiz/altura/primeira folha zero viram formato sem aprovação. Falhas de carga/publicação e estatísticas finais seguem L04/L05.

Critérios para testes futuros: ida/volta dos três perfis; resumo persistido versus páginas reais; raiz folha/interna; key_idx2 no esquema `(dre,nome,cr)`; chave/perfil incompatíveis; duas chaves e cinco referências; NULL fora do secundário; leitura meta fria contada e pins liberados. Não fixar bytes esperados antes de aprová-los.

## 5. Comportamentos a implementar e dependências

| Componente | Passos / invariantes | Dependências abertas |
| :--- | :--- | :--- |
| Bytes dos internos | Ler/escrever contador, primeiro filho, pares; conferir offsets/limites; comparar separadores completos | Não precisa de meta persistida em testes isolados que forneçam k e contexto explicitamente |
| Entradas primárias | Codificar/decodificar os dois formatos; extrair chave/ID; aplicar limites; preencher SlottedPage mantendo ordem | Não precisa de escolha de preenchimento para testar entradas/páginas isoladas |
| RID público e fetch de página | Reproduzir formato P2, localizar slot, remover prefixo quando aplicável e decodificar registro | Objeto de arquivo persistente e tratamento de referências inválidas dependem L02/L05 |
| Percurso descendente | Em cada interno, escolher filho pela comparação completa e igualdade à direita; usar nível para interpretar página | Perfil e altura fornecidos pelo chamador em teste; abertura real depende L02 |
| Igualdade/faixas primárias | Procurar início da faixa pelo campo; na NOT UNIQUE incluir/excluir todos os IDs de uma chave conforme os limites; seguir next até fim da faixa | Não inventar sentinelas persistidas; representação exata dos limites em memória e validação em L05 |
| Scan primário | Percorrer folhas em ordem e emitir `(RID, registro)` sem vazar views; preservar identidade dos slots | Primeira folha/árvore vazia de L02; iteradores e pins em L04/L05 |
| Leitura auxiliar | Procedimento da seção 4.4, retornando referências sem interpretar registros de dados | Formato da entrada fechado D09; política de erro/pins em L04/L05 |
| Construção das folhas | Receber entradas ordenadas; IDs na primária NOT UNIQUE; ligações para a frente; limites de entrada | Política dos grupos finais/preenchimento L03; memória L04 |
| Construção do secundário | Agrupar chaves iguais sem materializar implicitamente lista ilimitada; registrar primeiro slot e quantidade; gerar folhas distintas do fluxo auxiliar | L03/L04; formato fechado D09; não prometer algoritmo com memória limitada antes de defini-lo |
| Construção dos níveis internos | A partir dos filhos, representar cada mínimo e bloco; construir de baixo para cima até raiz | Distribuição dos últimos grupos L03; armazenamento de descritores L04; publicar meta em L02 |
| create/open/close | Implementar chamadas reais do catálogo; persistir/reabrir o mesmo formato; limpar recursos | L02: não gravar um bloco1 inventado para declarar create/open prontos |
| stats/recusa de manutenção | Cumprir forma consumida pelo catálogo/P4; recusar operações antes de alteração parcial | L05; não copiar expectativas de manutenção do guia antigo |

A organização dos helpers privados não é outra API pública. Não criar um motor genérico separado para cada layout nem três implementações de internos. Também não pressupor que toda operação já esteja implementada só porque existe uma assinatura.

## 6. Buffer, recursos e integração

- Usar `bp.page(fid,bno,ptype)` para escopo de leitura quando apropriado ou pin/unpin em try/finally; `bp.new_page` devolve página fixada e dirty; liberar a página mesmo em erro.
- Marcar alterações dirty. SlottedPage não faz isso; usar Page.mark_dirty ou unpin(dirty=True) conforme operação real de P1.
- `SlottedPage.get` retorna memoryview da página, não registro independente. Não devolver view após liberar pin; copiar bytes quando forem sobreviver ao pin ou sofrer movimentação por compactação/inserção.
- Fechar iteradores quando o consumidor abandona a consulta; não depender da coleta de lixo para unpin.
- Sem acesso direto a fm.read/write; metadados de P3 também passam pelo buffer. Bloco0 permanece exclusivo de P1.
- Mapeamento exato de tipos de página às áreas do buffer e número de pins simultâneos são parte de L04. Não adotar silenciosamente uma política herdada do guia antigo.
- Catálogo de P2 precisa recusar carga incremental B+ e Table precisa barrar mutações incompatíveis antes de alterar dados/índices, inclusive heap/hash com secundário B+ somente leitura. Não modificar código de colegas sem alinhamento.
- `cbd/exec/access.py::fetch_cost` ainda cobra altura da árvore para qualquer BTREE; alinhar o estimador de P4 ao fetch físico direto de D-P3-04. Comentário Ptr em contracts.py também prevê chave/seq. O repasse de Ptr em execução já é opaco.
- P6 ainda prepara variante incremental e outros fatores históricos; sua presença no código não altera D-P3-01. A integração da bancada não é entregue por stubs.

## 7. Estatísticas: contrato existente versus lacunas

A arquitetura existente pede ao menos `blocks`, `records`, `overflow_blocks`, `levels`, `leaf_blocks`, `buckets`, `meta_blocks`. Catalog.analyze usa leaf_blocks para a B+ primária e calcula registros/blocos de dados e tamanho médio pelo codecP2. Não redefinir isso para usar capacidade máxima teórica.

Contabilizar páginas de referências do secundário como auxiliares, não folhas. A convenção de altura da árvore vazia, contagens totais/distintas de índice e sua persistência/validação precisam ser fechadas em L02/L05. Não retornar zeros fictícios para satisfazer consumidores antes da implementação.

## 8. Lacunas controladas — decisões ainda não aprovadas

| ID | Falta decidir | Não fazer antes de fechar |
| :--- | :--- | :--- |
| L01 — Contador secundário — FECHADA | D-P3-09: u32 LE de 4 B, domínio1…2³²−1; formato K+10 B | Abertura/construção/validação continuam L02–L05; não confundir decisão com código entregue |
| L02 — Bloco1 e abertura — PARCIAL | D-P3-10 aprova campos e organização lógica. Faltam larguras/códigos/offsets, valor de magic_version, vazio e validação concreta; estatísticas/publicação articuladas a L04/L05 | Não publicar create/open funcionais nem inventar bytes, root=0/altura=0 ou outro default |
| L03 — Preenchimento/ocupação | Quanto ocupar folhas/internos e como distribuir últimos grupos/raiz com entradas variáveis; restrições para entradas grandes | Não adotar fill0.9/min_fill0.5 nem finalizar um builder multinível sem tratar grupos finais |
| L04 — Construção e memória | Estratégia para descritores entre níveis, orçamento da carga, pins simultâneos, áreas para páginas e limpeza de falhas | Não guardar toda a árvore/grupo em listas por conveniência; não alegar respeito ao orçamento sem verificar |
| L05 — Comportamento compartilhado | Recusa de mutação/recarga/reorganize, exceções/validação de referências e limites/NULL, semântica final das estatísticas e alinhamento de consumidores | Não remover contratos unilateralmente, fazer alterações parciais ou esconder falhas com respostas vazias |

O estado de L02 pertence ao item do grupo 4 no Notion, não impede aprovar o formato dos nós/folhas. Com L01 fechada, o formato dos internos e das três folhas está decidido; metadados, preenchimento e construção continuam itens separados. Implementação e decisão têm estados separados. A lista é um checklist de dependências, não compromisso de cinco novas discussões extensas: podemos fechar convenções relacionadas juntas.

## 9. Sequência incremental e critérios de entrega

### Estado executável atual — primeira entrega parcial

| Componente | Implementado e verificado | Ainda não entregue |
| :--- | :--- | :--- |
| `btree_node.py::_InternalPage` | Formato D06, leitura/escrita, limite físico, busca pelo separador completo com igualdade à direita | Construção/ocupação da árvore, identificação de níveis por metadados |
| `btree_node.py::_PrimaryEntries` | Registro P2, prefixo opcional D05/D07, extração de chave completa, limites de tamanho | Gerador global de IDs na carga, divisão/distribuição das folhas, recusa UNIQUE na carga |
| `btree_node.py::_encode_rid/_decode_rid` | Bytes D04, também usados por encode_ptr/decode_ptr de BTreeFile | API final de referência inválida em L05 |
| `btree_core.py::_descend` | Navegação por contexto explícito (fid/raiz/altura/k/área), sem ler folha; no máximo um pin | Descobrir esse contexto ao abrir bloco1; limites de busca por campo L05 |
| `btree_core.py::_fetch_primary` | Fetch físico e retirada de prefixo antes do codec, sem devolver view | Ligação a objeto de arquivo aberto e validação pública L02/L05 |
| `BTreeFile`, `BTreeIndex`, `expected_height` | Superfícies conhecidas com NotImplementedError referindo lacunas; sem respostas fictícias | Criar/abrir/carregar/consultar/estatísticas/fechar; estimativa de altura; rejeição final de manutenção |

**Não confundir testes de componentes com integração pronta.** `tests/test_btree_node.py` e `tests/test_btree_partial.py` testam bytes literais, nós cheios, prefixos/limites/RIDs, descida multinível, fetch, páginas encadeadas de teste e liberação em erros. As páginas/raízes dos testes são preparadas explicitamente; não existe builder, scan público, leitor de grupos auxiliares ou BTreeFile.open funcional. Reabertura testada é a de P1, não a árvore completa.

Os testes dos esqueletos provam somente que falham claramente antes de I/O/consumo de entradas. Não provam os comportamentos futuros. Para as lacunas ainda abertas L02–L05, manter os critérios abaixo como checklist até aprovar resultados esperados; não criar asserts de valores escolhidos por conveniência.

**Efeito sobre teste existente:** a presença de `btree_file.py` ativa `tests/test_access.py::test_access_with_real_btree`, anteriormente ignorado por ausência do módulo. Ele continua falhando em create por L02. Não foi alterado nem enfraquecido. Não anunciar suíte ou integração verdes enquanto essa dependência persistir.

### Próximas entregas

| Entrega | Pode começar com escolhas atuais? | Evidência mínima antes de chamar de pronta |
| :--- | :--- | :--- |
| A. Formato interno e entradas primárias | Sim, quando Gabriel pedir código | Testes de bytes/limites; k4/8/12; round-trip do codec/ID; erros de tamanho |
| B. Folhas primárias e fluxo auxiliar em páginas reais | Sim para unidades isoladas; políticas completas seguem lacunas | SlottedPage + buffer real; slots/next; grupos começando no meio e atravessando páginas; pins liberados |
| C. Arquivo persistente mínimo | Depois L02; builder limitado deve declarar seu alcance e ser aprovado como entrega parcial | Criar/carregar/scan/fetch/fechar/reabrir com registros reais, sem formato temporário escondido |
| D. Árvore multinível e acessos | Depois L03/L04, completando limites L05 | Separadores/mínimos corretos, igualdade/faixas e duplicatas entre folhas, grupos finais e reabertura |
| E. Secundário completo e integração | Depois L02 e fechamento das dependências acima; L01 já fechada | Uma entrada por chave, referências e grupos completos, fetch no arquivo real e comparação com referência |

Não reimplementar componentes já entregues quando avançar de etapa: estender e testar os mesmos formatos. Lacunas devem ficar localizadas, com seu ID e motivo, sem uma infraestrutura genérica de placeholders. Testes com dublês provam lógica isolada, não medições de I/O.

### Verificação por comportamento

- Comparar multiconjuntos de registros, não apenas contagem; UNIQUE recusa valores repetidos e NOT UNIQUE preserva todas as ocorrências.
- Testar igualdade/faixas com extremos abertos, inclusivos/exclusivos, chave ausente e duplicatas atravessando folhas/níveis. Confirmar propagação de mínimos no builder.
- No secundário, verificar ausência de chave repetida e equivalência entre RIDs devolvidos e registros do arquivo de dados. Testar fronteira de grupo/página sem vazar grupo seguinte.
- Conferir RID→fetch antes/depois de reabrir; não prometer estabilidade após reconstrução.
- Toda operação/iterador, inclusive erro/abandono, termina sem pins vazando. Operação não suportada não pode modificar parte da tabela.
- Medições com pilha real e cache frio; custos esperados calculados e explicados. Separar página de folha, interno, auxiliar e meta. Níveis aplicáveis e suíte completa antes de declarar entrega pronta, conforme AGENTS.md.
- Antes de cada etapa, definir casos de borda relevantes; não escrever testes que apenas copiem a implementação.

## 10. Fontes e acompanhamento

Os cartões D-P3-01…10 contêm as justificativas: enunciado vigente, SKS §§13.2.2,14.3.1,14.3.5,14.4.1,14.4.4 e E&N §§17.1.3,17.3.2,17.6.1–2, mais decisões/código de P1/P2. Arquitetura/contratos existentes definem consumidores; divergências estão explicitadas acima, não resolvidas por copiar o guia antigo.

[Mapa de decisões no Notion](https://app.notion.com/p/4269ab021b6d81508de1d7317021236f): mesmos 23 itens/cinco grupos, com estado da decisão separado da implementação. A entrega parcial acima não aprova as lacunas; o estado executável deste documento é mais específico que o estado amplo de decisão no mapa.
