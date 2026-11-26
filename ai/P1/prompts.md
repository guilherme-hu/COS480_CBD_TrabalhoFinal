# Registro de uso de IA — P1 (Gerente de Arquivos e Gerente de Buffer Pool)

- **Responsável:** Vivian Maria da Silva e Souza (DRE: `123205793`)
- **Data:** 06/11/2026
- **Ferramenta / Modelo:** Google Antigravity (Gemini)

---

## Prompt 1 — Análise teórica e justificativa dos parâmetros da Seção 2

> P1: Vivian - Seção 2
> P2: Gui - Seção 3 e 4 
> P3: Biel - Seção 3 e 4
> P4: Phel - Seção 5
> P5: Enzo - Seção 5
> P6: Pozzato - Seção 6
>
> pode me ajudar a entender o que precisa ser feito para a minha parte? sou vivian
> o readme está trocado, considere o que está no prompt, minha parte é Gerente de Arquivos e Gerente de Buffer Pool
>
> Quais as duas “políticas de substituição” de página a serem comparadas, entre: LRU, MRU, FIFO e Clock (segunda chance) -> implementa duas, mas na hora do teste, escolhe uma (justificando)
> pensar nas operaçoes feitas para entender quais duas politicas sao melhores (basicamente LRU e MRU de acordo com o Milton)
> quero fazer essa análise com base no banco de dados, incluindo os parametros necessários, como cada uma dessas a ser definida (2.1 Gerente de Arquivos e 2.2 Gerente de Buffer Pool).

---

## Prompt 2 — Refinamento com tamanho único de bloco (4 KB), Contígua vs. Extents e fundamentação nos livros-texto (SKS e Elmasri & Navathe)

> na descrição do trabalho diz para testar em apenas 1 tamanho de bloco, nao? acredito nao ter necessidade de colocar os 3.
> por que a contígua é melhor que a extents?
> beleza, mas nesse cenário é melhor extends ou contíguo? E por que?
> sim, atualize o arquivo com todas as decisões e suas justificativas. Lembre de checar o enunciado para ter certeza que está tudo coberto
> mas com base em que material teorico voce se baseou para isso? voce pode olhar nos livros na mesma pasta

---

## Prompt 3 — Implementação da Camada 4 (`cbd/storage/`) e bateria de testes

> agora é necessário implementar, de acordo com o que já foi definido no arquivo

---

## Prompt 4 — Revisão de PR, correção do bug de auto-expulsão no `_prefetch` e adequação ao `AGENTS.md` (08/11/2026)

> tenhho essa porposta de mudanças: [...] referente a isso: @guilherme-hu commented: Pedi para IA revisar seu PR, e ela me deu o feedback abaixo. Uma coisa é que enquanto você fazia sua branch, eu criei umas documentações para guiar e organizar nosso repo, você poderia arrumar sua branch seguindo ela por favor?
>
> sim e quero manter apenas LRU e MRU

---

## Prompt 5 — 2ª Revisão do PR #2 (`reserve_work` + `clear`, `close_file`), casos de borda e documentação completa (09/11/2026)

> quem define os descritores de arquivos que passa no file_manager?
> como e por quem o buffer pool manager é chamado?
> pode me explicar os parametros dos metodos do buffer pool?
> temos eesses comentário da nosa pr: [...] 1. clear() dentro de reserve_work duplica frames [...] 2. FileManager.close/create/delete não conversam com o buffer [...] 3. O estresse não confere a contagem de I/O [...] 4. ai/P1/raw/buffer_pool_inicial.py não é código bruto da IA [...] 5. Menores [...]
> quero que voce cheque falhas ou casos de borda no codigo
> quero que voce garanta que está tudo documentado


---

## Prompt 6 — Fechamento: triagem das issues #38/#42/#45 e revisores independentes (22/11/2026)

- **Ferramenta / Modelo:** Claude Code (Claude Opus 5.5)

> analise o diretorio, veja as issues abertas ligadas a mim (mssvivian) no github e veja o que ainda precisa ser feito
>
> pode começar pelos passos 1 e 2, me passe os textos

- **Resultado:** a IA notou que 4 testes obrigatórios do guia §4 não tinham contagens nem o cálculo no comentário. O caso mais sério: `test_lru_vs_mru_victims` conferia só o conjunto final de residentes, e um LRU que expulsasse na ordem errada (1 e depois 3) passaria. Os testes foram reforçados na branch `p1/42-contagens-explicadas`. Também foram lançados 4 revisores independentes (corretude, contratos, contagem de I/O, qualidade dos testes) sobre `cbd/storage/` (AGENTS §8.2).

