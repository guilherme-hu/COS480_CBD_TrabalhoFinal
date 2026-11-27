# Pendências externas — P4

Conferido em 26/11/2026 com `origin/main` em `3d0eb70`. A camada 4 de P1
e o codec, as páginas com slots, heap, temporários, catálogo, hash primário
e índice hash secundário de P2 já estão disponíveis. A branch da P4 passou a
usar essa base localmente.

| P4 | Dependência | Falta verificar ou combinar | Estado |
| :--- | :--- | :--- | :--- |
| #54 · Adaptador B+ | P3 #49 e #53 | Rodar `test_access_with_real_btree` e validar `read_ordered` e `lookup` com índice B+ secundário real. | Heap, hash primário e hash secundário já validados a frio. O teste real da B+ pula pela ausência de `cbd.files.btree_file`. |
| #58 · Consumo no sort-merge | P5 #66 | Exercitar o sort-merge da P5 usando `mem` parcial e fechamento antecipado. Alinhar a referência do guia P5 a `_bfr_from_sample`, que não consta da interface pública. | A P4 já intercalou dois sorts com `TempFile` real em seis frames WORK e fechou um antes do fim. Falta a integração com o operador ausente da P5. |
| #59 · B+ e faixa por índice | P3 #49 e #53 | Medir B+ primária e secundária a frio. Validar filtros de 1% e 50% no índice e comparar leituras com e sem ordenação de RIDs em faixas secundárias. | Heap e hash foram comparados com SQLite. Faixas por hash primário aparecem como `N/A` na bancada. |
| #59 · Argumentos dos estimadores | P6 #36 | Separar os argumentos do operador e do estimador ou aprovar outra forma de representar o custo com `sort_ptrs`. | Reproduzido `TypeError` no estimador da igualdade secundária real. A bancada também envia o argumento ao estimador de faixa. A assinatura da arquitetura reserva `sort_ptrs` aos operadores. A igualdade hash secundária funciona pela API da P4 e mede dois blocos no teste controlado. |
| #60 · Casos B+ e cobertura da matriz | P3 #49 e #53, P6 #36 e decisão do grupo | Rodar as variantes restantes de B+, inclusive a configuração e. Para cobrir BNL em a–e mesmo quando o interno tem índice, a bancada pode medir `SCAN` sobre as mesmas organizações e preservar seu rótulo. | 20 cenários de nested-loop suportados passaram na bancada com SQLite, com as três consultas e configurações a–d disponíveis. BNL e INL já têm contagem física controlada. |
| #60 · Política oficial | P6 #16 | Rodar a escolha de política na matriz e registrar a política da operação. | No teste da P4, BNL com cinco frames DATA mediu 21 leituras com LRU e 17 com MRU. |
| #67 · Aceite do grupo | P1 #72, P6 #14 e #36, P3/P5 acima | Incorporar a P4 à `main`, rodar a matriz física completa e incluir o post-mortem da P4 na consolidação da raiz. | A branch P4 está publicada e a integração física disponível está documentada em `stress.md`. O aceite no `main` e a rodada completa estão abertos. |

## Verificações que a `main` já liberou

- #54. Varredura de heap: um bloco. Hash primário: um bloco. Hash secundário:
  dois blocos, com zero pins depois de fechar o cursor.
- #58. Sort físico em `M = 3` validado em seis e cinquenta blocos. No caso
  de seis blocos, 12 leituras e seis gravações. Dois sorts em fluxo dividiram
  seis frames WORK sem deixar pins.
- #59 e #60. Os caminhos disponíveis foram exercitados em uma base P6 de
  escala `0,002`, por meio de `bench.run.measure` e também da API da P4.
- #64. Estresse físico concluído com SQLite. BNL revisado: 22.650 leituras,
  zero gravações. Sort: 1.050 leituras, 900 gravações. Ver [stress.md](stress.md).

## Como retomar o aceite

1. Quando a P3 publicar os arquivos B+, execute `test_access_with_real_btree`
   e repita os cenários de seleção e junção que hoje exigem B+ primária ou
   secundária. Confira as tuplas com SQLite, a contagem a frio e zero pins.
   Na configuração d, falta a orientação que usa o índice do lado detalhe.
   Na configuração e, falta o caso com índices secundários em ambos os lados.
2. Quando a P5 publicar o sort-merge, verifique que o operador usa o orçamento
   `mem` de cada fluxo e os fecha em erro ou interrupção. Use como regressão
   `test_external_sort_interleaves_two_real_work_streams`.
3. Quando a P6 alinhar os argumentos de `sort_ptrs`, exercite igualdade e
   faixa secundárias na bancada e confira a cobertura BNL a–e. Execute a
   matriz completa e o comparativo de políticas na escala prevista pela P6.
   Mantenha as leituras medidas e as estimadas identificadas na saída.
4. Registre o commit da pilha, o comando executado, o resultado e as
   contagens de cada aceite. Após a integração ao `main`, valide a suíte e
   repasse as evidências para o relatório e o post-mortem do grupo.

Os comandos e resultados do estresse físico estão em [stress.md](stress.md).
Os testes por requisito estão no [estado da P4](ESTADO.md). Os motivos,
alternativas e parâmetros da P4 estão nas
[decisões da P4](../../docs/decisoes/P4.md).
