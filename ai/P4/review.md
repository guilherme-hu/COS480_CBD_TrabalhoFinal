# Revisão do código gerado — P4

## 18/11/2026 — Adaptador (#54)

### O cursor reavaliava um caminho já validado

- **Arquivo:** `raw/access_v1.py`, `read_ordered`, `lookup`, `_ordered` e
  `_matching` (linhas 47–91). Categoria: corretude.
- **Detecção:** revisão independente de corretude. `JoinSide` é mutável.
  Se o chamador pedia `read_ordered` com `PRIMARY` e depois mudava o caminho
  para `SCAN`, consumir o cursor disparava `KeyError`. A revisão reproduziu
  um efeito equivalente ao mudar a coluna de um `lookup` secundário.
- **Correção:** resolver o arquivo e, quando houver, o índice na criação
  do cursor. Abrir o cursor da camada 3 somente na primeira iteração.
  `test_cursor_keeps_the_validated_access_path` demonstra os dois casos.

### Consultas com múltiplos resultados e buscas redundantes

- **Arquivo:** `tests/test_access.py`, `test_access_paths`. Categoria:
  cobertura de corretude e contagem.
- **Detecção:** revisões de contrato e I/O. Os casos de busca esgotada
  tinham no máximo uma linha. O teste também aceitava `fetch` repetido
  porque só verificava o nome do método. Um operador que perdia detalhes
  ou buscava o mesmo ponteiro duas vezes poderia passar.
- **Correção:** testar busca primária e secundária com duas linhas de mesma
  chave e conferir quais ponteiros foram buscados, na ordem e uma vez cada.

### Integração com a camada 3

- **Arquivo:** `tests/test_access.py`, `test_access_with_real_heap_and_btree`.
  Categoria: cobertura de contagem física.
- **Detecção:** revisão independente de I/O. O teste inicial resfriava o
  cache uma vez e verificava apenas os resultados e o total de pins final.
- **Correção:** usar cache frio por operação e declarar as leituras previstas
  para o heap de um bloco e a B+ de uma folha. Conferir pins após cada
  consumo ou fechamento. Os testes com armazenamento real seguem pendentes.

### Despacho da varredura física com caminho primário

- **Arquivo:** `tests/test_access.py`, `test_scan_always_reads_physical_order`.
- **Detecção:** a revisão dos testes confirmou em memória que substituir
  `scan()` por `range()` só em `PRIMARY` B+ passava com os casos iniciais.
  Isso pode trocar as dicas de cache usadas pela varredura física.
- **Correção:** exercitar `read_all` com caminho primário em hash e B+ e
  exigir exatamente uma chamada a `scan()`.

### Ponteiro opaco no caminho secundário

- **Detecção:** a revisão dos testes confirmou em memória que converter o
  ponteiro para `int` antes do `fetch` passava nos dublês iniciais. Isso
  quebraria o `RID` de heap/hash e a tupla da B+ (arquitetura §5.4).
- **Correção:** usar os dois formatos de ponteiro nos dublês de índice e
  verificar que `fetch` recebe cada ponteiro no formato original.
