# Validação do schema de frontmatter

O vault-search-mcp suporta validação configurável do frontmatter de notas markdown usando Pydantic.

## Visão geral

A validação permite:
- Definir tipos esperados para cada campo
- Auto-gerar valores (UUID v7, timestamps)
- Coerção automática de tipos
- Campos obrigatórios, opcionais ou sugeridos
- Aliases para nomes alternativos de campos

## Configuração

### Habilitando no `config.yaml`

```yaml
frontmatter:
  enabled: true
  mode: lenient        # strict | lenient | warn_only
  allow_extra_fields: true

  schema:
    id:
      type: uuid
      on_missing: auto

    created_at:
      type: datetime
      on_missing: auto
      aliases: [created, date]

    status:
      type: enum
      values: [draft, review, published, archived]
      on_missing: suggest

    title:
      type: string
      on_missing: require
      max_length: 200
```

## Tipos suportados

| Tipo | Descrição | Validações |
|------|-----------|------------|
| `string` | Texto | `min_length`, `max_length`, `pattern` |
| `int` | Número inteiro | `minimum`, `maximum` |
| `float` | Número decimal | `minimum`, `maximum` |
| `bool` | Booleano | - |
| `date` | Data ISO (YYYY-MM-DD) | - |
| `datetime` | Data/hora ISO | - |
| `uuid` | UUID v4/v7 | - |
| `url` | URL HTTP(S) | - |
| `enum` | Valor de lista | `values`, `case_insensitive` |
| `list` | Lista de valores | `item_type`, `min_items`, `max_items` |

## Comportamento `on_missing`

Define o que fazer quando um campo está ausente:

| Comportamento | Descrição |
|---------------|-----------|
| `auto` | Gera valor automaticamente (só `uuid` e `datetime`) |
| `suggest` | Aceita, mas retorna sugestão para adicionar |
| `require` | Bloqueia operação se ausente |
| `ignore` | Silenciosamente ignora (default) |

## Modos de validação

| Modo | Descrição |
|------|-----------|
| `strict` | Bloqueia operação se houver erros |
| `lenient` | Bloqueia erros, mas reporta warnings |
| `warn_only` | Converte todos os erros em warnings |

Na implementação atual, `strict` e `lenient` calculam `valid` da mesma forma.
Mantenha essa equivalência em mente até que uma diferença de contrato seja
definida e testada.

## Coerção de tipos

O validador tenta converter valores automaticamente:

```yaml
# YAML
priority: "3"     # String → int
active: "yes"     # String → bool
tags: "a, b, c"   # String → list
```

### Conversões suportadas

| De | Para | Exemplo |
|----|------|---------|
| string numérica | int/float | `"42"` → `42` |
| float | int (trunca) | `3.7` → `3` |
| "true"/"yes"/"1" | bool | → `True` |
| "false"/"no"/"0" | bool | → `False` |
| date | datetime | adiciona `T00:00:00` |
| string c/ vírgulas | list | `"a,b,c"` → `["a","b","c"]` |
| UPPER CASE | enum | normaliza para valor canônico |

### Valores rejeitados

Alguns valores são rejeitados para evitar problemas em JSON/busca:

| Tipo | Valores Rejeitados |
|------|-------------------|
| `int` | NaN, Infinity, -Infinity |
| `float` | NaN, Infinity, -Infinity |

## Aliases

Permite múltiplos nomes para o mesmo campo:

```yaml
schema:
  created_at:
    type: datetime
    aliases: [created, date, timestamp]
```

Qualquer alias é aceito e normalizado para o nome canônico:
- `created: 2024-01-15` → `created_at: 2024-01-15T00:00:00`

### Conflito de aliases

Se o campo canônico e um alias estiverem ambos presentes, o valor canônico é usado e um warning é gerado:

```python
# Input
{"created_at": "2024-01-15", "created": "2024-01-20"}

# Result
# created_at = "2024-01-15" (canônico vence)
# warning: "Conflito: alias 'created' ignorado porque campo 'created_at' já existe"
```

## Resultado da validação

