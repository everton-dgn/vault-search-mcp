# Cache

## Camadas atuais

| Camada | Chave | Invalidação |
|---|---|---|
| Metadados de nota | path, `mtime_ns` e tamanho | Mudança no stat gera miss; CRUD invalida path |
| Embedding de query | hash da query normalizada | Limite LRU; reconfiguração exige processo novo |
| Índices do sistema operacional | arquivos LanceDB acessados | Gerenciado pelo SO; prewarm é opcional |

## Cache de metadados

`MetadataCache` usa `OrderedDict` e lock. A chave incorpora metadados do sistema
de arquivos, então alteração de conteúdo normalmente cria uma chave nova. CRUD
também remove entradas do path afetado.

Esse mecanismo reduz parsing repetido, mas não elimina corridas entre `stat` e
leitura. A operação de arquivo continua responsável pela consistência.

## Cache de embeddings

`VaultSearcher` mantém embeddings de queries repetidas em memória. O cache tem
limite e métricas de hits e misses expostas por `system_stats`.

Não registre a query para explicar um hit. Hash, contagem e duração bastam para
diagnóstico operacional.

## O que medir

- tamanho e limite de cada cache;
- hits, misses e taxa calculada;
- latência em estado frio e aquecido, separadamente;
- memória do processo antes e depois do aquecimento;
- invalidações após escrita, move e reindexação.

Use [benchmarking.md](benchmarking.md) antes de publicar números.

## Falhas que testes devem cobrir

- arquivo muda mantendo tamanho;
- relógio ou resolução de timestamp não diferencia duas escritas;
- invalidação durante leitura concorrente;
- limite LRU com acesso paralelo;
- cache de query depois de troca do índice.
