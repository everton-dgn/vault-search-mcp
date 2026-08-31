# Instalação

## Suporte atual

| Item | Suporte |
|---|---|
| Python | 3.14 ou superior |
| Gerenciador | uv com `uv.lock` |
| Servidor MCP | Qualquer cliente com transporte `stdio` |
| Daemon | macOS launchd e Linux systemd de usuário |
| Windows | Core sem validação em CI; daemon sem instalador |

## 1. Instale as dependências

Clone o código e instale o ambiente bloqueado pelo lockfile:

```bash
git clone https://github.com/everton-dgn/vault-search-mcp.git
cd vault-search-mcp
uv sync --locked
```

`--locked` impede alteração silenciosa do lockfile. O comando instala o pacote
editável e registra os entry points `vault-search` e `vault-search-daemon`.

## 2. Configure um vault

```bash
cp config.example.yaml config.yaml
```

Edite `paths.vault_path`. Um caminho relativo usa o diretório de `config.yaml`
como base. Neste fluxo, o arquivo está na raiz da cópia local.

```yaml
paths:
  vault_path: "vaults/obsidian_vault"
  data_dir: "data"
```

Alternativamente, use um override operacional:

```bash
export VAULT_SEARCH_VAULT_PATH="$PWD/vaults/obsidian_vault"
```

`config.yaml`, `data/` e o conteúdo de `vaults/` são ignorados pelo Git. Não use
um vault real como fixture de teste.

## 3. Valide a configuração

```bash
uv run vault-search-config
```

O comando termina com código 0 e imprime somente `vault-search configuration:
ok`. Uma falha recebe tipo e referência, sem traceback, valor ou path resolvido.
Use `uv run python -m vault_search config` como fronteira equivalente.

## 4. Construa o índice

```bash
uv run python -m vault_search.core.indexer
```

O índice fica em `paths.data_dir`. A primeira execução pode baixar modelos. O
tempo e o espaço variam por plataforma, cache e versões resolvidas.

Modos opcionais:

```bash
# Falha se o daemon não estiver saudável
uv run python -m vault_search.core.indexer --require-daemon

# Aguarda por até 60 segundos
uv run python -m vault_search.core.indexer --wait-daemon 60
```

## 5. Inicie o MCP

```bash
uv run vault-search
# Fronteira equivalente:
uv run python -m vault_search
```

O servidor usa `stdio`. Logs e banners não devem escrever no canal de protocolo.
O cliente MCP precisa executar o comando com a raiz do repositório como diretório
de trabalho.

Configuração mínima em clientes que aceitam JSON:

```json
{
  "mcpServers": {
    "vault-search": {
      "command": "uv",
      "args": ["run", "vault-search"]
    }
  }
}
```

Se o cliente inicia em outro diretório, use sua opção nativa de `cwd` ou de
diretório do projeto. Não copie caminhos absolutos de outra máquina.

## 6. Verifique

No cliente MCP:

1. liste as tools;
2. execute `health_check`;
3. execute `vault_stats`;
4. faça uma busca com uma query sintética.

O registro esperado contém 43 tools e 6 resources.

## Daemon opcional

O daemon mantém modelos carregados entre processos MCP. Instale depois que a
indexação local funcionar:

```bash
# macOS
./scripts/install-daemon.sh

# Linux
./scripts/install-daemon-linux.sh

curl --fail http://127.0.0.1:9847/health
```

Para executar manualmente, use `uv run vault-search-daemon` ou
`uv run python -m vault_search daemon`.

Durante o warmup, `/health` retorna 503. O instalador aguarda o estado `ready`;
uma falha depois da janela restaura a unidade anterior.

Leia [../daemon-setup.md](../daemon-setup.md) antes de alterar host ou serviço.

## OCR opcional

PDF com texto nativo usa PyMuPDF. PDF composto apenas por imagens requer
Tesseract no sistema.

```bash
# macOS
brew install tesseract tesseract-lang

# Debian e Ubuntu
sudo apt install tesseract-ocr tesseract-ocr-por tesseract-ocr-eng
```

Confirme os idiomas disponíveis:

```bash
tesseract --list-langs
```

## Ambiente de desenvolvimento

```bash
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy src/vault_search
uv run pytest -m "not slow" --cov=vault_search --cov-report=term \
  --cov-fail-under=65
uv run python scripts/check_publication.py
uv build
```

Consulte [../../CONTRIBUTING.md](../../CONTRIBUTING.md) para contratos de
segurança, documentação e pull request.