```python
{
    "valid": True,
    "errors": [],           # Erros que bloqueiam
    "warnings": [],         # Coerções aplicadas
    "suggestions": [],      # Campos sugeridos
    "auto_generated": {     # Campos gerados automaticamente
        "id": "019c503c-08e7-707f-9441-f4e6c5d0dd61",
        "created_at": "2024-01-15T10:30:00"
    },
    "validated_data": {...} # Dados finais após validação
}
```

## API

### Ferramenta MCP: `validate_frontmatter`

```python
# Validar nota existente
validate_frontmatter(path="notes/minha-nota.md")

# Validar dict diretamente
validate_frontmatter(frontmatter={
    "status": "draft",
    "tags": ["ai", "python"]
})
```

### Python

```python
from vault_search.frontmatter import FrontmatterValidator
from vault_search.frontmatter.schema import FieldSchema, FrontmatterSchemaConfig

config = FrontmatterSchemaConfig(
    enabled=True,
    mode="lenient",
    schema={
        "title": FieldSchema(type="string", on_missing="require"),
    },
)

validator = FrontmatterValidator(config)
result = validator.validate({"title": "Minha Nota"})
```

## Integração com CRUD

### `create_note`

Validação automática no `create_note`:

```python
create_note(
    path="notes/nova.md",
    content="# Conteúdo",
    frontmatter={"status": "draft"},
)
```

A tool MCP sempre executa a validação configurada; ela não expõe um argumento
para desativá-la por chamada.

Se validação falhar:
- Modo `strict`/`lenient`: retorna erro
- Modo `warn_only`: prossegue com warnings

### `update_frontmatter`

Validação também disponível no `update_frontmatter`:

```python
update_frontmatter(
    path="notes/existente.md",
    metadata={"status": "published"},
    merge=True,
)
```

### `ensure_note_id`

`ensure_note_id` é uma função interna usada pela indexação. No contrato MCP,
use `generate_missing_ids(dry_run=True)` para revisar um lote ou `reindex_note`
para processar uma nota.

```python
generate_missing_ids(folder="notes", dry_run=True)
```

### Geração automática de campos

Campos com `on_missing: auto` são gerados automaticamente:

```python
# Input
create_note("note.md", "# Hi", frontmatter={})

# Output (frontmatter gerado)
---
id: 019c503c-08e7-707f-9441-f4e6c5d0dd61
created_at: 2024-01-15T10:30:00
---
```

## Exemplos de schema

### Blog/Artigos

```yaml
schema:
  id:
    type: uuid
    on_missing: auto

  title:
    type: string
    on_missing: require
    max_length: 200

  status:
    type: enum
    values: [draft, review, published]
    on_missing: suggest
    default: draft

  published_at:
    type: datetime
    on_missing: ignore

  tags:
    type: list
    item_type: string
    on_missing: ignore

  author:
    type: string
    on_missing: suggest
```

### Notas diárias

```yaml
schema:
  id:
    type: uuid
    on_missing: auto

  date:
    type: date
    on_missing: require
    aliases: [created, dia]

  mood:
    type: enum
    values: [great, good, okay, bad]
    on_missing: ignore

  energy:
    type: int
    minimum: 1
    maximum: 10
    on_missing: ignore
```

### Documentação técnica

```yaml
schema:
  id:
    type: uuid
    on_missing: auto

  version:
    type: string
    pattern: "^\\d+\\.\\d+\\.\\d+$"  # semver
    on_missing: suggest

  deprecated:
    type: bool
    on_missing: ignore
    default: false

  source:
    type: url
    on_missing: ignore
```

## Arquitetura

```
src/vault_search/frontmatter/
├── __init__.py       # Exports públicos
├── types.py          # TypedDicts (ValidationError, ValidationResult)
├── schema.py         # Modelos Pydantic (FieldSchema, FrontmatterSchemaConfig)
├── coercion.py       # Funções de coerção para cada tipo
└── validator.py      # FrontmatterValidator principal
```

## Veja também

- [UUID v7 nas notas](uuid-system.md)
- [Tools de CRUD e frontmatter](../api/tools-crud.md)
- [Configuração YAML](../config/yaml.md)
