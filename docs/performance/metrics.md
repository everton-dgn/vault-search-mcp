# Métricas

## Princípio

Uma métrica operacional ajuda a localizar falhas. Ela só vira SLO depois de uma
baseline do ambiente e de uma decisão explícita.

## Fontes

| Fonte | Conteúdo |
|---|---|
| `system_stats` | cache, componentes e métricas internas |
| `vault_stats` | notas, chunks e atualização do índice |
| `health_check` | saúde agregada |
| `benchmark_search` | amostras locais de busca |
| daemon `/stats` | estado dos modelos locais |
| logs estruturados | evento, duração, contagem e código de falha |

## Dimensões seguras

Use nome de operação, status, componente, duração e contagem. Evite query,
conteúdo, título, tag, UUID e path absoluto como label. Valores de alta
cardinalidade pioram custo e podem expor dados.

## Distribuições

Para latência, mantenha dados suficientes para mediana, p95 e quantidade de
amostras. Separe:

- cache frio e aquecido;
- daemon e modelos no processo;
- busca semântica e híbrida;
- indexação completa e incremental;
- sucesso, timeout, cancelamento e falha.

## Alertas derivados da baseline

Exemplos de condição:

- health check falha em sequência;
- p95 se afasta da baseline do mesmo ambiente;
- hit rate cai depois de uma mudança de chave;
- fila de enriquecimento para de avançar;
- contagem do índice diverge do vault;
- reinícios do daemon aumentam.

Não copie threshold de outro hardware. Registre o cálculo e a janela usados.

## Exportação

O projeto ainda não inclui exporter Prometheus ou OpenTelemetry. Uma integração
futura deve preservar nomes estáveis, cardinalidade limitada e privacidade por
default. Até lá, colete tools e logs localmente.
