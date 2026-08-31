# Tools de indexação

As seis tools deste grupo operam sobre o índice derivado em LanceDB. Elas não
substituem backups do vault. `reindex_vault`, `reindex_note`, `sync_vault` e
`compact_index` alteram dados derivados; `vault_stats` e
`vector_index_status` são leituras.

## `vault_stats`

```python
vault_stats() -> dict
```

Retorna:

```python
{
    "total_chunks": 0,
    "unique_notes": 0,
    "last_modified": None,
}
```

Os valores acima representam um índice vazio. Em uso, as contagens e a data
refletem o estado local no momento da chamada.

## `reindex_vault`

```python
reindex_vault(
    dry_run: bool = False,
    require_daemon: bool = False,
) -> dict | str
```

Reconstrói chunks, links e aliases em tabelas de staging. O índice anterior é
mantido quando parsing ou commit falham. A troca para as tabelas novas ocorre
somente depois que o conjunto foi processado.

Use primeiro:

```python
reindex_vault(dry_run=True)
```

O dry run escaneia arquivos e devolve `would_index`, distribuição por extensão
e batch calculado. São contagens observadas no scan atual. Ele não faz parsing,
não gera chunks, não carrega modelos e não modifica o índice. Portanto, o
retorno não estima duração nem quantidade futura de chunks.

Na execução real, o retorno inclui `status`, `total_notes`, `total_chunks` e
`duration_seconds`. Campos adicionais descrevem links, aliases, índice vetorial,
erros de parsing ou preservação do índice anterior.

Com `require_daemon=True`, a execução espera até 30 segundos pelo daemon e
retorna erro público se ele continuar indisponível. `dry_run=True` não exige
modelos.

## `reindex_note`

```python
reindex_note(path: str) -> dict | str
```

Atualiza uma nota de forma incremental. O path precisa ser relativo e ter uma
extensão habilitada. Se o arquivo deixou de existir, os registros antigos são
retirados do índice.

O retorno informa `status` e `chunks_indexed`. Estados possíveis cobrem
atualização, remoção, arquivo vazio, rejeição de path ou extensão, erro de
parsing e falha de escrita.

## `sync_vault`

```python
sync_vault(
    dry_run: bool = False,
    require_daemon: bool = False,
) -> dict
```

Compara o scan atual com `note_path` e `modified_at` do índice. Detecta arquivos
novos, alterados e ausentes. O retorno contém:

```python
{
    "vault_files": 0,
    "indexed_files": 0,
    "new_files": 0,
    "modified_files": 0,
    "deleted_files": 0,
    "synced": 0,
}
```

As contagens acima ilustram um vault e índice vazios. `dry_run=True` relata as
diferenças observadas, deixa `synced` em zero e não reindexa. Na execução normal,
removidos são tratados primeiro, seguidos por novos e modificados.

`require_daemon` tem o mesmo contrato de `reindex_vault`. A variável
`VAULT_SEARCH_REQUIRE_DAEMON=1` também força esse comportamento.

## `compact_index`

```python
compact_index() -> dict | str
```

Solicita a otimização das tabelas LanceDB após várias atualizações incrementais.
O formato exato das estatísticas depende da versão do backend. Rode a operação
em um período sem escrita concorrente e verifique o retorno antes de automatizar
decisões.

## `vector_index_status`

```python
vector_index_status() -> dict
```

Retorna `exists`, `auto_create_enabled`, `threshold`, `total_chunks` e
`would_create`. `would_create` indica o que a configuração faria ao reconstruir
o índice; ele não cria o índice durante a consulta.

## Escolha operacional

| Situação | Tool indicada |
|---|---|
| Uma nota mudou | `reindex_note` |
| Servidor ficou parado durante alterações | `sync_vault(dry_run=True)` e depois `sync_vault()` |
| Configuração de parsing ou embedding mudou | `reindex_vault(dry_run=True)` e depois `reindex_vault()` |
| Muitas atualizações incrementais ocorreram | `compact_index` |
| Precisa conferir o índice ANN | `vector_index_status` |

## Recuperação

Se uma reindexação completa retornar `previous_index_preserved: true`, o índice
anterior continua canônico. Corrija o erro de parsing ou escrita e execute
novamente. Caso o processo tenha sido interrompido, confira `status` e
`timed_out` antes de decidir a próxima tentativa.

Veja [Solução de problemas](../operation/troubleshooting.md) para falhas de
daemon, permissão e índice ausente.
