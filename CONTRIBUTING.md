# Como contribuir

Contribuições precisam preservar três propriedades: o vault continua sendo a
fonte primária, operações de escrita ficam explícitas e afirmações de
desempenho permanecem reproduzíveis.

## Antes de começar

1. Procure uma discussão ou issue que descreva o problema.
2. Para falhas de segurança, siga [SECURITY.md](SECURITY.md) e use um canal
   privado.
3. Nunca anexe notas reais, caminhos da máquina, tokens, bancos de índice ou
   logs sem sanitização.
4. Mudanças em nomes, argumentos ou retornos MCP exigem documentação e teste de
   contrato no mesmo pull request.

## Ambiente local

Requisitos: Python 3.14 ou superior e `uv`. Mudanças nos scripts do daemon
também exigem ShellCheck.

```bash
uv sync --locked
cp config.example.yaml config.yaml
```

Use um vault sintético para desenvolvimento. `config.yaml`, `data/` e
`vaults/` ficam fora do controle de versão.

## Fluxo de trabalho

Crie uma branch pequena com um dos prefixos `feat/`, `fix/`, `refactor/`,
`perf/`, `docs/`, `test/` ou `chore/`. Use um slug em minúsculas, por exemplo
`fix/daemon-health-check`.

Commits seguem Conventional Commits:

```text
fix(indexer): preserve the active index during rebuild
docs(config): document environment precedence
```

Configure o Git com um nome público escolhido e um endereço no-reply. O gate de
publicação rejeita e-mails pessoais nos metadados de autor e committer.

Evite misturar refatoração ampla com uma correção funcional. Não inclua
artefatos gerados, conteúdo de vault ou configuração local.

## Gates locais

Execute a menor validação que cobre sua mudança e depois o conjunto padrão:

```bash
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
bash -n scripts/*.sh && shellcheck scripts/*.sh
uv run mypy src/vault_search
uv run pytest -m "not slow" --cov=vault_search --cov-report=term \
  --cov-fail-under=65
uv run python scripts/check_publication.py
uv build
uv run python scripts/check_publication.py --require-dist
```

Testes com a marca `slow` podem baixar e carregar modelos. Declare no pull
request se eles foram executados, em qual hardware e com qual comando.

## Padrões de código

- Funções públicas recebem type hints.
- Efeitos colaterais, formatos de retorno e falhas relevantes entram na
  docstring.
- Logs não carregam conteúdo de notas, consultas completas nem caminhos
  absolutos.
- Escritas de notas devem ser atômicas quando o sistema de arquivos permitir.
- Caminhos fornecidos pelo cliente são resolvidos e verificados dentro do vault.
- Exclusões movem dados para a lixeira. Não use APIs de remoção permanente.
- Dependência nova exige motivação, impacto de distribuição e alternativa
  considerada.

## Documentação e desempenho

Documente o comportamento observado no código. Uma meta deve aparecer como
meta. Um benchmark precisa incluir ambiente, dataset, estado de cache, número
de amostras e percentis, conforme
[docs/performance/benchmarking.md](docs/performance/benchmarking.md).

Links relativos e contagens públicas são verificados pelo script de publicação.
Se uma fixture precisar conter deliberadamente um padrão proibido, acrescente
`publication-check: synthetic-fixture` na mesma linha. A isenção vale somente
para essa linha. Nunca use o marcador para ocultar um valor real.

## Pull request

O pull request deve informar:

- problema e impacto para o usuário;
- contrato alterado;
- riscos e caminho de reversão;
- comandos executados com seus resultados;
- validações relevantes que não foram executadas;
- atualização de docs, ou justificativa concreta para sua ausência.

Mantenedores podem pedir a divisão de mudanças quando a revisão ou reversão
ficar difícil.
