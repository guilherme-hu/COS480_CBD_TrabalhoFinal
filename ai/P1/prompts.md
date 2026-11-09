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
