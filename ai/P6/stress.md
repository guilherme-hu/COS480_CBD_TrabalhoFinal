# Verificação da preparação

- Testes automatizados: `python3 -m pytest -q`.
- Escala de integração lógica: 0.002; geração, persistência, consultas por SQL
  comparadas com joins/seleções Python, permutação, nulos, multiplicidade,
  PK composta, restrições, reprodutibilidade entre processos e carga via dublê.
- Geração final: `python3 -m bench.build --scale 1 --rows turma=100000 --force`.
  50 cursos, 1000 disciplinas, 100000 turmas, 50000 alunos,
  500000 inscrições, 50000 detalhes, além dos três subconjuntos.
- `PRAGMA integrity_check` e `PRAGMA foreign_key_check` são exigidos em toda
  preparação. Estatísticas e SHA-256 estão em `results/base_stats.csv`.
- Tabelas grandes são transmitidas por iteradores. A permutação não aloca
  listas de milhões de IDs; utiliza seis chaves Feistel e memória constante.
- Não há medições de I/O, pin/unpin, política de buffer ou desempenho dos
  operadores neste estágio, pois as implementações correspondentes não existem.
