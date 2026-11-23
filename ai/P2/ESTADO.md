# Estado da parte P2 — atualizado em 23/11
## Pronto (com teste que prova)
- **Codec (#39), `cbd/records/codec.py`:** `RecordCodec` (encode/decode, `max_size`), `key_size`, `encode_key`, `decode_key`, com D-P2-01 a D-P2-05. 65 testes em `tests/test_codec.py` (N1 roundtrip, layout, sentinelas, corrompidos, fuzz de 20 mil registros corrompidos, aleatórios; N3 todas as tabelas do gerador; N4 contagens de blocos das decisões; ordem das chaves em 10 mil pares por tipo). Versão bruta em `ai/P2/raw/codec_v1.py`; defeitos achados em `ai/P2/review.md`.
- `cbd/records/hashing.py` existe (escrito pelo P6 para a referência SQLite), sem teste próprio (#41).
## Em andamento
- #39 na branch `p2/39-codec`: código e testes prontos, **não commitados**. Falta: commit (com o ok do Guilherme), trocar o PR #91 para `Closes #39` e conferir os critérios de aceite no PR.
## Falta
- #40, #41, #43, #83, #46, #48, #52, #55, #62, nesta ordem (tabela no `CLAUDE.local.md` §5).
## Requisitos do enunciado cobertos
| Enunciado § | Onde está | Teste que prova |
| :-- | :-- | :-- |
| §3.1.2 campos fixos e variáveis | `codec.py` (parte fixa + prefixo u16, D-P2-01) | `test_layout_fixed_part_first_then_length_prefixed_variables` |
| §3.1.2 comuns, opcionais, null bitmap | `codec.py` (sentinelas, D-P2-02) | `test_null_sentinels_take_the_field_space`, `test_codec_roundtrip` |
| §3.1.2 multivalorado | `codec.py` (LIST, D-P2-03) | `test_codec_roundtrip`, `test_codec_corrupt` |
| §3.1.2 estruturado e derivado | `codec.py` (string única; nada derivado, D-P2-04) | `test_generated_base_roundtrips` (endereço com `\|`) |
| §3.1.3 registros no bloco (unspanned) | `RecordCodec(..., block_size)` → `RecordTooLarge` | `test_record_too_large_for_the_block` |
| §3.4 chaves da B+ (ordem da árvore) | `encode_key`/`key_size` (D-P2-05) | `test_key_order`, `test_composite_key_with_seq_keeps_order` |
| §7.1 acesso fora da faixa | `decode` valida cada tamanho | `test_codec_corrupt` |
## Desvios ou dúvidas sobre o contrato
- **`RecordCodec(schema, block_size=None)`** (os dois revisores independentes apontaram): a arquitetura §5.1 diz que `encode` levanta `RecordTooLarge` "se não couber num bloco", mas a assinatura `RecordCodec(schema)` não recebe o tamanho do bloco. Acrescentei o parâmetro opcional `block_size` (compatível: o P4 chama `RecordCodec(schema)` e continua funcionando, só sem o limite). Provisório: precisa do ok do Guilherme e de um PR separado na arquitetura §5.1, avisando o grupo. Alternativa: o heap (#43) checar `len > max_record(B)` e o codec nunca levantar. De todo jeito, a folha da B+ do P3 acrescenta `ks + 8` bytes de chave composta, então o P3 precisa checar o próprio limite.
- `_PAGE_OVERHEAD = 32` no codec copia `SlottedPage.max_record(B) = B − 32`; trocar pela chamada na #40.
- `aluno.curso` é Zipf (0,7) no gerador: curso 1 tem 6 192 alunos, o menor 416. O guia P2 §6.1 fala em "~1 000 por curso" (é só a média).
- D-P2-01 muda o tamanho do registro: o estimador do P6 (`bench/sqlite_ref.py::record_bytes`, 4 B por variável) precisa passar para 2 B, e o D-P2-02 tira o bitmap (`(nf + 7) // 8`). Pedido ao P6 na issue #90 (22/11); complementos do D-P2-02 e do D-P2-03 (itens de LIST com `1 + len`, sem contagem) em comentários na #90. O codec real confirma: `inscricao` 32 B; heap com 1 830 blocos de `aluno` e 4 425 de `inscricao` em B = 4096.
## Armadilhas conhecidas / bugs abertos
- No Windows, 2 testes do P6 (`test_data_preparation.py`) só passam com `PYTHONUTF8=1` (abrem arquivo sem `encoding`). Avisar o P6.
- Chave de texto não aceita `\x00` (é o preenchimento), nem em VARCHAR que o registro aceitaria. A bancada não tem chave de texto.
## Decisões provisórias a rever
- `block_size` opcional no `RecordCodec` (acima).
