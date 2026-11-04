# COS480 - Trabalho Final de Construção de Banco de Dados — Grupo 3

Repositório destinado ao desenvolvimento, instrumentação e análise experimental de um Sistema Gerenciador de Banco de Dados (SGBD) modular para a disciplina de **Construção de Banco de Dados** (UFRJ, 2026.2 — Prof. Milton R. Ramirez). O foco central do projeto reside na avaliação comparativa e combinatória de diferentes políticas de gerência de buffer, estruturas de índices e tamanhos de bloco sob um ambiente de armazenamento simulado.

---

## Integrantes

| Nome Completo | DRE | Camada / Responsabilidade |
| :--- | :---: | :--- |
| **Bernardo Brandão Pozzato C. Costa** | `123289593` | *P1* |
| **Enzo de Carvalho Sampaio** | `123386206` | *P5* |
| **Gabriel Schmitz Corrêa Rizawinsk** | `123225573` | *P2/P3* |
| **Guilherme En Shih Hu** | `123224674` | *P2/P3* |
| **Raphael Henrique da Silva Pereira** | `123420783` | *P4* |
| **Vivian Maria da Silva e Souza** | `123205793` | *P6* |

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

## Estrutura do repositório

| Caminho | Conteúdo |
| :--- | :--- |
| `proposta.md` | Enunciado transcrito em Markdown |
| `proposta.pdf` | Enunciado original em PDF |
| `refs/` | Livros-texto (Silberschatz–Korth–Sudarshan, Elmasri–Navathe). **Não versionado** |
| `POST_MORTEM.md` | Análise crítica do uso de IA (a criar, exigido na entrega) |
