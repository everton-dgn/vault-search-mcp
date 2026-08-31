# Estratégia de testes e qualidade

## Pirâmide usada pelo projeto

| Camada | Objetivo | Comando |
|---|---|---|
| Lint | Fonte, testes e scripts | `uv run ruff check src tests scripts` |
| Formatação | Fonte, testes e scripts | `uv run ruff format --check src tests scripts` |
| Shell | Instaladores e desinstaladores | `bash -n scripts/*.sh && shellcheck scripts/*.sh` |
| Tipos | Pacote Python completo | `uv run mypy src/vault_search` |
| Unitária | Regras sem carregar modelos | `uv run pytest -m "not slow" --cov=vault_search --cov-fail-under=65` |
| Integração ML | Modelos, índice e ambiente real | `uv run pytest -m slow` |
| Publicação | Docs, privacidade, árvore Git e pacotes | `uv run python scripts/check_publication.py && uv build && uv run python scripts/check_publication.py --require-dist` |

ShellCheck é uma dependência de desenvolvimento para mudanças em `scripts/*.sh`;
ele não entra nas dependências de runtime do pacote Python.

## Typecheck do pacote

O mypy verifica todos os arquivos-fonte. Tipos próprios modelam os payloads
heterogêneos, e exceções para bibliotecas sem stubs ficam limitadas aos módulos
externos declarados no `pyproject.toml`. O projeto não aceita `ignore` genérico
para esconder erro do pacote.

## Testes sem modelos

O conjunto padrão deve usar fixtures sintéticas e mocks nas bordas de ML. Ele
não deve baixar modelos, ler um vault pessoal, chamar serviços externos ou
depender de daemon previamente instalado.

O gate de publicação aceita `publication-check: synthetic-fixture` somente na
linha que contém um padrão sintético deliberado. O marcador nunca deve acompanhar
um token, caminho ou endereço real.

Quando existe um repositório Git, o gate também inspeciona a árvore rastreada e
o histórico alcançável por `HEAD`. Ele rejeita config local, dados de vault,
artefatos gerados e e-mails pessoais nos metadados dos commits. Identidades
genéricas do projeto, bots e endereços no-reply do GitHub são aceitos. Com
`--require-dist`, o gate exige wheel e sdist e valida seus membros sem extrair
os arquivos.

```bash
uv run pytest -m "not slow" --cov=vault_search --cov-report=term --cov-fail-under=65
```

Para uma mudança localizada, execute primeiro o arquivo ou teste focal e depois
o conjunto padrão.

## Testes lentos

Use `slow` quando o teste carregar modelos, depender de hardware específico ou
executar um volume incompatível com feedback curto. Registre:

- modelo e versão resolvida;
- device e precisão;
- hardware e sistema operacional;
- cache frio ou aquecido;
- tempo total e resultado.

## Cobertura

Cobertura ajuda a localizar caminhos sem execução. Ela não prova qualidade de
assertions nem cobre contratos externos por si só.

```bash
uv run pytest --cov=vault_search --cov-report=term-missing -m "not slow"
```

A CI exige pelo menos 65% de cobertura combinada de statements e branches. A
linha de base foi definida abaixo da medição local registrada em 2026-08-30 com
Python 3.14.7 no macOS: 1.161 testes passaram, 21 foram excluídos pela marca
`slow` e a cobertura foi 66,48%. Os 7 avisos observados vieram de tipos SWIG da
integração de PDF. Esse snapshot não substitui a revisão dos caminhos críticos
nem promete o mesmo resultado em outro ambiente.

## Casos mínimos por superfície

### Caminhos e CRUD

- `..`, caminho absoluto e byte nulo;
- symlink interno que aponta para fora do vault;
- corrida entre validação e escrita;
- falha antes e depois da troca atômica;
- preservação do original quando a escrita falha.

### Daemon

- porta fechada, listener estranho e resposta inválida;
- morte depois de um health check positivo;
- limite de corpo e lote;
- timeout de conexão e de leitura;
- rejeição de bind fora de loopback.

### Índice

- rebuild vazio e rebuild que falha no meio;
- geração anterior disponível até o commit da nova;
- criação de ANN em tamanho mínimo;
- configuração inválida sem fallback silencioso.

### MCP

- todos os decoradores registrados;
- tipos, defaults e limites de argumentos;
- erros sanitizados;
- cancelamento e shutdown.

## Atualização de snapshots e baselines

Nunca atualize baseline apenas para deixar a CI verde. Primeiro explique a
mudança de contrato e revise o efeito para quem consome a saída.
