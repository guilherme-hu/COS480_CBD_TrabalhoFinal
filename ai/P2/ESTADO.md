# Estado da parte P2 — atualizado em 23/11
## Pronto (com teste que prova)
- **Codec (#39), `cbd/records/codec.py`:** `RecordCodec` (encode/decode, `max_size`), `key_size`, `encode_key`, `decode_key`, com D-P2-01 a D-P2-06. 71 testes em `tests/test_codec.py` (N1 roundtrip, layout, sentinelas, corrompidos, fuzz de 20 mil registros corrompidos, aleatórios; N3 todas as tabelas do gerador; N4 contagens de blocos das decisões; ordem das chaves em 10 mil pares por tipo). Versão bruta em `ai/P2/raw/codec_v1.py`; defeitos achados em `ai/P2/review.md`.
- `cbd/records/hashing.py` existe (escrito pelo P6 para a referência SQLite), sem teste próprio (#41).
## Em andamento
- #39 na branch `p2/39-codec`, PR #91 (`Closes #39`). Codec commitado em `bb1416c`; a troca do D-P2-06 (tirar `block_size`) ainda não commitada. Falta: commit, PR separado no comentário da arquitetura §5.1, revisão de P3/P4 e merge.
## Falta
- #40, #41, #43, #83, #46, #48, #52, #55, #62, nesta ordem (tabela no `CLAUDE.local.md` §5).
## Requisitos do enunciado cobertos
| Enunciado § | Onde está | Teste que prova |
| :-- | :-- | :-- |
| §3.1.2 campos fixos e variáveis | `codec.py` (parte fixa + prefixo u16, D-P2-01) | `test_layout_fixed_part_first_then_length_prefixed_variables` |
| §3.1.2 comuns, opcionais, null bitmap | `codec.py` (sentinelas, D-P2-02) | `test_null_sentinels_take_the_field_space`, `test_codec_roundtrip` |
| §3.1.2 multivalorado | `codec.py` (LIST, D-P2-03) | `test_codec_roundtrip`, `test_codec_corrupt` |
| §3.1.2 estruturado e derivado | `codec.py` (string única; nada derivado, D-P2-04) | `test_generated_base_roundtrips` (endereço com `\|`) |
| §3.1.3 registros no bloco (unspanned) | D-P2-06: o heap/hash levanta `RecordTooLarge` (#43); o codec não conhece o bloco | `test_every_schema_fits_the_smallest_block` (todo `max_size` ≤ 2 016 B); `test_heap_record_too_large` na #43 |
| §3.4 chaves da B+ (ordem da árvore) | `encode_key`/`key_size` (D-P2-05) | `test_key_order`, `test_composite_key_with_seq_keeps_order` |
| §7.1 acesso fora da faixa | `decode` valida cada tamanho | `test_codec_corrupt` |
## Desvios ou dúvidas sobre o contrato
- **D-P2-06 (23/11):** o codec ficou com a assinatura `RecordCodec(schema)` do §5.1 e não levanta `RecordTooLarge`; quem insere (heap, hash) compara com `SlottedPage.max_record(B)`. Falta ajustar o comentário do `encode` na arquitetura §5.1 (PR separado, avisar o grupo). O P3 precisa checar o limite da folha (`ks + 8` B a mais).
- `aluno.curso` é Zipf (0,7) no gerador: curso 1 tem 6 192 alunos, o menor 416. O guia P2 §6.1 fala em "~1 000 por curso" (é só a média).
- D-P2-01 muda o tamanho do registro: o estimador do P6 (`bench/sqlite_ref.py::record_bytes`, 4 B por variável) precisa passar para 2 B, e o D-P2-02 tira o bitmap (`(nf + 7) // 8`). Pedido ao P6 na issue #90 (22/11); complementos do D-P2-02 e do D-P2-03 (itens de LIST com `1 + len`, sem contagem) em comentários na #90. O codec real confirma: `inscricao` 32 B; heap com 1 830 blocos de `aluno` e 4 425 de `inscricao` em B = 4096.
## Armadilhas conhecidas / bugs abertos
- No Windows, 2 testes do P6 (`test_data_preparation.py`) só passam com `PYTHONUTF8=1` (abrem arquivo sem `encoding`). Avisar o P6.
- Chave de texto não aceita `\x00` (é o preenchimento), nem em VARCHAR que o registro aceitaria. A bancada não tem chave de texto.
## Decisões provisórias a rever
- Nenhuma.
