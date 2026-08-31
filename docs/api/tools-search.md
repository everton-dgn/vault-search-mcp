# Ferramentas de busca

7 ferramentas para busca semântica, híbrida e filtrada.

## search_vault

Busca semântica com reranking.

```python
search_vault(query: str, top_k: int = 10) -> list[dict]
```

| Parâmetro | Tipo | Default | Descrição |
|-----------|------|---------|-----------|
| query | str | required | Texto de busca (qualquer idioma) |
| top_k | int | 10 | Quantidade de resultados (max: 100) |

**Retorno:**
```python
[
    {
        "note_path": "projetos/meu-projeto.md",
        "note_title": "Meu Projeto",
        "folder": "projetos",
        "headers": "## Introdução",
        "tags": "#projeto #2024",
        "text": "Conteúdo relevante...",
        "score": 0.89
    }
]
```

O exemplo é sintético. `score` ordena resultados daquela execução e não é uma
probabilidade calibrada.

---

## search_vault_hybrid

Busca híbrida: semântica + keywords (FTS).

```python
search_vault_hybrid(query: str, top_k: int = 10) -> list[dict]
```

**Quando usar:** Queries com termos técnicos, siglas ou nomes próprios.

---

## search_by_folder

Busca semântica filtrada por pasta.

```python
search_by_folder(query: str, folder: str, top_k: int = 10) -> list[dict]
```

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| folder | str | Pasta para filtrar (ex: "projetos", "estudos/python") |

---

## search_advanced

Busca facetada: semântica + filtros estruturados + exclusão + highlight.

```python
search_advanced(
    query: str,
    top_k: int = 10,
    tags: list[str] = None,
    folder: str = None,
    extension: str = None,
    date_range: str = None,      # "today", "week", "month", "year"
    date_from: str = None,       # ISO: "2026-01-01"
    date_to: str = None,         # ISO: "2026-12-31"
    status: str = None,          # "draft", "review", "published", "archived"
    note_type: str = None,       # "daily", "weekly", "monthly", "yearly", "meeting", "idea", "task"
    category: str = None,        # "work", "personal", "reference", "project"
    project: str = None,         # nome do projeto
    exclude: list[str] = None,   # termos para EXCLUIR
    highlight: bool = False      # destacar matches com **marcadores**
) -> list[dict]
```

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| tags | list[str] | Tags para filtrar (OR entre elas) |
| folder | str | Pasta para filtrar (inclui subpastas) |
| extension | str | Extensão habilitada: `.md`, `.mdx`, `.txt`, `.pdf` ou `.canvas` por padrão |
| date_range | str | Período: today, week, month, year |
| status | str | Status do frontmatter |
| note_type | str | Tipo de nota do frontmatter |
| category | str | Categoria do frontmatter |
| project | str | Projeto do frontmatter |
| exclude | list[str] | Termos para excluir dos resultados |
| highlight | bool | Destacar termos da query com **marcadores** |

**Exemplos:**
```python
# Excluir resultados com Django ou Flask
search_advanced("python", exclude=["django", "flask"])

# Destacar matches no texto
search_advanced("API REST", highlight=True)
# Resultado: "Implementação de **API** **REST** com..."
```

---

## find_similar_notes

Encontra notas semanticamente similares.

```python
find_similar_notes(path: str, top_k: int = 5) -> list[dict]
```

| Parâmetro | Tipo | Default | Descrição |
|-----------|------|---------|-----------|
| path | str | required | Caminho da nota de referência |
| top_k | int | 5 | Quantidade de notas similares (max: 20) |

**Retorno:**
```python
[
    {
        "note_path": "projetos/similar.md",
        "note_title": "Projeto Similar",
        "folder": "projetos",
        "tags": "projeto",
        "similarity_score": 0.87
    }
]
```

---

## search_by_tags

Busca notas por tags específicas (busca exata, sem semântica).

```python
search_by_tags(
    tags: list[str],           # tags a buscar
    match_all: bool = False,   # True = AND, False = OR
    limit: int = 50            # máximo de resultados (max: 200)
) -> list[dict] | str
```

| Parâmetro | Tipo | Default | Descrição |
|-----------|------|---------|-----------|
| tags | list[str] | required | Tags a buscar (max: 20) |
| match_all | bool | False | True = todas as tags (AND), False = qualquer (OR) |
| limit | int | 50 | Máximo de notas (max: 200) |

**Uso:** Navegação por categorias, complemento ao `tag_stats`, filtros exatos.

---

## search_duplicates

Encontra grupos de notas duplicadas ou muito similares no vault.

```python
search_duplicates(
    threshold: float = 0.90,    # similaridade mínima (0.5-0.99)
    max_notes: int = 500,       # máximo de notas a processar
    folder: str | None = None   # restringir a uma pasta
) -> list[dict] | str
```

| Parâmetro | Tipo | Default | Descrição |
|-----------|------|---------|-----------|
| threshold | float | 0.90 | Similaridade mínima (0.5-0.99) |
| max_notes | int | 500 | Máximo de notas a processar (10-1000) |
| folder | str | None | Restringir busca a uma pasta |

`threshold` é o corte de similaridade de cosseno entre embeddings médios das
notas. Reduzir o valor aumenta a quantidade de pares candidatos. Não há corte
universal para "duplicata": calibre com um conjunto revisado do próprio vault.

**Uso:** Identificar conteúdo duplicado, encontrar notas para mesclar, limpeza do vault.
