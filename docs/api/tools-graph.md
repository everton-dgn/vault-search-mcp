# Tools de grafo

As quatro tools usam o índice de links para produzir um grafo de notas. Links
externos ficam fora das arestas. Os exemplos abaixo são fixtures sintéticas e
mostram somente o formato.

## `graph_data`

```python
graph_data(
    folder: str | None = None,
    include_orphans: bool = False,
) -> dict | str
```

Retorna nós e arestas direcionadas:

```python
{
    "nodes": [
        {"id": "notes/a.md", "label": "A", "outlinks": 1, "backlinks": 0},
        {"id": "notes/b.md", "label": "B", "outlinks": 0, "backlinks": 1},
    ],
    "edges": [{"source": "notes/a.md", "target": "notes/b.md"}],
    "stats": {"total_nodes": 2, "total_edges": 1, "orphan_nodes": 0},
}
```

`folder` filtra origens sob a pasta. `include_orphans=True` acrescenta notas do
catálogo sem links conhecidos. O índice limita a leitura a 100.000 registros e
o catálogo de órfãs a 10.000 notas, então o payload pode ser uma visão parcial
de conjuntos maiores.

O formato `nodes` e `edges` é simples de adaptar para bibliotecas de
visualização; ele não é um arquivo nativo de Gephi nem do Obsidian.

## `suggest_links`

```python
suggest_links(
    path: str,
    limit: int = 10,
    min_similarity: float = 0.7,
) -> list[dict] | str
```

Busca notas semanticamente parecidas e remove a própria nota e targets já
ligados. `limit` fica entre 1 e 50. Cada sugestão contém `path`, `title`,
`similarity` e `folder`.

```python
[
    {
        "path": "notes/b.md",
        "title": "B",
        "similarity": 0.0,
        "folder": "notes",
    }
]
```

O zero acima documenta o tipo. Uma execução real só inclui itens cujo score
atinge `min_similarity`. O score não é probabilidade calibrada; revise a nota
antes de criar o link.

## `find_link_clusters`

```python
find_link_clusters(
    min_cluster_size: int = 3,
    folder: str | None = None,
) -> dict | str
```

Converte as arestas em grafo não direcionado e encontra componentes conexos com
BFS. `min_cluster_size` fica entre 2 e 100. O retorno contém até 20 clusters e
até 20 notas por cluster, com `truncated` quando houver mais.

```python
{
    "total_clusters": 0,
    "largest_cluster_size": 0,
    "clusters": [],
}
```

`density` segue a definição de grafo simples não direcionado:
`m / (n * (n - 1) / 2)`. Arestas repetidas são deduplicadas e self-loops ficam
fora de `m`, portanto o resultado permanece entre 0 e 1.

## `find_bridge_notes`

```python
find_bridge_notes(
    limit: int = 20,
    folder: str | None = None,
) -> dict | str
```

Executa Tarjan iterativo em `O(V + E)` no grafo não direcionado e retorna os
pontos de articulação: notas cuja remoção aumenta o número de componentes
conectados. O limite fica entre 1 e 100.

```python
{
    "total_bridge_notes": 3,
    "returned_notes": 1,
    "has_more": True,
    "notes": [
        {
            "path": "notes/bridge.md",
            "title": "Bridge",
            "bridge_score": 2,
            "separated_branches": 2,
            "connections": 4,
        }
    ],
}
```

`total_bridge_notes` cobre todo o grafo observado; `returned_notes` e `has_more`
descrevem o recorte limitado. `separated_branches` registra o score estrutural,
e `bridge_score` permanece como alias compatível. O cálculo é exato para a visão
de `graph_data`; se essa visão atingiu seus limites de leitura, o resultado não
cobre arestas que ficaram fora dela.

## Consistência

As quatro tools veem a última geração indexada. Depois de editar links, aguarde
o watcher ou execute `reindex_note`. Veja [Links indexados](../features/link-index.md)
e [Tools de indexação](tools-indexing.md).
