# Catálogo MCP

O servidor registra 43 tools e 6 resources. A lista abaixo deriva dos
decoradores em `src/vault_search/server/` e é validada pelo check de publicação.

## Escolha da busca

| Necessidade | Tool |
|---|---|
| Relação semântica | `search_vault` |
| Termo exato e semântica | `search_vault_hybrid` |
| Restringir a uma pasta | `search_by_folder` |
| Combinar filtros estruturados | `search_advanced` |
| Encontrar notas parecidas | `find_similar_notes` |
| Filtrar por tags | `search_by_tags` |
| Detectar conteúdo duplicado | `search_duplicates` |

Detalhes: [tools-search.md](tools-search.md).

## Busca: 7

| Tool | Propósito |
|---|---|
| `search_vault` | Recuperação vetorial com reranking |
| `search_vault_hybrid` | Combina recuperação vetorial e FTS |
| `search_by_folder` | Busca semântica sob uma pasta |
| `find_similar_notes` | Usa uma nota como referência |
| `search_duplicates` | Agrupa notas por similaridade |
| `search_advanced` | Aplica filtros de pasta, data e frontmatter |
| `search_by_tags` | Seleciona notas por tags |

## Navegação: 10

| Tool | Propósito |
|---|---|
| `get_backlinks` | Links que apontam para uma nota |
| `get_outlinks` | Links que saem de uma nota |
| `find_broken_links` | Alvos sem nota resolvida |
| `find_orphan_notes` | Notas sem ligações conhecidas |
| `link_stats` | Estatísticas do índice de links |
| `get_recent_notes` | Notas alteradas em uma janela |
| `tag_stats` | Frequência e distribuição de tags |
| `folder_tree` | Árvore agregada de pastas |
| `random_note` | Amostra uma nota com filtros |
| `daily_note` | Localiza ou descreve nota diária |

Detalhes: [tools-navigation.md](tools-navigation.md).

## Indexação: 6

| Tool | Efeito |
|---|---|
| `vault_stats` | Lê estatísticas do índice |
| `reindex_vault` | Reconstrói o índice completo |
| `reindex_note` | Atualiza uma nota no índice |
| `sync_vault` | Compara vault e índice, com dry run |
| `compact_index` | Compacta artefatos LanceDB |
| `vector_index_status` | Lê estado do índice ANN |

Detalhes: [tools-indexing.md](tools-indexing.md).

## CRUD e frontmatter: 13

| Tool | Efeito |
|---|---|
| `read_note` | Lê conteúdo e metadados |
| `get_note_metadata` | Lê metadados sem conteúdo integral |
| `list_notes` | Lista notas com filtros e paginação |
| `create_note` | Cria nota nova |
| `write_note` | Substitui conteúdo de nota |
| `append_note` | Acrescenta conteúdo |
| `update_frontmatter` | Mescla ou substitui frontmatter |
| `delete_note` | Move nota para `.trash` |
| `move_note` | Move ou renomeia nota |
| `generate_missing_ids` | Adiciona UUID a notas sem ID |
| `validate_frontmatter` | Valida nota ou conjunto contra schema |
| `enrich_frontmatter` | Agenda enriquecimento externo autorizado |
| `enrich_frontmatter_status` | Consulta job de enriquecimento |

Detalhes: [tools-crud.md](tools-crud.md).

## Grafo: 4

| Tool | Propósito |
|---|---|
| `graph_data` | Exporta nós e arestas com limites |
| `suggest_links` | Sugere relações por similaridade |
| `find_link_clusters` | Componentes conexos e densidade de grafo simples |
| `find_bridge_notes` | Pontos de articulação por Tarjan iterativo |

Detalhes: [tools-graph.md](tools-graph.md).

## Sistema: 3

| Tool | Propósito |
|---|---|
| `system_stats` | Métricas internas e cache |
| `health_check` | Saúde agregada dos componentes |
| `benchmark_search` | Amostra latência no ambiente atual |

Detalhes: [tools-system.md](tools-system.md).

## Resources: 6

| URI | Conteúdo |
|---|---|
| `vault://stats` | Estado resumido do índice |
| `vault://folders` | Árvore de pastas |
| `vault://notes` | Lista de notas |
| `vault://notes/{path*}` | Conteúdo de uma nota por path relativo |
| `vault://search/recent` | Notas recentes |
| `vault://tags` | Estatísticas de tags |

Detalhes: [tools-resources.md](tools-resources.md).

## Efeitos e autorização

Tools de leitura não alteram notas. Indexação modifica apenas artefatos
derivados, salvo geração explícita de UUID. CRUD, geração de IDs e enriquecimento
podem modificar o vault e devem passar pela política de autorização do cliente.

`delete_note` usa a pasta `.trash` do vault. Não existe exclusão permanente no
contrato público.

## Erros

Clientes devem tratar o formato de sucesso e erro como dados estruturados. Uma
mensagem pública não deve conter stack trace, path absoluto, consulta completa
ou conteúdo de nota. Veja [errors.md](errors.md).

## Compatibilidade

A versão 0.1 é alpha. Mudança em nome, argumento, default ou retorno deve entrar
no changelog e nos testes de contrato. A estabilidade compatível passa a ser
obrigatória a partir da versão 1.0.
