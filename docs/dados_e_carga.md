# Base de dados e carga de trabalho

Esta entrega implementa a preparação dos dados da universidade do enunciado
(`proposta.pdf`, início e §6.4) e do P6 (§2, §3 e §5), seguindo os tipos e
interfaces de `00_ARQUITETURA_GERAL.md`. Não executa a matriz de desempenho,
algoritmos de seleção/junção, gráficos ou relatório experimental.

Requer Python 3.11+, sem dependências externas para preparar os dados.

## Preparar e reproduzir

Na raiz do repositório, para desenvolvimento:

```sh
python3 -m bench.build --scale 0.1
```

Base final calibrada para a configuração-base (B=4096, WORK=64, DATA=128):

```sh
python3 -m bench.build --scale 1 --rows turma=100000 --output data/prepared
```

Se a base já existe, o comando recusa sobrescrevê-la. Acrescente `--force`
para regenerar explicitamente. Os mesmos argumentos produzem os mesmos
registros, consultas, hashes e planos. A carga é feita em uma base temporária
e só é publicada depois da validação de integridade. Mantenha a pasta
sem outros leitores durante a substituição dos arquivos de preparação.

Para uma base minúscula independente:

```sh
python3 -m bench.build --scale 0.002 --output data/tiny --results results/tiny
python3 -m pytest -q
```

É possível ajustar volumes com `--rows tabela=N` (repetível). `detalhes`
sempre acompanha `aluno`, preservando a correspondência por CPF. Para escalas
minúsculas, `inscricao` é limitada ao número de pares distintos aluno/turma.
`--work-pages` controla o orçamento dos subconjuntos; mínimo 3.

## Arquivos produzidos

| Arquivo em `data/prepared/` | Conteúdo |
| --- | --- |
| `ref.sqlite` | Seis tabelas completas e três subconjuntos, PKs, UNIQUEs, FKs e índices auxiliares da referência |
| `manifest.json` | Versão, escala, volumes, seeds, esquemas, cursos dos subconjuntos e adequação estimada dos tamanhos |
| `workload.json` | Nove joins, até vinte igualdades UNIQUE, seis faixas e respectivos `(count, checksum)` esperados |
| `physical_plan_2048.json` / `4096` / `8192` | Variantes heap/B+/hash, índices secundários e parâmetros de carga |
| `base_stats.csv` | Volumes, tamanho médio previsto do registro, cardinalidades, SHA-256 e blocagem estimada por tamanho de bloco |

A CLI copia `base_stats.csv` para `results/base_stats.csv` (ou `--results`).
Os bancos gerados estão ignorados pelo Git; os scripts e as estatísticas
permitem reproduzi-los. Não é necessário gerar de novo ao trocar a política
de buffer. Os dados lógicos são os mesmos nos três tamanhos de bloco.

`b` e `bfr` ficam vazios enquanto não há medição física. `estimated_blocks` e
`estimated_bfr` simulam empacotamento heap com o layout P2 (cabeçalho de
28 bytes e slot de 4 bytes); **não são contadores de I/O ou medições do SGBD**.
`V(A)` exclui nulos, como `COUNT(DISTINCT ...)` no SQL. O SHA-256 considera
todos os campos e sua ordem de emissão; os checksums das consultas consideram
as chaves lógicas e independem da ordem do resultado.

## Dados e integridade

Os esquemas em `bench/schema.py` são os seis esquemas exatos do contrato §8.3.
`PK` inclui `(dre, turma)` para inscrição. As chaves estrangeiras são
verificadas pelo SQLite durante a carga e por `foreign_key_check` ao final.
Professor/orientador são identificadores em 1..2000; não há tabela de professor
no contrato. Pré-requisitos apontam para disciplinas anteriores, evitando ciclos.
Os CPFs são identificadores sintéticos únicos de onze dígitos, sem pretensão
de validar os dígitos verificadores de documentos reais.

O gerador usa `random.Random(480+i)` por tabela e permutação Feistel com
cycle walking, sem guardar uma lista de IDs ou registros em memória.
Inscrições vêm de uma permutação do produto aluno × turma, sem repetir a PK
composta. O SQLite usa `INT` nas PKs numéricas para manter um `rowid`
independente: a exportação preserva a ordem embaralhada da geração.
LIST é `tuple` em Python e JSON no SQLite; DATE é `datetime.date` e texto ISO.

Cursos de alunos seguem Zipf leve; CR segue normal truncada em [0,10], com
duas casas; cerca de 30% dos orientadores são nulos. Detalhes têm 0–8 itens.
Endereços usam `rua|numero|bairro|cidade|uf`. Campos e listas respeitam os
limites em bytes UTF-8 e os tipos do codec planejado.

