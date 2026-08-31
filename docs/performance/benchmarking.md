# Benchmarking reproduzível

## Princípio

Desempenho é um resultado situado. Uma latência sem hardware, dataset, estado de
cache e amostra não serve como baseline do projeto.

## Duas medições diferentes

1. `benchmark_search` mede o caminho de busca já configurado pelo servidor.
2. A indexação inicial mede parsing, chunking, embeddings e gravação do índice.

Publique os resultados separadamente. Um ganho em cache aquecido não descreve o
tempo de primeira execução.

## Manifesto mínimo

```yaml
project:
  version: "0.1.0"
  commit: "<commit-testado>"
environment:
  os: "<sistema-e-versão>"
  python: "3.14.x"
  cpu: "<modelo>"
  ram_bytes: 0
  device: "cpu"
dataset:
  source: "fixture sintética"
  notes: 0
  chunks: 0
  index_bytes: 0
runtime:
  daemon: false
  model_cache: "cold"
  index_cache: "cold"
sample:
  warmups: 0
  runs: 0
```

Substitua todos os valores antes de publicar. O manifesto incompleto é template,
sem valor probatório.

## Busca

No cliente MCP, chame `benchmark_search` com queries sintéticas que representem
termos exatos, paráfrases, siglas e filtros. Registre cada configuração de modo
separado:

- semântica ou híbrida;
- `top_k` e quantidade de candidatos;
- reranker ativo;
- daemon ou processo local;
- cache frio ou aquecido.

Use ao menos uma rodada de aquecimento quando a pergunta for sobre estado
aquecido. Publique mediana e p95, além da quantidade de amostras. Média isolada
esconde caudas.

## Indexação

Crie um vault sintético versionável por gerador, sem copiar notas pessoais.
Registre distribuição de tamanhos e formatos. Limpe apenas artefatos
reconstruíveis por meio recuperável antes da rodada fria.

```bash
time uv run python -m vault_search.core.indexer
```

O comando mede tempo de parede. Para comparar implementações, mantenha lockfile,
modelo, device, dataset e estado de cache constantes.

## Relatório

| Campo | Obrigatório |
|---|---|
| Commit, lockfile e versão Python | Sim |
| Hardware, device e precisão | Sim |
| Notas, chunks e tamanho do índice | Sim |
| Warmups, amostras, mediana e p95 | Sim |
| Comando ou payload MCP | Sim |
| Dados brutos ou arquivo de resultado | Sim |
| Interpretação e limitações | Sim |

## Estado da baseline pública

Ainda não existe uma baseline de release publicada com esse protocolo. Números
antigos sem manifesto devem ser tratados como observações históricas, sem uso em
comparação ou promessa de produto.
