# Estado da parte P2 — atualizado em 24/11
## Pronto (com teste que prova)
- **Codec (#39), `cbd/records/codec.py`:** `RecordCodec` (encode/decode, `max_size`), `key_size`, `encode_key`, `decode_key`, com D-P2-01 a D-P2-06; INT de 4 B no registro e na chave desde a revisão do D-P2-05 (23/11). 83 testes em `tests/test_codec.py` (N1 roundtrip, layout, sentinelas, corrompidos, fuzz de 20 mil registros corrompidos, aleatórios; N3 todas as tabelas do gerador; N4 contagens de blocos das decisões; ordem das chaves em 10 mil pares por tipo). Versão bruta em `ai/P2/raw/codec_v1.py`.
- **Slotted page (#40), `cbd/records/slotted_page.py`:** `SlottedPage` com a assinatura da arquitetura §5.1, D-P2-07 (formato; revista em 23/11: sem `flags` e sem `USER`, `HEADER` = 10 B, folha da B+ encadeia pelo `next`) e D-P2-08 (compactação preguiçosa). 86 testes em `tests/test_slotted_page.py` (N1 enchimento exato em 2/4/8 KB, `free_space` exato com `insert` e `insert_at`, `insert_at` recusando página com slot apagado, reuso de slot, compactação, ordem lógica, padrão do split da B+, cabeçalho corrompido, oráculos `dict` e lista; N2 página sobrevivendo ao disco com 1 leitura). Versão bruta em `ai/P2/raw/slotted_page_v1.py`; 4 revisores independentes e uma 2ª revisão (`/code-review` + `/ponytail-review`), achados em `review.md`.
- `cbd/records/hashing.py` existe (escrito pelo P6 para a referência SQLite), sem teste próprio (#41).
## Em andamento
- #39: PR #91 (`p2/39-codec`) aberto, esperando revisão de P3/P4 e merge. O INT de 4 B (commit `e806c37`, com o comentário do contrato em `cbd/contracts.py` e na arquitetura §4) ainda não foi enviado; o texto do PR fala em INT de 8 B.
- #40: PR #93 (`p2/40-slotted-page`, base `p2/39-codec`), com o merge do INT de 4 B (`c08c68a`) ainda não enviado. Falta: push, atualizar os textos do #91 e do #93, revisão do P3, merge do #91 e troca da base do #93 para `main`.
- #43: branch `p2/43-heap` (igual à `p2/40-slotted-page` por enquanto). D-P2-09 (achar bloco com espaço) em debate; página no site.
- Avisos pendentes (precisam do ok do Guilherme): P6 na #94 (`record_bytes` soma 4 B por INT e cabeçalho de 10 B); P3 (fan-out 256 e chave composta de 12 B com INT de 4 B).
## Falta
- #41, #43, #83, #46, #48, #52, #55, #62, nesta ordem (tabela no `CLAUDE.local.md` §5).
## Requisitos do enunciado cobertos
| Enunciado § | Onde está | Teste que prova |
| :-- | :-- | :-- |
| §3.1.2 campos fixos e variáveis | `codec.py` (parte fixa + prefixo u16, D-P2-01) | `test_layout_fixed_part_first_then_length_prefixed_variables` |
| §3.1.2 comuns, opcionais, null bitmap | `codec.py` (sentinelas, D-P2-02) | `test_null_sentinels_take_the_field_space`, `test_codec_roundtrip` |
| §3.1.2 multivalorado | `codec.py` (LIST, D-P2-03) | `test_codec_roundtrip`, `test_codec_corrupt` |
| §3.1.2 estruturado e derivado | `codec.py` (string única; nada derivado, D-P2-04) | `test_generated_base_roundtrips` (endereço com `\|`) |
| §3.1.3 registros no bloco | `slotted_page.py` (diretório de slots, D-P2-07); unspanned: o heap/hash levanta `RecordTooLarge` (D-P2-06, #43) | `test_fill_until_full`, `test_layout_bytes`, `test_every_schema_fits_the_smallest_block`; `test_heap_record_too_large` na #43 |
| §3.1.4 registros deletados e slotted page | `slotted_page.py` (`delete` marca, `insert` reaproveita, compactação preguiçosa, D-P2-08) | `test_delete_keeps_slot_and_rid_of_others`, `test_insert_reuses_smallest_deleted_slot`, `test_insert_compacts_when_only_holes_fit`, `test_random_ops_match_dict_oracle` |
| §3.2.2 critério de compactação (no bloco) | `slotted_page.py` (`_make_room`: só quando o insert precisa) | `test_insert_that_fits_contiguously_does_not_compact`, `test_insert_compacts_when_only_holes_fit` |
| §3.4 chaves da B+ (ordem da árvore) | `encode_key`/`key_size` (D-P2-05) | `test_key_order`, `test_composite_key_with_seq_keeps_order` |
| §3.4 folhas da B+ em ordem | `insert_at`/`remove_at`/`next` | `test_insert_at_keeps_logical_order`, `test_split_pattern_of_the_btree`, `test_random_insert_at_remove_at_match_list_oracle` |
| §7.1 acesso fora da faixa | `decode` valida cada tamanho; `get` valida offset e slot | `test_codec_corrupt`, `test_corrupt_directory_is_detected`, `test_offset_inside_header_is_detected` |
## Desvios ou dúvidas sobre o contrato
- **D-P2-06:** o codec tem a assinatura `RecordCodec(schema)` do §5.1 e não levanta `RecordTooLarge`; quem insere (heap, hash) compara com `SlottedPage.max_record(B)`. O P3 precisa checar o limite da folha (`ks + 8` B a mais).
- **#40, critério 3 ("`free_space` exato, descontando os 4 B do slot novo"):** por D-P2-08, quando há slot apagado o `insert` o reaproveita e o `free_space()` não desconta os 4 B. Na folha da B+ (sem slots apagados) o desconto é sempre de 4 B. Dizer isso no PR.
- **Registro de 0 B recusado** pela slotted page (não está no §5.1; nenhum produtor gera 0 B).
- `aluno.curso` é Zipf (0,7) no gerador: curso 1 tem 6 192 alunos, o menor 416. O guia P2 §6.1 fala em "~1 000 por curso" (é só a média).
- D-P2-01 muda o tamanho do registro: o estimador do P6 (`bench/sqlite_ref.py::record_bytes`) precisa acompanhar. Pedido ao P6 na issue #90.
## Armadilhas conhecidas / bugs abertos
- No Windows, 2 testes do P6 (`test_data_preparation.py`) só passam com `PYTHONUTF8=1` (abrem arquivo sem `encoding`). Avisar o P6.
- Chave de texto não aceita `\x00` (é o preenchimento). A bancada não tem chave de texto.
- Views de `get` valem só com a página fixada; `insert`/`insert_at`/`compact` podem mover registros. Avisar o P3 no PR.
- LIMITAÇÃO: slots apagados no fim do diretório guardam seus 4 B até um `insert` reaproveitá-los.
## Decisões provisórias a rever
- Nenhuma. Escolhas herdadas do guia ainda sem justificativa, a debater nas issues: fator de carga 0,8 e buckets no bloco 2 + i (#48), `compact_at` 0,2 (#43), `hash_value` (#41), catálogo em JSON reescrito e forçado a cada mudança (#46), gravação dos temporários (#43). Resolvidas em 24/11: mapa de espaço livre (D-P2-09) e RID de 6 B (D-P2-10).
