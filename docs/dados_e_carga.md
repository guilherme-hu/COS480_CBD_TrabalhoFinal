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

Base final (B=4096 com WORK=64 e DATA=128, e as mesmas memórias em bytes nos outros blocos):

```sh
python3 -m bench.build --scale 1 --output data/prepared
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
`--work-pages` (M em B = 4096, mínimo 3) define o limite usado para conferir se a
tabela menor cabe em M − 2 páginas; nos outros blocos ele escala (D-P6-02).

## Arquivos produzidos

| Arquivo em `data/prepared/` | Conteúdo |
| --- | --- |
| `ref.sqlite` | Seis tabelas completas, PKs, UNIQUEs, FKs e índices auxiliares da referência |
| `manifest.json` | Versão, escala, volumes, seeds, esquemas e adequação estimada dos tamanhos |
| `workload.json` | Três joins, até vinte igualdades UNIQUE, duas faixas em CR e respectivos `(count, checksum)` esperados |
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

Não há subconjuntos (`aluno_p`, `detalhes_p`, `turma_p` existiam na versão
anterior): as tabelas reais já cobrem as três combinações de tamanho
(arquitetura §0). `turma` e `detalhes` ficam só na base lógica, para integridade
referencial e para a referência SQLite; não ganham variantes físicas.

A base final usa os volumes padrão (`--scale 1`, 10 mil turmas). O override
`--rows turma=100000` da versão anterior existia para um join com `turma` que
saiu do escopo. O manifesto confere, para cada tamanho de bloco, pequena ≤ M − 2
e grande ≥ 10 × DATA com as áreas daquele bloco: a memória do buffer é a mesma
em bytes nos três tamanhos (D-P6-02), então M e DATA em páginas escalam com B
(WORK = 128/64/32 e DATA = 256/128/64 páginas em 2/4/8 KiB). Com isso a escala 1
é adequada nos três tamanhos. Escalas menores servem para desenvolvimento e podem
não ter tabelas grandes; a CLI avisa cada combinação inadequada.

## Consultas de referência

`bench/workload.py` cobre só o escopo obrigatório (arquitetura §0): os três
NATURAL JOINs mestre-detalhe (curso ⋈ disciplina, curso ⋈ aluno,
aluno ⋈ inscricao), com o mestre sempre como `r`. O join usa igualdade explícita
e saída concatenada `r_rec + s_rec`, conforme o contrato. Nulos não se juntam.
Repetições em colunas NOT UNIQUE preservam a multiplicidade.

As seleções são 20 igualdades em `aluno.dre` (sorteadas com seed 490) e duas
faixas em `aluno.cr`: `cr >= q` com meta de 1% (CR alto) e `cr <= q` com meta
de 50%. `q` é o quantil dos dados efetivamente carregados e a seletividade
real fica registrada em `selectivity`: empates de CR podem fazer a resposta
passar um pouco da meta (na escala 0,1: 1,02% e 50,12%). As igualdades UNIQUE
retornam uma linha, portanto sua seletividade é 1/n.

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
As variantes cobrem só `curso` (`codigo`), `disciplina` (`curso`), `aluno`
(`dre`, `curso`, `cr`) e `inscricao` (`dre`). Para cada tabela há heap com
índices UNIQUE→HASH e NOT UNIQUE→B+, B+ e hash por cada uma dessas colunas e
índices B+ NOT UNIQUE nas outras colunas permitidas (23 variantes no total).
Há também `aluno__bt_dre_inc` com carga incremental e um par hash com
`buckets` e `seed` iguais para cada join (`_eq_<tamanho>`). O N comum do par é
o menor N padrão das duas tabelas, limitado pelo V(A) do lado NOT UNIQUE
(D-P6-01, provisória). Toda variante hash NOT UNIQUE recebe `distinct = V(A)`.
As variantes hash comuns têm seeds distintas entre tabelas, permitindo
comparar também o caso sem particionamento compatível.

Quando P1/P2/P3 estiverem implementados, execute:

```sh
python3 -m bench.build --scale 1 --force --physical
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
