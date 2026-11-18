# Pendências externas — P4

Registro das verificações e alinhamentos que exigem entregas de P1–P3, P5
ou P6. Os testes locais com dublês verificam a lógica da P4. A validação
física e o aceite entre partes são registrados quando executados.

| P4 | Dependência | Falta verificar ou combinar | Estado |
| :--- | :--- | :--- | :--- |
| #54 · Adaptador | P1 #37, P2 #43 e #46, P3 #49 | Rodar `tests/test_access.py::test_access_with_real_heap_and_btree`, conferir tuplas, uma leitura física por operação no cenário controlado e zero pins. | Aberta. A parte local passou em 30 testes. Na cópia com a camada 4 do PR #2, o teste físico foi pulado porque `cbd.files.catalog` está ausente. |
| #58 · Ordenação externa | P1 #37 e P2 #39, #40, #43 e #46 | Rodar `test_external_sort_with_real_storage` com codec, layout, reserva `WORK` e `Catalog.temp()` reais. Conferir 12 leituras e 6 gravações no caso de seis blocos e `M = 3`, além de cinco passadas no caso de 50 blocos. | Implementação e testes locais concluídos. Aceite físico em aberto. |
| #58 · Consumo no sort-merge | P5 #66 | Exercitar dois fluxos ordenados intercalados com `mem` parcial e fechamento antecipado no join. Alinhar a referência do guia P5 a `_bfr_from_sample`, auxiliar que não consta da interface pública. | Dois sorts intercalados passaram no teste com dublês. Aceite com o operador P5 aberto. |
| #59 · Seleções e custo físico | P1 #37, P2 #46, #48, #52, P3 #49 e #53 | Validar igualdade e faixa nos caminhos aplicáveis; comparar saída com SQLite e leituras frias com a estimativa, inclusive ordenação de `RID`s e cenário de hash primário marcado `N/A`. | Aberta. |
| #59 · Bancada | P6 #36 | `bench.run.measure` encaminha `sort_ptrs` aos estimadores das linhas secundárias de igualdade (com `False`) e de faixa (com `False` ou `True`). A arquitetura recebe esse argumento apenas nos operadores. Combinar o encaminhamento e como registrar o custo das variantes no CSV. | Aberta. |
| #60 · Contagem, políticas e integração | P1 #37, P2 #39, #40, #43, #46, #48 e #52, P3 #49 e #53, P6 #16 e #36 | Com heap real, executar `test_bnl_exact_cost` e `test_bnl_flooding`, observar LRU × MRU com varreduras repetidas e medir a variante indexada em caminhos reais. Integrar os resultados à bancada. | Aberta. |
| #60 · BNL na matriz a–e | P6 #36 e decisão do grupo | O §5.1 pede BNL em todas as configurações. Os caminhos da arquitetura selecionam INL automaticamente sempre que o interno é pesquisável. A bancada pode medir o BNL adicional construindo lados `SCAN` sobre as mesmas organizações físicas e preservando seus rótulos. | Aberta. |
| #64 · Estresse físico | P1 #37, P2 #39, #40, #43 e #46 | Rodar `pytest -q -s -m stress` com heap, codec e temporários reais. Registrar leituras, gravações e tempos a frio e explicar a diferença para os custos estimados em `stress.md`. | Cinco cenários locais passaram com SQLite. Os dois físicos foram pulados. Em cópia com P1, a execução parou na ausência de catálogo. |
| #67 · Aceite do grupo | P1 #72, P6 #36 e os aceites externos acima | Incorporar a P4 no `main`, validar os operadores e estimativas pela bancada e incluir o post-mortem da P4 na consolidação da raiz. | Fechamento local registrado. Aceite e publicação em aberto. |

Última conferência: 18/11/2026. As issues de armazenamento acima ainda estavam
abertas. Ao retomar uma linha, registrar o commit da pilha testada, o comando,
o resultado e as contagens que confirmam o aceite.
