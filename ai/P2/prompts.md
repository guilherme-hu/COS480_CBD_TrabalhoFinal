# Prompts — P2 (Guilherme)
| Data | Ferramenta/modelo | Pedido |
| :-- | :-- | :-- |
| 19/11 | Claude Code (Opus 5.5) | Gerar dois materiais de estudo a partir do SKS e do E&N: (1) tudo do gerente de armazenamento para a P2; (2) transações, concorrência e recuperação, ligados à P2. Didáticos, em português, com diagramas. Resultado: `docs/estudo/P2_armazenamento.html` e `docs/estudo/P2_transacoes.html`. |
| 22/11 | Claude Code (Opus 5.5) | Seguir uma issue por vez, debatendo cada decisão com prós e contras, padrão dos dados e trade-offs de transações/concorrência/recuperação. Conferir o enunciado (PDF × md). |
| 22/11 | Claude Code (Opus 5.5) | D-P2-01: explicar melhor as opções A (diretório, SKS) e C (prefixo de tamanho, E&N). Escolha: C. Pedir ao P6, por issue, a troca de 4 para 2 B no estimador. |
| 22/11 | Claude Code (Opus 5.5) | D-P2-02: debater nulos, campos comuns e opcionais e o null bitmap, com as opções medidas na base. Escolha: S (sentinela, sem bitmap). Criar o PR da parte inicial. |
| 22/11 | Claude Code (Opus 5.5) | D-P2-03: debater o campo multivalorado (LIST), com as opções dos livros medidas na base. Escolha: C (itens com tamanho em 1 B, sem contagem). Dividir o site de estudo em uma página por decisão. |
