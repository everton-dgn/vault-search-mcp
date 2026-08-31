# Troubleshooting

## Diagnóstico curto

```bash
uv --version
uv run python --version
uv sync --locked
uv run python scripts/check_publication.py
```

Depois valide configuração, índice e daemon nessa ordem. Sanitizar qualquer
saída antes de compartilhá-la.

## Configuração não aplicada

1. Confirme `VAULT_SEARCH_CONFIG`.
2. Verifique se o arquivo existe e é YAML válido.
3. Reinicie MCP e daemon, pois a configuração fica em cache.
4. Confirme apenas os campos necessários com `get_config()` localmente.

`VAULT_SEARCH_VAULT_PATH` substitui o vault. O alias legado `VAULT_PATH` também
funciona quando a variável moderna não está definida. `VAULT_SEARCH_DATA_DIR`
substitui o diretório de dados. Essas variáveis são lidas no primeiro import,
então reinicie o processo após alterá-las. `VAULT_SEARCH_DB_DIR` não é
reconhecida.

## Vault não encontrado

```bash
uv run python -c "from vault_search.config.paths import VAULT_PATH; print(VAULT_PATH.exists())"
```

Não publique o path impresso. Se `False`, ajuste `paths.vault_path` ou
`VAULT_SEARCH_VAULT_PATH`.

Para conferir o diretório de dados sem imprimir o path, use:

```bash
uv run python -c "from vault_search.config.paths import DATA_DIR; print(DATA_DIR.exists())"
```

## Índice ausente ou vazio

```bash
uv run python -m vault_search.core.indexer
```

Se a reindexação falhar, preserve a geração anterior. Não apague o índice antes
de entender a exceção. Para descartar um artefato comprovadamente reconstruível,
mova-o à lixeira e execute novamente:

```bash
trash data/vault_chunks.lance
uv run python -m vault_search.core.indexer
```

Confirme o target exato antes do comando. O vault nunca faz parte dessa limpeza.

## Daemon indisponível

```bash
curl --fail --max-time 5 http://127.0.0.1:9847/health
```

Se falhar:

- macOS: `launchctl print "gui/$(id -u)/com.vault-search.daemon"`;
- Linux: `systemctl --user status vault-search-daemon`;
- confirme host e porta no YAML;
- retire `VAULT_SEARCH_REQUIRE_DAEMON=1` somente se fallback local for aceito.

Não trate uma porta aberta como prova de que o processo certo está saudável.

## Busca sem resultado

1. Consulte `vault_stats`.
2. Confirme que a extensão é `.md`, `.mdx`, `.txt`, `.pdf` ou `.canvas`.
3. Verifique se a pasta está em `indexing.ignored_folders`.
4. Execute `sync_vault` em dry run.
5. Compare busca semântica e híbrida com uma query sintética.

## PDF sem texto

Confirme se o documento tem camada de texto. Para imagem escaneada:

```bash
tesseract --version
tesseract --list-langs
```

Instale os idiomas declarados em `pdf.ocr_languages` e reinicie o processo.

## MPS ou CUDA falhando

Troque temporariamente para o padrão portável:

```yaml
embedding:
  device: "cpu"
  use_fp16: false
```

Se CPU funcionar, registre driver, backend, modelo, versão do PyTorch e operação
que falhou antes de abrir issue.

## Tool ou resource ausente

```bash
uv run python scripts/check_publication.py
```

O registro esperado tem 43 tools e 6 resources. Divergência indica checkout
incompleto, import interrompido ou documentação desatualizada.

## Erro ao escrever nota

Verifique extensão, tamanho, schema e contenção do path. Use uma fixture sintética
para reproduzir. Não reduza validações de path e não compartilhe conteúdo real.

Se `error_code` for `write_lock_timeout`, outro escritor cooperativo manteve o
lock além do prazo. Releia a nota antes de tentar novamente. O prazo pode ser
ajustado entre 0 e 300 segundos com
`VAULT_SEARCH_WRITE_LOCK_TIMEOUT_SECONDS`; valor inválido volta a 5 segundos.

Se `error_code` for `write_conflict`, a revisão observada mudou durante a
operação. Releia e reconcilie o conteúdo. Em plataformas sem `fcntl`, processos
distintos não compartilham o lock advisory.

## Como abrir uma issue útil

Inclua:

- versão ou commit;
- sistema operacional e Python;
- comando exato;
- erro sanitizado;
- fixture mínima sintética;
- daemon ativo ou não;
- validações já executadas.

Vulnerabilidades seguem [../../SECURITY.md](../../SECURITY.md) por canal privado.