`aluno_p` seleciona os dois últimos cursos, menos frequentes na distribuição
Zipf, limitado por um orçamento de bytes. `detalhes_p` contém exatamente os
CPFs desse subconjunto. Ambos cabem no mesmo orçamento estimado.
`turma_p` contém somente professores presentes em `aluno_p`, com limite de
bytes. Usa-se o bloco mínimo de 2048 e margem de 20%, para conservar os
mesmos subconjuntos entre 2/4/8 KiB. A confirmação definitiva usa `stats()`.

Os 10 mil registros de turma originalmente sugeridos não atingem 1280 blocos
na configuração-base. Por isso, a base final usa 100 mil turmas, documentadas
no manifesto. Escalas menores servem para desenvolvimento e podem não ter
tabelas grandes. O manifesto informa explicitamente toda combinação que
não atende pequena ≤ WORK−2 ou grande ≥ 10×DATA; a CLI destaca a situação
para B=4096. O critério mais forte de 10×DATA é calibrado para B=4096.

## Consultas de referência

`bench/workload.py` cobre os três tipos de join e as três combinações de
tamanho do P6. O join usa igualdade explícita e saída concatenada
`r_rec + s_rec`, conforme o contrato, inclusive para mestre/detalhe.
Nulos não se juntam. Repetições em colunas NOT UNIQUE preservam a multiplicidade.

As seleções incluem DREs sorteados com seed fixa, faixas de DRE e CPF e
faixas de CR com metas de 0,1% e 50%. Os limites são quantis dos dados
efetivamente carregados, e a seletividade real é registrada: empates de CR
podem impedir exatamente 0,1%. As igualdades UNIQUE retornam uma linha,
portanto sua seletividade é 1/n.

Exemplo de consumo por um futuro teste de operador:

```python
import json
from bench.sqlite_ref import SQLiteReference, output_signature

workload = json.load(open('data/prepared/workload.json'))
query = workload[0]
with SQLiteReference('data/prepared/ref.sqlite') as ref:
    expected = ref.result(query)
# actual = output_signature(query, operador(...))
# assert actual == expected
```

`result` usa SQL no SQLite e calcula em streaming o checksum da chave lógica
da seleção, ou `(PK_r, PK_s)` no join. `output_signature` extrai essas mesmas
chaves da saída do operador. A soma módulo 2^64 usa o `hash_value` compartilhado
de P2, nunca `hash()` do Python. Os hashes não substituem a contagem de linhas.

## Integração com o armazenamento do grupo

`bench.build.load_physical(eng, ref, plan)` usa exclusivamente `Catalog` para
criar tabelas, carregar iteradores e construir os índices após a carga.
Cada variante recebe `Schema` com seu próprio nome, mantendo campos idênticos.
Há heap com índices UNIQUE→HASH e NOT UNIQUE→B+, B+/hash por cada coluna
relevante e índices B+ NOT UNIQUE nas outras colunas permitidas.
Há também `aluno__bt_dre_inc` com carga incremental e um par hash com
`buckets` e `seed` iguais para cada consulta de join (`_eq_<tipo>_<tamanho>`).
As variantes hash comuns têm seeds distintas entre tabelas, permitindo
comparar também o caso sem particionamento compatível.

Quando P1/P2/P3 estiverem implementados, execute:

```sh
python3 -m bench.build --scale 1 --rows turma=100000 --force --physical
```

`cbd/engine.py` já liga as interfaces acordadas e oferece `cold`, `snapshot`,
`M` e fechamento. O comando acima gera `db_2k`, `db_4k`, `db_8k` e estatísticas
físicas JSON por variante, incluindo dados e índices, além de uma conferência
de tamanhos pelos heaps. O carregador recusa variantes já existentes; use
um diretório novo para reconstruir bases físicas.

**Estado atual:** o repositório ainda não implementa `cbd/storage/` ou
`cbd/files/`. A preparação SQLite e os testes de dados funcionam integralmente;
a carga física depende dessas entregas. Nenhuma contagem experimental foi
simulada. O adaptador foi verificado com um dublê do contrato do catálogo;
medidas e reabertura com outras políticas precisam da pilha real.

Para os joins tipos 2 e 3, os planos seguem a interpretação já registrada
no P6: a coluna pode determinar a organização física mesmo não sendo a PK
lógica. A eventual confirmação dessa interpretação com o professor fica
para a bancada/relatório do grupo.
