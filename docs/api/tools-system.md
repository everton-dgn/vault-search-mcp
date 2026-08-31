# Tools de sistema

As três tools deste grupo expõem estado do processo atual. Elas ajudam a
diagnosticar uma instalação e a produzir medições locais. Os valores não são
comparáveis entre máquinas sem registrar o protocolo de teste.

## `system_stats`

```python
system_stats(reset: bool = False) -> dict
```

O retorno reúne:

| Chave | Conteúdo |
|---|---|
| `performance.operations` | Contagem e distribuição de latência já coletada pelo processo |
| `cache.metadata_cache` | Tamanho, capacidade, acertos e erros do cache de metadados |
| `cache.embedding_cache` | Estado do cache de embeddings de consulta |
| `catalog.notes_catalog` | Contagens do catálogo ou estado não inicializado |
| `index` | Chunks, notas únicas e última modificação |
| `prewarm.status` | Resultado da tentativa de prewarm deste processo |

Com `reset=True`, a tool monta o resultado e limpa as métricas de operações em
seguida. Caches, catálogo e índice não são apagados.

Exemplo estrutural, sem números de referência:

```python
{
    "performance": {"operations": {}},
    "cache": {
        "metadata_cache": {},
        "embedding_cache": {},
    },
    "catalog": {"notes_catalog": {}},
    "index": {
        "total_chunks": 0,
        "unique_notes": 0,
        "last_modified": None,
    },
    "prewarm": {"status": {}},
}
```

## `health_check`

```python
health_check() -> dict
```

Confere índice, catálogo, modelos e alertas acumulados. O retorno inclui:

```python
{
    "status": "healthy",
    "uptime_seconds": 0.0,
    "components": {
        "index_ready": True,
        "catalog_ready": True,
        "embed_model_loaded": False,
        "reranker_loaded": False,
        "daemon_required": False,
    },
    "alerts": [],
    "alerts_count": 0,
}
```

O exemplo mostra apenas o formato. Ele não representa uma execução medida.

| Status | Condição operacional |
|---|---|
| `healthy` | Índice disponível, sem alerta atual |
| `degraded` | Índice ainda sem chunks, com catálogo disponível |
| `warning` | Há alerta de latência ou cache |
| `unhealthy` | Índice e catálogo indisponíveis, ou daemon obrigatório ausente |

O coletor abre alerta quando o p95 registrado passa de 500 ms ou quando a taxa
de acerto observada de um cache fica abaixo de 0,70. São limites operacionais
fixos do código atual, não metas universais de desempenho.

Modelos descarregados não tornam a saúde inválida por si só. O processo pode
carregá-los sob demanda ou usar o daemon.

## `benchmark_search`

```python
benchmark_search(
    query: str = "test",
    iterations: int = 10,
) -> dict | str
```

Executa `search` com `top_k=10` no processo atual. `iterations` é normalizado
para o intervalo de 1 a 100. O resultado de uma execução válida contém:

```python
{
    "query_length": 4,
    "iterations": 10,
    "mean_ms": 0.0,
    "min_ms": 0.0,
    "max_ms": 0.0,
    "p50_ms": 0.0,
    "p95_ms": 0.0,
}
```

Os zeros documentam tipos e chaves, sem afirmar latência. Se algumas amostras
falham, entram `errors` e `sample_error_type`. Se todas falham, a tool omite as
estatísticas e devolve diagnóstico do ambiente.

Esse microbenchmark não mede recall, qualidade do reranking, concorrência nem
consumo de memória. Para publicar um resultado, siga o
[protocolo de benchmark](../performance/benchmarking.md).