## Prompt 7 — Prefetch fixado pelo cursor dono, seguindo o SKS §13.5.1.2 (23/11/2026)

- **Ferramenta / Modelo:** Claude Code (Claude Opus 5.5)

> antes de ir para H, quero discutir o prefetch junto as politicas. o professor falou sobre a transação colocar o pin no prefecth e quando acabar, retirar. Nesse sentido, quero saaber quem que chama o pin e o unpin na arquitetura e o que seria possivel
>
> o professor quis dizer que se uma transação fez o prefetch, ela da pin em todo o prefetch e quando acaba, tira o pin. no caso de mais de 1 transação colocar o pin, aí todas precisam remover, ou seja, acabarem
>
> mas soltar o que usar faz algum sentido?
>
> vamos entao seguir o livro, é necessário documentar tudo o que for necessário

- **Resultado:**
  - **Opções:** a IA mapeou quem chama `pin`/`unpin` em cada camada e comparou a segunda chance (S0), o pin até o fim do cursor (S1, impossível num scan completo: 1 962 blocos em 128 frames), o pin do dono solto no uso mais o resto no fim (S2, o livro) e um `end_scan` explícito (S3).
  - **Implementação:** a integrante escolheu S2. Entraram `bp.seq_scan` e `SeqScan`, a pré-leitura fixada, o teto por cursor e o prefetch restrito a DATA e INDEX; as políticas voltaram a LRU/MRU puros. Primeira versão em `ai/P1/raw/buffer_pool_seq_scan_v1.py` e `ai/P1/raw/policies_puras_v1.py`.
  - **Testes e registros:** os testes de prefetch foram reescritos para o cursor e houve testes novos. Registros: D-P1-12 (revisão de 23/11), `review.md` 21 e 22.

## Prompt 8 — Revisores independentes do cursor e decisões S2/T0 (23/11/2026)

- **Ferramenta / Modelo:** Claude Code (Claude Opus 5.5)

> quero focar agora em 1, 2 e 3
>
> s1 é melhor que s2? nao entendi bem a gravidade de T
>
> s2 e t0

- **Resultado:**
  - **Revisores:** 4 revisores independentes (corretude, contratos, contagem de I/O, qualidade dos testes) revisaram o prefetch com cursor. A IA principal reproduziu as 10 sondas que falharam e as mutações M19 e M16.
  - **Decisões:** Vivian escolheu S2 (um salto solta as pré-lidas restantes) e T0 (documentar a condição exata da garantia de memória).
  - **Correções:** cursor fechado, erros de E/S no lote e testes novos (8 unitários e 1 estresse com dois cursores). Registros em `review.md` 23 a 28 e no adendo do cartão D-P1-12.

## Prompt 9 — Fechamento da parte P1 (#45) (23/11/2026)

- **Ferramenta / Modelo:** Claude Code (Claude Opus 5.5)

> vamos para o fechamento da #45

- **Resultado:**
  - **Revisor de confirmação:** rodou sobre o código final do prefetch (cerca de 92 mil falhas injetadas, sem defeito crítico, alto ou médio) e achou dois pontos baixos (achado 29): S2 soltava as pré-lidas num hit, e o limite "1 + k" não vale com aninhamento. Corrigido no PR #97 (`0dac349`) e na arquitetura, PR #96 (`df45ef8`), com o ok da integrante.
  - **Documentos atualizados:** o resumo das decisões e "como se combinam" em `docs/decisoes/P1.md` (com D0 e C0 nas linhas de D-P1-03 e D-P1-05), `ai/P1/ESTADO.md` (com a tabela de requisitos), `ai/P1/stress.md`, `ai/P1/post_mortem.md` (18 defeitos e estimado × medido; as seções de opinião ficaram como rascunho para a integrante) e `docs/estudo/P1.md` §1.3 e §2.

## Prompt 10 — Revisão do PR #97 e achado de P2 no prefetch (26/11/2026)

- **Ferramenta / Modelo:** Claude Code (Claude Opus 5.5)

> revise a pr 97
>
> o bug procede ou nao?
>
> pode corrigir na p1/42

- **Resultado:**
  - **Revisão:** a IA conferiu o head do PR (162 passed, 3 skipped; estresse 3 passed; `grep` limpos) e confirmou o achado de Guilherme (P2): o `except ValueError` de `_prefetch_read` escondia erro de leitura. Sem outro defeito no código.
  - **Correção:** `except ValueError` removido; teste reescrito para esperar o erro sem perder frame nem vazar pin. Registro em `review.md` 30.
