# Tipos públicos e internos

Os contratos compartilhados ficam em `src/vault_search/type_defs.py`. Respostas
CRUD ficam em `src/vault_search/crud/types.py`. A CI executa mypy sobre todos os
arquivos-fonte do pacote.

## Estados

### `ParseStatus`

| Valor | Significado |
|---|---|
| `success` | Parser produziu registros |
| `empty` | Arquivo válido sem chunks |
| `error` | Falha de parsing |
| `unsupported` | Formato não atendido pelo dispatcher |

### `ReindexStatus`

Valores: `updated`, `empty`, `deleted`, `parse_error`, `error_add_failed`,
`rejected_path_traversal`, `rejected_extension` e `circuit_breaker_open`.

### `FullReindexStatus`

Valores: `completed`, `failed` e `interrupted`.

As três classes herdam de `StrEnum`, então serializam como texto.

## Chunks

### `ChunkRecord`

Registro produzido pelo parser, antes do embedding:

```python
class ChunkRecord(TypedDict):
    note_path: str
    note_title: str
    folder: str
    headers: str
    tags: str
    modified_at: str
    text: str
```

Campos de frontmatter opcionais: `id`, `created_at`, `updated_at`,
`description`, `status`, `note_type`, `category`, `project` e `source`.

### `ChunkWithVector`

Estende `ChunkRecord` com:

```python
vector: list[float]
```

O vetor é armazenado no índice e não faz parte do `SearchResult` público.

### `ParseResult`

Dataclass com slots que separa arquivo vazio de falha:

```python
@dataclass(slots=True)
class ParseResult:
    status: ParseStatus
    chunks: list[ChunkRecord]
    links: list[LinkRecord]
    aliases: list[str]
    error_type: str | None
```

O iterador mantém o unpacking histórico de `chunks`, `links` e `aliases`.

## Links

`LinkRecord` contém origem, target original e normalizado, tipo, contexto e data
de modificação. A resolução acrescenta `to_note_path` e `is_resolved`. Alias,
heading e block reference são opcionais.

```python
class LinkRecord(TypedDict):
    from_note_path: str
    from_note_title: str
    link_type: str
    link_target: str
    link_target_normalized: str
    context: str
    modified_at: str
```

## Busca

```python
class SearchResult(TypedDict):
    note_path: str
    note_title: str
    folder: str
    headers: str
    tags: str
    text: str
    score: NotRequired[float]
```

O score aparece quando o backend fornece distância ou reranking. Ele serve para
ordenar o resultado daquela execução, não é uma probabilidade calibrada e não
deve ser comparado como métrica de qualidade entre modelos ou versões.

## Indexação

### `ReindexResult`

Campos obrigatórios:

```python
chunks_indexed: int
status: ReindexStatus
```

Campos opcionais: `links_indexed`, `aliases_indexed`, `id_added`,
`frontmatter_enriched`, `frontmatter_fields_filled` e `auto_compacted`.

### `IndexStats`

```python
class IndexStats(TypedDict):
    total_chunks: int
    unique_notes: int
    last_modified: str | None
```

### `FullReindexStats`

```python
class FullReindexStats(TypedDict):
    status: FullReindexStatus
    total_notes: int
    total_chunks: int
    duration_seconds: float
```

Campos opcionais registram erros de parsing, preservação do índice anterior,
links, aliases, interrupção e criação do índice vetorial. O retorno de
`dry_run` usa outro formato com contagens observadas no scan, descrito em
[Tools de indexação](tools-indexing.md).

## CRUD

### `NoteContent`

Retorno de `read_note`:

```python
class NoteContent(TypedDict):
    path: str
    content: str
    frontmatter: dict
    body: str
    tags: list[str]
    title: str
    folder: str
    modified_at: str
    size_bytes: int
```

### `NoteMetadata`

Retorno de `get_note_metadata`. Tem os mesmos metadados de `NoteContent`, sem
`content` e `body`.

### `NoteListItem` e `NoteListResult`

```python
class NoteListItem(TypedDict):
    path: str
    title: str
    folder: str
    extension: str
    modified_at: str
    size_bytes: int

class NoteListResult(TypedDict):
    notes: list[NoteListItem]
    total: int
    limit: int
    offset: int
    has_more: bool
```

### `OperationResult`

```python
class OperationResult(TypedDict):
    success: bool
    message: str
    path: str
    error_code: NotRequired[str]
    reindex_status: NotRequired[str]
```

`reindex_status` informa `queued`, `coalesced`, `queue_full` ou `stopped` quando
a operação agenda atualização assíncrona do índice. Algumas operações também
acrescentam avisos de validação, sugestões, campos de UUID ou o ID de um job de
enriquecimento.

## Compatibilidade

Os tipos ajudam o código interno e documentam o formato atual. Durante a fase
alpha, clientes devem ignorar campos extras e tratar campos opcionais como
ausentes. Uma remoção ou renomeação precisa entrar no [changelog](../../CHANGELOG.md)
e nos testes de contrato.
