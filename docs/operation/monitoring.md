# Monitoramento

## Objetivo

Monitorar este projeto significa detectar indisponibilidade, fila de trabalho,
falha de sincronização e pressão de recursos sem registrar conteúdo do vault.

## Sinais disponíveis

| Sinal | Interface | Uso |
|---|---|---|
| `health_check` | Tool MCP | Estado agregado do servidor e índice |
| `system_stats` | Tool MCP | Métricas internas e cache |
| `vault_stats` | Tool MCP | Notas, chunks e atualização do índice |
| `/health` | Daemon HTTP local | Identidade e saúde dos modelos |
| `/stats` | Daemon HTTP local | Uso agregado do daemon |
| logs estruturados | stderr, journald ou arquivo local | Eventos e falhas sanitizados |

## Check local do daemon

```bash
curl --fail --silent --show-error --max-time 5 \
  http://127.0.0.1:9847/health
```

Não use esse endpoint por rede. Uma resposta HTTP válida deve também ter o
schema e a identidade esperados. O status HTTP é 200 somente quando os dois
modelos estão carregados e o estado é `ready`; estados de inicialização,
degradação e falha retornam 503.

## Estado do índice

Registre ao menos:

- total de notas e chunks;
- timestamp da última atualização;
- arquivos novos, alterados e removidos no sync check;
- geração ativa do índice, quando disponível;
- status do ANN e do FTS.

Mudança brusca na contagem merece investigação antes de uma reindexação
destrutiva.

## Logs seguros

Eventos podem conter código estável, duração, contagem e nome de componente.
Evite:

- consulta completa;
- trecho de nota;
- path absoluto ou nome de usuário;
- frontmatter, tag privada ou UUID desnecessário;
- token, variável de ambiente completa ou stack trace devolvido ao cliente.

Para compartilhar um diagnóstico, substitua paths por nomes sintéticos e revise
o arquivo linha a linha.

## Alertas

O projeto ainda não publica thresholds universais. Defina alertas a partir de
uma baseline do seu ambiente. Exemplos de condição, sem número prescrito:

- health check falha consecutivamente;
- sync permanece pendente além da janela normal;
- índice perde notas sem alteração correspondente no vault;
- p95 de busca aumenta em relação à baseline do mesmo ambiente;
- daemon reinicia repetidamente;
- memória livre cruza o limite operacional definido pelo mantenedor.

Registre o protocolo de [../performance/benchmarking.md](../performance/benchmarking.md)
antes de transformar uma observação em SLO.
