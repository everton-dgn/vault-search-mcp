# Sistema de links indexados

O sistema de links indexados evita reparsing completo do vault em consultas de
backlinks, outlinks e análises de grafo.

## Visão geral

Durante a indexação, o sistema extrai e armazena todos os links encontrados nas notas:

- **Wikilinks**: `[[nota]]`, `[[nota|alias]]`, `[[nota#heading]]`, `[[nota^block]]`
- **Markdown links**: texto com destino relativo ou externo
- **Embeds**: `![[imagem.png]]`, `![[nota]]`
- **URLs externas**: `https://...` (opcional)

Os payloads numéricos desta página são fixtures sintéticas para mostrar o
formato. Eles não representam um vault medido.

## Tabelas

### links_index

Armazena todos os links extraídos.

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `from_note_path` | string | Nota de origem do link |
| `from_note_title` | string | Título da nota de origem |
| `link_type` | string | `wikilink`, `markdown`, `embed`, `external` |
| `link_target` | string | Target original do link |
| `link_target_normalized` | string | Target normalizado para matching |
| `to_note_path` | string | Path da nota alvo (se resolvido) |
| `is_resolved` | bool | Se o link foi resolvido para uma nota |
| `alias` | string | Alias do link (`[[nota\|alias]]`) |
| `heading` | string | Heading (`[[nota#heading]]`) |
| `block_ref` | string | Block reference (`[[nota^block]]`) |
| `context` | string | Trecho de texto onde o link aparece |
| `modified_at` | string | Data de modificação da nota |

### note_aliases

Mapeia aliases do frontmatter para notas.

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `note_path` | string | Path da nota |
| `alias` | string | Alias original |
| `alias_normalized` | string | Alias normalizado |

## Ferramentas MCP

### get_backlinks

Encontra notas que linkam para uma nota específica.

```python
get_backlinks(path="minha-nota.md", include_context=True)
```

**Retorno**:
```json
[
  {
    "path": "outra-nota.md",
    "title": "Outra Nota",
    "link_type": "wikilink",
    "link_target": "minha-nota",
    "context": "...texto com [[minha-nota]]..."
  }
]
```

**Execução:** consulta indexada no banco de links. O custo inclui busca e volume
de resultados retornados.

### get_outlinks

Lista todos os links saindo de uma nota.

```python
get_outlinks(path="minha-nota.md")
```

**Retorno**:
```json
{
  "path": "minha-nota.md",
  "wikilinks": [{"target": "outra", "resolved": true, "resolved_path": "outra.md"}],
  "markdown_links": [],
  "embeds": [{"target": "imagem.png", "resolved": false}],
  "external": [{"url": "https://example.com"}],
  "total": 3,
  "broken_count": 1
}
```

### find_broken_links

Encontra links que apontam para notas inexistentes.

```python
find_broken_links(folder="projetos", limit=100)
```

**Retorno**:
```json
{
  "total_broken_links": 5,
  "notes_with_broken_links": 3,
  "returned_notes": 1,
  "has_more": true,
  "notes": [
    {
      "path": "projeto.md",
      "title": "Projeto",
      "broken_links": [
        {"target": "inexistente", "type": "wikilink", "context": "..."}
      ]
    }
  ]
}
```

Os totais cobrem todo o filtro. `notes` contém até `limit` notas,
`returned_notes` informa o tamanho desse recorte e `has_more` mostra se houve
truncamento. Não há argumento `offset`.

### find_orphan_notes

Encontra notas sem nenhum backlink (isoladas no grafo).

```python
find_orphan_notes(folder=None, limit=100)
```

**Retorno**:
```json
{
  "total_notes": 500,
  "total_orphans": 42,
  "orphan_percentage": 8.4,
  "returned_notes": 1,
  "has_more": true,
  "notes": [
    {"path": "isolada.md", "title": "Nota Isolada", "modified_at": "2024-01-01"}
  ]
}
```

`total_notes`, `total_orphans` e `orphan_percentage` são globais dentro do
filtro. A lista respeita `limit`; confira `returned_notes` e `has_more` antes de
tratá-la como conjunto completo. Não há argumento `offset`.

### link_stats

Estatísticas gerais de links do vault.

```python
link_stats(limit=50)
```

**Retorno**:
```json
{
  "total_links": 1234,
  "total_resolved": 1100,
  "total_broken": 34,
  "total_external": 100,
  "resolution_rate": 97.0,
  "unique_sources": 200,
  "unique_targets": 150,
  "most_referenced": [
    {"path": "hub-note.md", "backlinks": 50}
  ],
  "most_outlinks": [
    {"path": "index.md", "outlinks": 30}
  ]
}
```

## Normalização de links

O sistema normaliza targets para matching consistente:

| Original | Normalizado |
|----------|-------------|
| `Meu Projeto` | `meu-projeto` |
| `docs/API.md` | `docs/api` |
| `  nota  ` | `nota` |
| `UPPER CASE` | `upper-case` |

## Resolução de links

Durante a indexação, o sistema tenta resolver cada link:

1. **Match exato**: `link_target_normalized` == `note_path_normalized`
2. **Match por stem**: `link_target_normalized` == `note_stem_normalized`
3. **Match por alias**: `link_target_normalized` == `alias_normalized`

Links resolvidos têm `is_resolved=true` e `to_note_path` preenchido.

## Extração de partes

O parser extrai todas as partes de wikilinks complexos:

```
[[Nota#Heading|alias]]
  └─ target: Nota
  └─ heading: Heading
  └─ alias: alias

[[Nota^block-id]]
  └─ target: Nota
  └─ block_ref: block-id
```

## Aliases do frontmatter

Aliases definidos no frontmatter são indexados:

```yaml
---
title: API Documentation
aliases: [API Docs, Documentação da API]
---
```

Links como `[[API Docs]]` serão resolvidos para esta nota.

## Performance

| Operação | Antes | Depois |
|----------|-------|--------|
| `get_backlinks()` | Reparse do vault | Consulta no índice de links |
| `find_broken_links()` | Não disponível | Consulta e resolução de alvos |
| `find_orphan_notes()` | Não disponível | Agregação do grafo indexado |
| `link_stats()` | Não disponível | Agregação do índice |

Números dependem do vault e do ambiente. Use o protocolo de
[benchmarking](../performance/benchmarking.md).

As tools aplicam limites internos de leitura e retorno. Em vaults maiores que
esses limites, campos chamados `total` podem descrever apenas o conjunto
processado pela chamada atual; não use esses valores como censo sem validar a
cobertura.

## Uso via CLI

```bash
# Reindexar para criar/atualizar índice de links
uv run python -m vault_search.core.indexer

# As ferramentas ficam disponíveis via MCP server
uv run python -m vault_search
```
