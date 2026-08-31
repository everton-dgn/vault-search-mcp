# Resources MCP

O servidor registra seis resources somente de leitura. Todos refletem o estado
local do índice, catálogo ou vault no momento da chamada.

| URI registrada | Fonte | Limite atual |
|---|---|---|
| `vault://stats` | LanceDB | sem paginação |
| `vault://folders` | catálogo SQLite | todas as pastas conhecidas |
| `vault://notes` | catálogo SQLite | primeiras 5.000, sem paginação |
| `vault://notes/{path*}` | arquivo `.md` | uma nota |
| `vault://search/recent` | catálogo SQLite | 50 notas em 7 dias |
| `vault://tags` | LanceDB | todas as tags conhecidas |

## `vault://stats`

Retorna um envelope com as estatísticas do índice:

```python
{
    "uri": "vault://stats",
    "type": "statistics",
    "data": {
        "total_chunks": 0,
        "unique_notes": 0,
        "last_modified": None,
    },
}
```

Os zeros representam um índice vazio.

## `vault://folders`

Monta uma árvore a partir das pastas do catálogo:

```python
{
    "uri": "vault://folders",
    "type": "folder_tree",
    "total_folders": 2,
    "tree": {"projects": {"atlas": {}}},
}
```

O exemplo é uma fixture sintética e documenta somente o formato.

## `vault://notes`

Lista as primeiras 5.000 entradas do catálogo com `path`, `title`, `folder` e
`modified_at`:

```python
{
    "uri": "vault://notes",
    "type": "note_list",
    "total": 0,
    "returned": 0,
    "limit": 5000,
    "has_more": False,
    "notes": [],
}
```

`total` é a contagem global do catálogo, `returned` informa o tamanho do
snapshot, `limit` vale 5.000 e `has_more` indica truncamento. O resource não
recebe argumentos, cursor nem `offset`, portanto não permite buscar as entradas
seguintes. Use `list_notes` quando precisar de paginação, filtro ou um limite
menor.

## `vault://notes/{path*}`

O wildcard preserva subpastas no path relativo:

```text
vault://notes/projects/atlas/decision.md
```

Somente `.md` pode ser lido como conteúdo completo. O retorno inclui `title`,
`content`, `frontmatter`, `modified_at` e `size_bytes`. Path inválido, extensão
não legível e nota ausente retornam erro público.

## `vault://search/recent`

Consulta no máximo 50 notas modificadas nos últimos 7 dias:

```python
{
    "uri": "vault://search/recent",
    "type": "recent_notes",
    "days": 7,
    "total": 0,
    "notes": [],
}
```

A janela é fixa neste resource. Use `get_recent_notes` para escolher dias,
pasta ou limite.

## `vault://tags`

Retorna cada tag conhecida e sua contagem:

```python
{
    "uri": "vault://tags",
    "type": "tag_stats",
    "total_unique_tags": 0,
    "tags": [],
}
```

## Consistência e erros

Resources baseados no catálogo podem refletir a última reconciliação concluída.
Após alteração externa, aguarde o watcher ou use `sync_vault`. Erros internos
passam pelo envelope público sanitizado; detalhes permanecem no log local.

Veja [Catálogo](../performance/catalog.md) e [Erros da API](errors.md).
