# Variáveis de ambiente

O YAML guarda configuração de produto. Variáveis de ambiente selecionam arquivo,
path do vault e modo operacional.

| Variável | Valor | Efeito |
|---|---|---|
| `VAULT_SEARCH_CONFIG` | caminho de arquivo | Seleciona YAML antes dos arquivos da raiz |
| `VAULT_SEARCH_VAULT_PATH` | caminho de diretório | Substitui somente `paths.vault_path` |
| `VAULT_PATH` | caminho de diretório | Alias legado do vault, usado apenas sem `VAULT_SEARCH_VAULT_PATH` |
| `VAULT_SEARCH_DATA_DIR` | caminho de diretório | Substitui `paths.data_dir` para LanceDB, catálogo e cache |
| `VAULT_SEARCH_REQUIRE_DAEMON` | `1` ou `0` | Proíbe ou permite fallback para modelos locais |
| `VAULT_SEARCH_WAIT_DAEMON` | segundos; `0` espera sem limite | Espera o daemon no indexador |
| `VAULT_SEARCH_DAEMON_STARTUP_TIMEOUT` | segundos positivos | Janela usada somente pelos instaladores do daemon |
| `VAULT_SEARCH_WRITE_LOCK_TIMEOUT_SECONDS` | número entre 0 e 300 | Prazo de aquisição do lock de escrita; inválido usa 5 segundos |
| `VAULT_SEARCH_ENV` | `production` ou outro | Seleciona formato de logging |
| `VAULT_SEARCH_LOG_LEVEL` | nível Python | Ajusta nível de log |
| `PYTORCH_ENABLE_MPS_FALLBACK` | `1` ou `0` | Permite fallback de operações MPS |

`VAULT_SEARCH_RUNNING_AS_DAEMON` é interno. O entry point do daemon define a
variável e usuários não devem configurá-la.

## Exemplos

```bash
export VAULT_SEARCH_CONFIG="$PWD/config.yaml"
export VAULT_SEARCH_VAULT_PATH="$PWD/vaults/obsidian_vault"
export VAULT_SEARCH_DATA_DIR="$PWD/data"
export VAULT_SEARCH_LOG_LEVEL="INFO"
```

Para exigir o daemon:

```bash
export VAULT_SEARCH_REQUIRE_DAEMON=1
uv run python -m vault_search.core.indexer --wait-daemon 60
```

## Regras de segurança

- Não coloque segredo em variável documentada no repositório.
- Não imprima o ambiente completo em logs ou relatórios.
- Sanitizar qualquer variável de path antes de compartilhar diagnóstico.
- Reiniciar processos após mudar variáveis.

`VAULT_PATH` e `VAULT_SEARCH_DATA_DIR` ainda são lidas por
`vault_search.config.paths` no primeiro import. Prefira o nome moderno
`VAULT_SEARCH_VAULT_PATH` para o vault. `VAULT_SEARCH_DB_DIR` não é reconhecida
e não altera o runtime.

`VAULT_SEARCH_WRITE_LOCK_TIMEOUT_SECONDS` também é capturada no primeiro import
do módulo de locking. Reinicie o processo após mudar esse valor.
