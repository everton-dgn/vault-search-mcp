# Daemon local de modelos

O daemon mantém embedding e reranking carregados entre processos MCP. Ele é um
componente opcional e local, separado do índice e do vault.

## Limite de segurança

O protocolo HTTP interno não oferece autenticação. O schema, o servidor e o
cliente aceitam somente endereço de loopback. Use `127.0.0.1`, não publique a
porta e não use proxy reverso. Acesso remoto fica fora do contrato até existir
TLS, autenticação, quotas e um modelo de ameaças específico.

## Instalação

Valide primeiro `uv run python -m vault_search.core.indexer` e o arquivo de
configuração.

```bash
# macOS
./scripts/install-daemon.sh

# Linux com systemd de usuário
./scripts/install-daemon-linux.sh
```

Os instaladores:

- localizam `uv` e a raiz do projeto;
- leem o host e a porta da configuração efetiva;
- preservam uma unidade existente antes de sobrescrever;
- registram um serviço de usuário;
- verificam o processo e o endpoint de saúde;
- restauram a unidade anterior quando ativação ou health check falham.

O health check aguarda até 300 segundos por padrão, inclusive no primeiro
download dos modelos. Ajuste a janela para uma instalação lenta sem alterar o
YAML:

```bash
VAULT_SEARCH_DAEMON_STARTUP_TIMEOUT=900 ./scripts/install-daemon.sh
```

No Linux, use a mesma variável com `install-daemon-linux.sh`.

Para executar no terminal sem registrar um serviço:

```bash
uv run vault-search-daemon
# Fronteira equivalente:
uv run python -m vault_search daemon
```

## Verificação

```bash
# URLs abaixo usam a configuração padrão.
curl --fail --silent --show-error http://127.0.0.1:9847/health
curl --fail --silent --show-error http://127.0.0.1:9847/stats
```

Endpoints internos:

| Método | Path | Uso |
|---|---|---|
| GET | `/health` | Identidade e saúde dos modelos |
| GET | `/stats` | Estado operacional agregado |
| POST | `/embed/queries` | Embeddings de queries |
| POST | `/embed/corpus` | Embeddings de chunks |
| POST | `/rerank` | Scores de reranking |

Use as tools MCP para operação normal. Os endpoints servem para integração
interna e diagnóstico local.

`/health` responde HTTP 200 somente no estado `ready`. Durante warmup, falha
parcial ou ausência de um dos modelos, responde HTTP 503 com o estado observado.
Os endpoints de inferência também rejeitam chamadas enquanto o daemon não está
pronto. O encerramento ocorre pelo gerenciador do serviço ou por sinal local.

## macOS

```bash
launchctl print "gui/$(id -u)/com.vault-search.daemon"
tail -f "$HOME/Library/Logs/vault-search-daemon.log"
./scripts/uninstall-daemon.sh
```

## Linux

```bash
systemctl --user status vault-search-daemon
journalctl --user -u vault-search-daemon -f
./scripts/uninstall-daemon-linux.sh
```

Os desinstaladores exigem `trash` ou `trash-put` e preservam logs. No Linux,
`trash-put` costuma vir no pacote `trash-cli`. Eles não apagam o vault nem o
índice.

## Modos do cliente

| Configuração | Comportamento |
|---|---|
| `daemon.auto_use: true` | Usa daemon saudável quando disponível |
| `VAULT_SEARCH_REQUIRE_DAEMON=1` | Falha em vez de carregar modelos locais |
| `--wait-daemon N` | Indexador espera até N segundos |
| `--wait-daemon 0` | Indexador espera sem limite definido |

Um socket aberto não basta para declarar saúde. O cliente deve validar resposta,
schema e identidade do endpoint.

## Falhas

### Serviço registrado sem resposta

1. leia logs sanitizados;
2. execute `/health` com timeout;
3. confirme que a porta pertence ao processo esperado;
4. reinicie o serviço uma vez;
5. use modelos locais se a política permitir.

### Conflito de porta

Não encerre um processo desconhecido automaticamente. Identifique o dono da
porta e escolha outra porta em `config.yaml`, atualizando cliente e serviço.

### Uso de memória

Consumo varia conforme modelo, backend, precisão e versão. Meça no seu ambiente
e registre o manifesto de [performance/benchmarking.md](performance/benchmarking.md).
