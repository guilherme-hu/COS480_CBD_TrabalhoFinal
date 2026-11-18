# Pendências externas — P4

Registro das verificações e alinhamentos que exigem entregas de P1–P3, P5
ou P6. Os testes locais com dublês verificam a lógica da P4. A validação
física e o aceite entre partes são registrados quando executados.

| P4 | Dependência | Falta verificar ou combinar | Estado |
| :--- | :--- | :--- | :--- |
| #54 · Adaptador | P1 #37, P2 #43 e #46, P3 #49 | Rodar `tests/test_access.py::test_access_with_real_heap_and_btree`, conferir tuplas, uma leitura física por operação no cenário controlado e zero pins. | Aberta. A parte local passou em 30 testes. Na cópia com a camada 4 do PR #2, o teste físico foi pulado porque `cbd.files.catalog` está ausente. |
| #58 · Ordenação externa | P1 #37 e P2 #39, #40, #43 e #46 | Usar o codec, o layout, a reserva `WORK` e `Catalog.temp()` reais. Conferir blocos lidos/gravados, orçamento e limpeza em `M = 3`. | Aberta. |
| #58 · Consumo no sort-merge | P5 #66 | Exercitar dois fluxos ordenados intercalados com `mem` parcial e fechamento antecipado no join. Alinhar a referência do guia P5 a `_bfr_from_sample`, auxiliar que não consta da interface pública. | Aberta. |
| #59 · Seleções e custo físico | P1 #37, P2 #46, #48, #52, P3 #49 e #53 | Validar igualdade e faixa nos caminhos aplicáveis; comparar saída com SQLite e leituras frias com a estimativa, inclusive ordenação de `RID`s e cenário de hash primário marcado `N/A`. | Aberta. |
| #59 · Bancada | P6 #36 | `bench.run.measure` encaminha `sort_ptrs` ao estimador de faixa; a assinatura da arquitetura recebe esse argumento apenas no operador. Combinar o encaminhamento e como registrar o custo das variantes no CSV. | Aberta. |

Última conferência: 18/11/2026. As issues de armazenamento acima ainda estavam
abertas. Ao retomar uma linha, registrar o commit da pilha testada, o comando,
o resultado e as contagens que confirmam o aceite.
