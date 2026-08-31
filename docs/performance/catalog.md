# Catálogo SQLite

O catálogo guarda metadados necessários para listagem, pastas e notas recentes.
Ele evita percorrer todo o vault a cada consulta, mas continua sendo um artefato
derivado. Estatísticas de tags consultam o índice LanceDB.

## Dados armazenados

- path relativo e pasta;
- título derivado do nome do arquivo;
- extensão, tamanho e `mtime_ns`.

Tags, UUID e outros campos de frontmatter ficam no índice LanceDB, fora do
catálogo SQLite.

Índices SQLite atendem filtros por pasta, extensão e modificação. A complexidade
real inclui busca no índice e quantidade de linhas retornadas; por isso o
projeto não descreve `list_notes` como custo constante.

## Lifecycle

1. A inicialização cria o schema e reconcilia com o vault.
2. O watcher faz upsert ou delete depois de eventos processados.
3. Uma thread de reconciliação corrige eventos perdidos.
4. Se o catálogo estiver indisponível, `list_notes` pode usar scan do filesystem.

## Consistência

O catálogo pode ficar temporariamente atrás do vault. Consumidores que exigem
estado imediato depois de uma escrita devem usar o retorno da operação ou uma
leitura direta, em vez de assumir reconciliação instantânea.

## Recuperação

Se o arquivo SQLite estiver corrompido, pare os processos, confirme o target e
mova apenas o catálogo reconstruível para a lixeira:

```bash
trash data/notes_catalog.db
```

Reinicie o servidor para reconstruir. O vault não participa dessa limpeza.

## Evidência

Compare catálogo e fallback com o mesmo vault sintético, filtros, limite e
cache. Registre mediana, p95 e linhas retornadas conforme
[benchmarking.md](benchmarking.md).
