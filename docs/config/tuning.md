# Ajuste por ambiente

## Comece pelo padrão automático

```yaml
embedding:
  device: "auto"
  use_fp16: null

indexing:
  workers: null
  batch_size: 500

prewarm:
  enabled: true
```

Altere um eixo por vez e guarde o manifesto de benchmark. Ajuste sem medição
pode trocar latência por memória ou reduzir qualidade de recuperação.

## Device

| Valor | Uso |
|---|---|
| `auto` | Detecta backend disponível e mantém fallback |
| `cpu` | Maior portabilidade e diagnóstico |
| `mps` | Apple Silicon, sujeito a operações sem suporte |
| `cuda` | GPU NVIDIA com stack compatível |

Fixar device exige teste no hardware alvo. FP16 reduz precisão e memória em
backends compatíveis; `null` deixa a decisão para o runtime.

## Lote e workers

`indexing.batch_size` afeta pico de memória e quantidade de chamadas ao backend.
`indexing.workers` afeta somente parsing paralelo. Aumentar ambos ao mesmo tempo
dificulta atribuir causa.

Sinais de lote grande demais:

- OOM ou swap intenso;
- pausa longa antes de progresso;
- timeout do daemon;
- instabilidade do backend.

Sinais de workers demais:

- disco saturado;
- CPU gasta em troca de contexto;
- memória cresce sem aumento de throughput.

## Candidatos e reranking

`search.candidates`, `candidates_multiplier` e `candidates_max` controlam o pool
antes do reranking. Um pool maior pode melhorar recall e aumentar custo. Meça
latência e qualidade em um conjunto de queries rotulado.

## Busca textual

`fts.language: null` mantém tokenização neutra para conteúdo multilíngue. Um
idioma específico habilita stemming e pode melhorar correspondências nessa
língua, com custo para termos de outras línguas. Reconstrua o FTS e compare o
mesmo conjunto de queries antes de manter a mudança.

## Chunking

Chunk maior preserva contexto e aumenta texto por embedding. Overlap ajuda
passagens nas fronteiras, com custo de índice e repetição. Compare por formato e
tipo de nota, sem usar uma única query como prova.

## ANN

`vector_index.min_chunks` evita criar índice aproximado em bases pequenas.
`num_sub_vectors` precisa ser compatível com a dimensão do embedding e o tipo
de índice. O schema rejeita antecipadamente a combinação inválida em `IVF_PQ`.
`vector_index_status` mostra a configuração efetiva.

## Prewarm e cache

Prewarm tem guardrails de memória. Desative em processos curtos ou ambientes
disputados. Cache de query beneficia repetição, mas benchmark de estado frio
deve começar em processo novo.

## Protocolo

Use [../performance/benchmarking.md](../performance/benchmarking.md) e registre
correção, recall, latência e memória. Uma configuração vira recomendação somente
depois de repetir em ambientes representativos.
