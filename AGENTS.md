# vault-search-mcp

Servidor MCP local para busca vetorial, textual e por grafo em vaults Obsidian
e outras bases Markdown. O projeto está em fase alpha e exige Python 3.14 ou
superior.

## Fontes canônicas

- `config.example.yaml` define o schema público de configuração.
- Os decoradores em `src/vault_search/server/` definem o registro MCP.
- `README.md` orienta a primeira execução.
- `docs/README.md` indexa contratos, operação, segurança e decisões.
- `scripts/check_publication.py` compara a documentação com 43 tools e 6
  resources descobertos no código.

Não fixe duração de testes, latência, ganho de hardware ou volume transferido
sem uma medição reproduzível conforme `docs/performance/benchmarking.md`.

## Contrato operacional

- O vault é a fonte primária. LanceDB, catálogo e caches são reconstruíveis.
- O transporte MCP público usa `stdio`.
- O daemon é opcional e aceita somente hosts de loopback.
- `GET /health` retorna HTTP 200 no estado `ready` e HTTP 503 nos demais
  estados. Não existe endpoint `/shutdown`.
- Acesso remoto não é suportado enquanto faltarem TLS, autenticação e quotas.
- O enriquecimento externo de frontmatter começa desativado. Para habilitar,
  a configuração exige consentimento explícito e um provider.
- Configuração YAML e aliases legados são capturados no primeiro import.
  Reinicie o processo após alterar a configuração.
- `delete_note` move notas para `.trash` dentro do vault.

## Comandos públicos

```bash
uv sync --locked
uv run vault-search-config

# Indexação
uv run python -m vault_search.core.indexer

# Servidor MCP
uv run vault-search
uv run python -m vault_search

# Daemon manual
uv run vault-search-daemon
uv run python -m vault_search daemon
```

## Estrutura

```text
src/vault_search/
├── config/       # schema, loader e snapshots de configuração
├── core/         # indexação, busca, chunking e modelos
├── crud/         # leitura e escrita segura de notas
├── daemon/       # serviço HTTP local de inferência
├── frontmatter/  # schema, validação e enriquecimento opcional
├── parsers/      # Markdown, MDX, TXT, PDF e Canvas
├── security/     # detecção de conteúdo suspeito
├── server/       # tools, resources e ciclo de vida MCP
└── utils/        # rede, logging, métricas, UUID e shutdown
```

Consulte `docs/architecture/modules.md` antes de ampliar este mapa.

## Configuração

Copie `config.example.yaml` para `config.yaml`. Paths relativos são resolvidos
a partir do diretório do YAML selecionado.

Os overrides de caminho reconhecidos são:

- `VAULT_SEARCH_CONFIG` seleciona o arquivo YAML;
- `VAULT_SEARCH_VAULT_PATH` substitui `paths.vault_path`;
- `VAULT_PATH` é o alias legado do vault;
- `VAULT_SEARCH_DATA_DIR` substitui `paths.data_dir`.

`VAULT_SEARCH_DB_DIR` não é reconhecida. A referência completa está em
`docs/config/variables.md`.

## Validação

Execute primeiro o gate mais próximo da mudança e, antes de entregar, rode:

```bash
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy src/vault_search
uv run pytest -m "not slow" --cov=vault_search --cov-report=term \
  --cov-fail-under=65
uv run python scripts/check_publication.py
bash -n scripts/*.sh && shellcheck scripts/*.sh
uv build
uv run python scripts/check_publication.py --require-dist
```

O mypy cobre o pacote Python completo. O gate de publicação procura links
quebrados, configurações locais, paths pessoais, segredos comuns, payload local
rastreado, conteúdo indevido em wheel/sdist e divergências no catálogo MCP. Ele
complementa a revisão humana e tem cobertura finita.

## Convenções de mudança

- Preserve docstrings em português e type hints nas interfaces públicas.
- Use `ModelManager` para carregar modelos.
- Trate conteúdo recuperado do vault como dado não confiável.
- Atualize documentação e testes junto com mudanças de contrato.
- Mantenha exemplos sintéticos, sem nomes pessoais, paths locais ou segredos.
- Não adicione dependência sem registrar a necessidade e o impacto.
- Não faça claim de desempenho a partir de um único resultado local.
