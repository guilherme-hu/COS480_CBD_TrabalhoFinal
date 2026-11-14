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

O volume de `turma` foi aumentado de 10 mil para 100 mil registros na versão
anterior da carga; com o escopo atual, `turma` e `detalhes` ficam só na base
lógica, para a integridade referencial e a referência SQLite, e não entram nos
experimentos.

> **Decisão pendente (#6):** com B = 8192 a tabela `aluno` não atinge o critério
> de tabela grande (ver a tabela de adequação abaixo). A escolha entre aumentar a
> base, manter e justificar ou restringir o experimento de tamanho de bloco entra
> aqui quando for tomada.
