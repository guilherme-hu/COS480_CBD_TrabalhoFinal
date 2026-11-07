# COS480 - Trabalho Final de Construção de Banco de Dados — Grupo 3

Repositório destinado ao desenvolvimento, instrumentação e análise experimental de um Sistema Gerenciador de Banco de Dados (SGBD) modular para a disciplina de **Construção de Banco de Dados** (UFRJ, 2026.2 — Prof. Milton R. Ramirez). O foco central do projeto reside na avaliação comparativa e combinatória de diferentes políticas de gerência de buffer, estruturas de índices e tamanhos de bloco sob um ambiente de armazenamento simulado.

---

## Integrantes

| Nome Completo | DRE | Camada / Responsabilidade |
| :--- | :---: | :--- |
| **Bernardo Brandão Pozzato C. Costa** | `123289593` | *P6* |
| **Enzo de Carvalho Sampaio** | `123386206` | *P5* |
| **Gabriel Schmitz Corrêa Rizawinsk** | `123225573` | *P3* |
| **Guilherme En Shih Hu** | `123224674` | *P2* |
| **Raphael Henrique da Silva Pereira** | `123420783` | *P4* |
| **Vivian Maria da Silva e Souza** | `123205793` | *P1* |

---

## O trabalho

**Avaliação 2 – Indexação e Processamento de Consultas.** Prazo: **22/11/2026**.

Construir, com auxílio de IA, uma bancada de teste que mede o número de blocos transferidos entre memória secundária e buffer pool, para responder: *qual configuração do SBD é mais adequada para processar uma dada carga de trabalho sobre uma determinada base de dados?*

Arquitetura sugerida, em quatro camadas (cada uma usa só a interface da camada de baixo):

1. **Bancada de Teste (orquestrador):** gera a base, instancia as configurações, roda as operações e grava os resultados em CSV.
2. **Mini Monitor de Execução:** primitivas de seleção e junção (nested-loop em blocos, sort-merge, partition-hash).
3. **Gerente de Armazenamento:** organizações primárias (heap, B+ e hash, com campo UNIQUE e NOT UNIQUE), índices e mini catálogo.
4. **Gerente de Arquivos e Gerente de Buffer Pool:** disco simulado em blocos, contadores de leituras e gravações, políticas de substituição.

Enunciado completo: [proposta.md](proposta.md).

## Base e carga de trabalho

Preparação implementada em Python 3.11+, sem dependências externas:

```sh
python3 -m bench.build --scale 0.1
python3 -m pytest -q
```

Para a base final calibrada: `python3 -m bench.build --scale 1 --rows turma=100000`.
Os comandos geram os dados universitários, subconjuntos, consultas com respostas
SQLite e planos de carga heap/B+/hash em `data/prepared/`, além de
`results/base_stats.csv`. Use `--force` para recriar uma preparação existente.

Instruções, decisões sobre volumes e integração com P1–P3:
[Base de dados e carga de trabalho](docs/dados_e_carga.md).

## Estrutura do repositório

| Caminho | Conteúdo |
| :--- | :--- |
| `proposta.md` | Enunciado transcrito em Markdown |
| `proposta.pdf` | Enunciado original em PDF |
| `docs/implementacao/` | Arquitetura geral e contratos (`00_ARQUITETURA_GERAL.md`) + um guia de implementação por integrante (`P1` a `P6`). Cada um passa à IA o geral + o seu |
| `refs/` | Livros-texto (Silberschatz–Korth–Sudarshan, Elmasri–Navathe). **Não versionado** |
| `POST_MORTEM.md` | Análise crítica do uso de IA (a criar, exigido na entrega) |
