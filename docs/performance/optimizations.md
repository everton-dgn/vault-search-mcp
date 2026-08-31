# Otimizações implementadas

Este inventário descreve mecanismos presentes no código. Ele não atribui ganho
numérico sem benchmark reproduzível.

## Menos trabalho repetido

- Cache LRU de metadados com chave baseada em stat.
- Cache de embeddings para queries repetidas.
- Catálogo SQLite para filtros e paginação.
- Índice separado de links e aliases.

## Pipeline de indexação

- Parsing de arquivos em pool de threads.
- Embedding e persistência em lotes.
- Tabelas de staging antes da publicação da geração.
- Compactação periódica de arquivos LanceDB.
- Criação opcional de ANN acima de um limite de chunks.

## Busca

- Candidatos limitados antes do reranking.
- FTS e vetor combinados no modo híbrido.
- Seleção de colunas para reduzir materialização desnecessária.
- Prewarm opcional quando a memória livre comporta a estimativa.

## Lifecycle

- Daemon local reutiliza modelos entre processos.
- Watcher agrupa eventos por debounce.
- Catálogo reconcilia eventos possivelmente perdidos.
- Shutdown coordenado espera seções protegidas.

## Tradeoffs

| Mecanismo | Ganha | Custa |
|---|---|---|
| Cache | Menos cálculo repetido | Memória e invalidação correta |
| Threads | Sobrepõe I/O e parsing | Contenção e pico de memória |
| Batch | Melhor uso do backend | Latência e memória por lote |
| ANN | Reduz candidatos examinados | Build, disco e recall aproximado |
| Prewarm | Evita I/O frio | Memória residente |
| Daemon | Reutiliza modelos | Processo adicional e health check |

## Como provar um ganho

1. Declare a hipótese e a métrica.
2. Fixe commit, lockfile, modelo, dataset e hardware.
3. Meça baseline e variante na mesma ordem alternada.
4. Publique amostras, mediana, p95 e pico de memória.
5. Verifique correção e recall, além de tempo.

O protocolo completo está em [benchmarking.md](benchmarking.md).
