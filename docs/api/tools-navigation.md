# Ferramentas de navegação

10 ferramentas para navegação e descoberta no vault.

Os payloads numéricos desta página são fixtures sintéticas que mostram o
formato. Eles não representam um vault medido.

## get_backlinks

Encontra notas que linkam para uma nota específica usando o índice de links.

```python
get_backlinks(
    path: str,              # caminho da nota alvo
    include_context: bool = True  # incluir trecho onde o link aparece
) -> list[dict] | str
```

**Retorno:**
```python
[
    {
        "path": "projetos/roadmap.md",
        "title": "Roadmap",
        "link_type": "wikilink",  # wikilink, markdown, embed
        "link_target": "minha-nota",
        "context": "...relacionado à [[minha-nota]] que define..."
    }
]
```

**Execução:** consulta indexada; o custo varia com o grafo e a quantidade de
resultados.

---

## get_outlinks

Lista os links saindo de uma nota pelo índice de links.

```python
get_outlinks(path: str) -> dict | str
```

**Retorno:**
```python
{
    "path": "projetos/meu-projeto.md",
    "wikilinks": [
        {"target": "roadmap", "resolved": true, "resolved_path": "roadmap.md"}
    ],
    "markdown_links": [
        {"target": "docs/manual.md", "resolved": true}
    ],
    "embeds": [
        {"target": "diagrama.png", "resolved": false}
    ],
    "external": [
        {"url": "https://example.com"}
    ],
    "total": 4,
    "broken_count": 1
}
```

---

## find_broken_links

Encontra links que apontam para notas inexistentes.

```python
find_broken_links(
    folder: str | None = None,  # filtrar por pasta
    limit: int = 100            # máximo de notas (max: 500)
) -> dict | str
```

**Retorno:**
```python
{
    "total_broken_links": 5,
    "notes_with_broken_links": 3,
    "returned_notes": 1,
    "has_more": True,
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

`total_broken_links` e `notes_with_broken_links` cobrem todo o filtro.
`returned_notes` mede o recorte em `notes`; `has_more` indica que outras notas
ficaram fora do limite. A tool não recebe `offset`, e o limite máximo é 500.

**Execução:** consulta e resolução de alvos no índice.

---

## find_orphan_notes

Encontra notas sem nenhum backlink (isoladas no grafo).

```python
find_orphan_notes(
    folder: str | None = None,  # filtrar por pasta
    limit: int = 100            # máximo de notas (max: 500)
) -> dict | str
```

**Retorno:**
```python
{
    "total_notes": 500,
    "total_orphans": 42,
    "orphan_percentage": 8.4,
    "returned_notes": 1,
    "has_more": True,
    "notes": [
        {"path": "isolada.md", "title": "Nota Isolada", "modified_at": "2024-01-01"}
    ]
}
```

Os três totais são globais dentro do filtro, enquanto `notes` respeita `limit`.
`returned_notes` descreve o recorte e `has_more` sinaliza resultado truncado. A
tool não oferece `offset`.

**Execução:** agregação do índice de links e do catálogo.

---

## link_stats

Estatísticas gerais de links do vault.

```python
link_stats(limit: int = 50) -> dict | str
```

**Retorno:**
```python
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

**Execução:** agregação do índice de links.

---

## get_recent_notes

Retorna notas modificadas recentemente.

```python
get_recent_notes(
    days: int = 7,           # janela de tempo (max: 365)
    limit: int = 20,         # máximo de notas (max: 100)
    folder: str | None = None  # filtrar por pasta
) -> list[dict] | str
```

**Retorno:**
```python
[
    {
        "path": "projetos/nota.md",
        "title": "Minha Nota",
        "modified_at": "2024-01-15T10:30:00",
        "folder": "projetos",
        "days_ago": 2
    }
]
```

---

## tag_stats

Retorna estatísticas de tags do vault (tag cloud).

```python
tag_stats(
    limit: int = 50,           # máximo de tags (max: 500)
    folder: str | None = None  # filtrar por pasta
) -> dict | str
```

**Retorno:**
```python
{
    "total_tags": 127,
    "total_notes_with_tags": 892,
    "tags": [
        {"tag": "projeto", "count": 156},
        {"tag": "2024", "count": 89},
        {"tag": "ideia", "count": 45}
    ]
}
```

---

## folder_tree

Retorna a estrutura de pastas do vault como árvore hierárquica.

```python
folder_tree(
    include_counts: bool = True,  # incluir contagem de notas
    max_depth: int = 10           # profundidade máxima (max: 50)
) -> dict | str
```

**Retorno:**
```python
{
    "total_folders": 45,
    "total_notes": 1234,
    "tree": {
        "projetos": {
            "_count": 56,
            "web": {"_count": 23},
            "mobile": {"_count": 12}
        },
        "referencias": {
            "_count": 89,
            "livros": {"_count": 34}
        }
    }
}
```

---

## random_note

Retorna uma nota aleatória do vault.

```python
random_note(
    folder: str | None = None,    # filtrar por pasta
    extension: str | None = None  # filtrar por extensão
) -> dict | str
```

**Retorno:**
```python
{
    "path": "ideias/projeto-x.md",
    "title": "Projeto X",
    "folder": "ideias",
    "extension": ".md",
    "modified_at": "2024-01-15T10:30:00",
    "size_bytes": 2048
}
```

**Uso:** Redescoberta, serendipidade, exploração aleatória.

---

## daily_note

Verifica a existência de uma daily note para uma data específica.

```python
daily_note(
    date: str | None = None,  # data ISO (YYYY-MM-DD), default hoje
    folder: str = "daily"     # pasta das daily notes
) -> dict | str
```

| Parâmetro | Tipo | Default | Descrição |
|-----------|------|---------|-----------|
| date | str \| None | None | Data em formato ISO. Se None, usa data atual |
| folder | str | "daily" | Pasta onde ficam as daily notes |

**Retorno (nota existe):**
```python
{
    "exists": True,
    "path": "daily/2024-01-15.md",
    "title": "2024-01-15",
    "folder": "daily",
    "date": "2024-01-15",
    "modified_at": "2024-01-15T10:30:00",
    "size_bytes": 1024
}
```

**Retorno (nota não existe):**
```python
{
    "exists": False,
    "expected_path": "daily/2024-01-15.md",
    "date": "2024-01-15"
}
```
