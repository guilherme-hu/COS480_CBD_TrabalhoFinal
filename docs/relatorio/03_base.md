# Base de dados e carga de trabalho

## Esquema

A base é a da universidade do enunciado, com as seis tabelas e os tipos do
contrato do grupo (arquitetura §8.3): `curso`, `disciplina`, `turma`, `aluno`,
`inscricao` e `detalhes`. As chaves primárias e estrangeiras são verificadas pelo
SQLite na carga da referência. Professores e orientadores são identificadores
sem tabela própria, porque o contrato não tem tabela de professor.

## Geração

Os registros são gerados de forma determinística: cada tabela usa um
`random.Random` com seed explícita, e as chaves são emitidas em ordem embaralhada
por uma permutação (Feistel com *cycle walking*), para que um heap não fique
ordenado por acaso e favoreça os scans. Os mesmos argumentos produzem os mesmos
registros, e o SHA-256 de cada tabela fica registrado (`results/base_stats.csv`).

Distribuições relevantes para os experimentos:

- `aluno.curso` segue uma Zipf leve sobre os cursos, de modo que alguns cursos
  concentram mais alunos (skew moderado nas junções por curso);
- `aluno.cr` segue uma normal de média 6,5 e desvio 1,8, truncada em [0, 10] e
  arredondada a duas casas: é o campo NOT UNIQUE das seleções por faixa;
- cerca de 30% dos orientadores são nulos (nulos não se juntam);
- cada aluno tem em média 10 inscrições.

`turma` e `detalhes` ficam só na base lógica, para a integridade referencial e
a referência SQLite, e não entram nos experimentos.

## Volumes e tamanhos de bloco (D-P6-02)

Os volumes são os mesmos nos três tamanhos de bloco. O que muda com o bloco é o
número de páginas de cada área do buffer: a memória em **bytes** é a mesma da
configuração-base (DATA = 512 KiB, por exemplo, são 256, 128 ou 64 páginas em
2, 4 ou 8 KiB). Assim, o experimento de tamanho de bloco compara blocos com a
mesma memória, sem misturar "bloco maior" com "buffer maior". Com essa regra, a
tabela menor de cada junção cabe em M − 2 páginas e a maior passa de 10 × DATA
em todos os tamanhos (tabela abaixo). A alternativa preterida de aumentar a base
para ~67 mil alunos alongaria a rodada em ~34% e manteria a mistura entre bloco e
memória.
