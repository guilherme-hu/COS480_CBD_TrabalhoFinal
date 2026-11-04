# Registro de uso de IA — preparação da base

Data: 04/11/2026. Ferramenta: Codex, nesta sessão do projeto.

Pedido do usuário:

> Gostaria de que voce implementasse apenas a parte do banco de dados e da
> carga de trabalho (carga de dados de aluno e turma por exemplo) usando o que
> tiver sobre essa parte no inicio do pdf da proposta e no md que comeca com
> P6. Faça tudo necessario para deixar esses dados prontos pros testes

Contexto consultado: primeiras três páginas de `proposta.pdf` (extraídas
localmente com PDFKit), transcrição `proposta.md`, guia P6, arquitetura geral
e trechos do P2 sobre codec, hashing, catálogo e layout de páginas.

Escopo aplicado: esquemas, contratos compartilhados copiados literalmente,
geração determinística, integridade relacional, subconjuntos, SQLite de
referência, consultas e assinaturas, planos e adaptador de carga física,
Engine contratual, CLI, documentação e testes. As demais camadas ainda não
existem no repositório; não foram implementados algoritmos ou medições fictícias.
