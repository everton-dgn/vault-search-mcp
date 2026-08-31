# Caminhos e persistência

## Fonte de cada path

| Dado | Configuração | Papel |
|---|---|---|
| Vault | `paths.vault_path`, `VAULT_SEARCH_VAULT_PATH` ou alias `VAULT_PATH` | Fonte primária de notas |
| Dados | `paths.data_dir` ou `VAULT_SEARCH_DATA_DIR` | LanceDB, catálogo e cache reconstruíveis |
| Tabela | `paths.lancedb_table` | Nome lógico dentro do LanceDB |

Caminhos relativos usam o diretório que contém o arquivo YAML carregado. Sem
arquivo, os defaults usam o diretório de trabalho do processo. O runtime
expande `~` e resolve o path sem exigir que o destino já exista.

`VAULT_SEARCH_DB_DIR` não é lida. Os overrides reconhecidos são aplicados no
primeiro import de `vault_search.config.paths` e exigem reinício para mudar.

## Layout padrão

```text
vault-search-mcp/
├── config.yaml                 # local, ignorado pelo Git
├── data/                       # artefatos reconstruíveis
│   ├── vault_chunks.lance/
│   └── notes_catalog.db
└── vaults/
    └── obsidian_vault/         # vault ou symlink controlado
```

## Contenção

Todo path recebido por tool deve ser relativo ao vault. A validação segura:

1. rejeita path absoluto, `..`, byte nulo e extensão não suportada;
2. resolve a raiz e o destino;
3. confirma que o destino resolvido está dentro da raiz;
4. verifica symlinks para impedir escape;
5. repete a garantia no momento da operação quando uma corrida for possível.

Validação somente textual não cobre symlink que muda depois da checagem.

## Backup

Faça backup do vault com a ferramenta já usada para suas notas. Índices podem
ser reconstruídos e não substituem backup. Antes de manutenção, confirme que o
backup consegue restaurar ao menos uma nota de teste.

## Dados públicos

Nunca versione:

- conteúdo de `vaults/`;
- `config.yaml` ou `.env*`;
- `data/`, arquivos `.lance`, `.sqlite` ou `.npy`;
- logs ou dumps que contenham caminhos resolvidos.

Use fixtures sintéticas pequenas em testes e documentação.

## Mudança de vault

Pare MCP e daemon, ajuste a configuração e crie um índice separado. Não reutilize
um índice de outro vault como se fosse compatível. Confirme `vault_stats` e uma
busca sintética antes de liberar operações de escrita.
