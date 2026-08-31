# Documentação

Este diretório separa tarefas de usuário, referência de contratos, operação e
decisões. Cada afirmação de comportamento deve apontar para código ou teste. Os
números de desempenho seguem o protocolo de benchmark, com ambiente e amostra.

## Primeira leitura

1. [Instalação](operation/installation.md)
2. [Configuração YAML](config/yaml.md)
3. [Catálogo das tools MCP](api/tools.md)
4. [Modelo de ameaças](security/threat-model.md)

## Operação

| Tarefa | Documento |
|---|---|
| Instalar e indexar | [operation/installation.md](operation/installation.md) |
| Manter modelos em um daemon | [daemon-setup.md](daemon-setup.md) |
| Diagnosticar uma falha | [operation/troubleshooting.md](operation/troubleshooting.md) |
| Observar saúde e métricas | [operation/monitoring.md](operation/monitoring.md) |

## Configuração

| Tema | Documento |
|---|---|
| Arquivo canônico e precedência | [config/yaml.md](config/yaml.md) |
| Variáveis de ambiente | [config/variables.md](config/variables.md) |
| Caminhos e dados locais | [config/paths.md](config/paths.md) |
| Ajustes por hardware | [config/tuning.md](config/tuning.md) |

## Referência MCP

- [Catálogo completo](api/tools.md)
- [Busca](api/tools-search.md)
- [CRUD e frontmatter](api/tools-crud.md)
- [Indexação](api/tools-indexing.md)
- [Navegação](api/tools-navigation.md)
- [Grafo](api/tools-graph.md)
- [Sistema](api/tools-system.md)
- [Resources](api/tools-resources.md)
- [Tipos](api/types.md)
- [Erros](api/errors.md)

O catálogo deve declarar `43 tools` e `6 resources`. O check de publicação
compara esses valores com os decoradores em `src/vault_search/server/`.

## Arquitetura

- [Visão geral](architecture/overview.md)
- [Mapa dos módulos](architecture/modules.md)
- [Diagramas](architecture/diagrams.md)
- [Registros de decisão](architecture/decisions.md)

### ADRs

- [ADR-0001: vault como fonte primária](architecture/adr/0001-vault-as-source-of-truth.md)
- [ADR-0002: daemon local de modelos](architecture/adr/0002-local-model-daemon.md)
- [ADR-0003: configuração YAML canônica](architecture/adr/0003-canonical-configuration.md)
- [ADR-0004: evidência de desempenho](architecture/adr/0004-performance-evidence.md)

## Funcionalidades

- [Formatos de arquivo](features/file-formats.md)
- [Schema de frontmatter](features/frontmatter-schema.md)
- [UUID v7](features/uuid-system.md)
- [Índice de links](features/link-index.md)
- [Busca facetada](features/faceted-search.md)
- [Enriquecimento externo](features/ai-enrichment.md)

## Desempenho

- [Como medir](performance/benchmarking.md)
- [Indexação](performance/indexing.md)
- [Cache](performance/cache.md)
- [Catálogo auxiliar](performance/catalog.md)
- [Prewarm](performance/prewarm.md)
- [Instrumentação](performance/metrics.md)
- [Otimizações implementadas](performance/optimizations.md)

Documentos de desempenho explicam mecanismos. Resultados numéricos só podem ser
tratados como baseline quando registram o ambiente exigido pelo protocolo.

## Desenvolvimento e manutenção

- [Estratégia de testes](development/testing.md)
- [Checklist de release](development/release-checklist.md)
- [Política de segurança](../SECURITY.md)
- [Como contribuir](../CONTRIBUTING.md)
- [Changelog](../CHANGELOG.md)

## Regra de atualização

Uma mudança em tool, resource, configuração, comportamento de segurança ou
comando operacional atualiza a documentação no mesmo pull request. Execute:

```bash
uv run python scripts/check_publication.py
```

O script verifica links locais, placeholders públicos, caminhos pessoais,
comandos destrutivos e contagens MCP.
