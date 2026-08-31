# Tools de CRUD e frontmatter

O servidor registra 13 tools neste grupo. As operações de leitura aceitam
caminhos relativos ao vault. Operações de escrita validam extensão, pasta,
tamanho e travessia de diretório antes de tocar no arquivo.

## Matriz de efeitos

| Tool | Lê arquivo | Escreve vault | Atualiza índice |
|---|---:|---:|---:|
| `read_note` | sim | não | não |
| `get_note_metadata` | sim | não | não |
| `list_notes` | catálogo ou filesystem | não | não |
| `create_note` | não | sim | em segundo plano |
| `write_note` | sim, quando existe | sim | em segundo plano |
| `append_note` | sim | sim | em segundo plano |
| `update_frontmatter` | sim | sim | em segundo plano |
| `delete_note` | não | move para `.trash/` | em segundo plano |
| `move_note` | não | move | origem e destino em segundo plano |
| `generate_missing_ids` | sim | opcional | notas alteradas em segundo plano |
| `validate_frontmatter` | opcional | não | não |
| `enrich_frontmatter` | sim | job assíncrono pode escrever | após cada alteração |
| `enrich_frontmatter_status` | não | não | não |

A atualização em segundo plano invalida o cache imediatamente. O watcher pode
reconciliar o índice caso a atualização assíncrona falhe.

## Escrita concorrente

Todas as mutações de nota participam do mesmo protocolo de lock por path.
`move_note` adquire origem e destino em ordem determinística. Em sistemas com
`fcntl`, o lock advisory também coordena processos que usam esta biblioteca;
sem `fcntl`, a coordenação cobre somente threads do processo atual.

O timeout padrão é de 5 segundos e aceita override entre 0 e 300 segundos por
`VAULT_SEARCH_WRITE_LOCK_TIMEOUT_SECONDS`. Quando o prazo termina, o arquivo
permanece inalterado e o resultado contém
`error_code: "write_lock_timeout"`. Uma mudança detectada entre leitura e
persistência retorna `error_code: "write_conflict"`.

Escritores externos que ignoram o lock não ficam serializados. As operações
comparam inode, `mtime_ns` e tamanho antes de substituir ou mover o arquivo,
mas o cliente ainda deve tratar conflitos como motivo para reler a nota.

## Leitura

### `read_note`

```python
read_note(path: str) -> dict | str
```

Lê uma nota `.md` completa. O resultado contém `content`, `frontmatter`,
`body`, `tags`, `title`, `folder`, `modified_at` e `size_bytes`. Para outros
formatos, use uma tool de busca.

### `get_note_metadata`

```python
get_note_metadata(path: str) -> dict | str
```

Lê somente o frontmatter e os metadados de uma nota `.md`. O resultado contém
`frontmatter`, `tags`, `title`, `folder`, `modified_at` e `size_bytes`, sem o
corpo. A implementação usa cache validado por path, data de modificação e
tamanho.

### `list_notes`

```python
list_notes(
    folder: str | None = None,
    extension: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict | str
```

Lista as extensões habilitadas em `vault.extensions`. O limite padrão é 500 e
o máximo é 5.000. `offset` negativo é normalizado para zero. O resultado traz
`notes`, `total`, `limit`, `offset` e `has_more`, com itens recentes primeiro.

```python
list_notes(folder="projects", extension="md", limit=50, offset=0)
```

## Escrita de Markdown

### `create_note`

```python
create_note(
    path: str,
    content: str,
    frontmatter: dict | None = None,
) -> dict | str
```

Cria uma nota `.md` e falha se o destino já existe. `content` é o corpo, sem o
bloco YAML. O servidor valida o schema configurado e adiciona um UUID v7 quando
`id` não foi fornecido.

```python
create_note(
    path="projects/atlas/decision.md",
    content="# Decision\n\nRecord of the choice.",
    frontmatter={"status": "draft", "tags": ["architecture"]},
)
```

### `write_note`

```python
write_note(path: str, content: str) -> dict | str
```

Escreve o conteúdo completo de uma nota `.md`, incluindo frontmatter se houver.
Pode criar o arquivo ou substituir um existente. A gravação usa arquivo
temporário e troca atômica dentro da mesma pasta.

### `append_note`

```python
append_note(
    path: str,
    content: str,
    separator: str = "\n\n",
) -> dict | str
```

Acrescenta texto a uma nota `.md` existente. O separador só é adicionado quando
o conteúdo atual ainda não termina com ele.

### `update_frontmatter`

```python
update_frontmatter(
    path: str,
    metadata: dict,
    merge: bool = True,
) -> dict | str
```

Com `merge=True`, a mesclagem ocorre em um nível: listas e objetos aninhados
são substituídos. Com `merge=False`, o frontmatter inteiro é trocado. O
resultado passa pelo schema configurado antes da gravação.

## Movimento recuperável

### `delete_note`

```python
delete_note(path: str) -> dict | str
```

Move o arquivo para `.trash/` dentro do vault, preservando a estrutura de
pastas. Colisões recebem um sufixo aleatório. A API não oferece exclusão
permanente.

### `move_note`

```python
move_note(from_path: str, to_path: str) -> dict | str
```

Move ou renomeia uma nota. O destino deve estar livre, ter a mesma extensão da
origem e ficar fora das pastas ignoradas.

## IDs e validação

### `generate_missing_ids`

```python
generate_missing_ids(
    folder: str | None = None,
    dry_run: bool = False,
) -> dict | str
```

Escaneia notas `.md` sem `id`. Comece com `dry_run=True`, que retorna a
contagem e até 100 paths sem alterar arquivos. A execução escreve UUID v7 e
limita os detalhes retornados a 50 sucessos e 10 erros.

### `validate_frontmatter`

```python
validate_frontmatter(
    path: str | None = None,
    frontmatter: dict | None = None,
) -> dict | str
```

Forneça exatamente um dos seletores. O resultado traz `valid`, `errors`,
`warnings`, `suggestions`, `auto_generated` e `validated_data`. A validação não
grava valores gerados no vault.

## Enriquecimento externo

O enriquecimento permanece desativado na configuração pública. Quando ativado,
ele só funciona com schema habilitado, `allow_external_processing: true`,
`provider` explícito e comando configurado. O conteúdo segue por `stdin`; o
template do comando aceita somente `{model}`.

### `enrich_frontmatter`

```python
enrich_frontmatter(
    path: str | None = None,
    paths: list[str] | None = None,
    folder: str | None = None,
    limit: int = 100,
) -> dict | str
```

Forneça exatamente um seletor. A tool agenda um job e devolve `job_id` sem
esperar pelo processamento. No modo `folder`, `limit` fica entre 1 e 1.000.

### `enrich_frontmatter_status`

```python
enrich_frontmatter_status(
    job_id: str | None = None,
    limit: int = 20,
) -> dict | str
```

Com `job_id`, consulta um job. Sem ele, lista os jobs recentes mantidos pelo
processo atual.

## Tratamento de erro

Falhas públicas usam mensagens sanitizadas. O cliente não deve depender do
texto humano; trate campos estruturados quando presentes. Consulte
[Erros da API](errors.md) e o [modelo de ameaças](../security/threat-model.md).
