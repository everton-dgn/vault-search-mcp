# Configuração YAML

`config.example.yaml` é o único exemplo integral e acompanha
`VaultSearchConfig`. Copie-o para `config.yaml` e mantenha a cópia local fora do
Git.

## Precedência

1. `VAULT_SEARCH_CONFIG`, quando aponta para um arquivo existente;
2. `config.yaml` no diretório de trabalho;
3. `config.yml` no diretório de trabalho;
4. `config.yaml` ou `config.yml` na raiz da instalação, se diferente;
5. defaults Pydantic.

O objeto é carregado uma vez e mantido em cache. Reinicie o MCP e o daemon após
alterar o arquivo.

## Seções

| Seção | Responsabilidade |
|---|---|
| `paths` | Vault, diretório de dados e tabela LanceDB |
| `search` | Resultados, candidatos, precisão e paginação |
| `indexing` | Lotes, workers, extensões e pastas ignoradas |
| `fts` | Tokenização neutra ou stemming opt-in por idioma |
| `prewarm` | Carregamento antecipado do índice em memória |
| `embedding` | Modelos, device, precisão e dimensões |
| `chunking` | Tamanho, overlap, headers e separadores |
| `security` | Limites de input, path, frontmatter e campos compatíveis |
| `watcher` | Debounce e encerramento de threads |
| `pdf` | OCR, idiomas e DPI |
| `vector_index` | Criação e parâmetros do ANN |
| `navigation` | Profundidade de árvore de pastas |
| `daemon` | Loopback, porta, timeout e detecção automática |
| `frontmatter` | Schema, modo de validação e enriquecimento externo |

## Exemplo mínimo

```yaml
paths:
  vault_path: "vaults/obsidian_vault"
  data_dir: "data"

embedding:
  device: "auto"
  use_fp16: null

frontmatter:
  enabled: false
  ai:
    enabled: false
    allow_external_processing: false
    provider: null
```

Campos omitidos recebem defaults. O arquivo integral explica cada valor.

## Paths

Caminhos relativos usam o diretório que contém o YAML selecionado. Sem arquivo,
os defaults usam o diretório de trabalho. `~` e caminhos absolutos são
resolvidos pelo runtime. Evite registrar o resultado resolvido em issue ou
documentação pública.

O override `VAULT_SEARCH_VAULT_PATH` troca somente o vault. O alias legado
`VAULT_PATH` é usado quando a variável moderna não existe.
`VAULT_SEARCH_DATA_DIR` troca o diretório de dados. Esses aliases são fixados no
primeiro import de `vault_search.config.paths`; reinicie o processo após mudar o
ambiente. `VAULT_SEARCH_DB_DIR` não é reconhecida.

## Device e precisão

O padrão `auto` escolhe um backend disponível e `null` decide a precisão em
runtime. Para fixar CPU:

```yaml
embedding:
  device: "cpu"
  use_fp16: false
```

Valide a combinação no hardware alvo. Um device aceito pelo schema ainda pode
falhar por versão de driver, backend ou operação sem suporte.

## Extensões

O conjunto público é `.md`, `.mdx`, `.txt`, `.pdf` e `.canvas`. Se alterar
`indexing.extensions`, use um subconjunto desses formatos. Valores sem parser,
duplicados, sem ponto ou com maiúsculas são rejeitados durante a carga.

`indexing.ignored_folders` compara nomes simples de pasta em qualquer nível. Por
isso, paths com `/` ou `\\`, `.` e `..` são inválidos. `.git`, `.obsidian`,
`.smart-env` e `.trash` começam ignorados.

## Busca textual

`fts.language: null` usa tokenização neutra e é o default para vaults
multilíngues. Defina um idioma aceito pelo backend somente quando quiser stemming
específico. A alteração exige reconstruir o índice FTS para afetar buscas já
indexadas.

## Frontmatter e processamento externo

A validação de schema e o enriquecimento externo começam desativados.
Processamento externo exige `allow_external_processing: true`, provider
declarado e comando seguro. O template aceita somente `{model}`. Conteúdo da
nota segue por stdin, sem interpolação em argumento de shell.

Nunca salve token ou credencial no YAML. Use o mecanismo de segredo do processo
externo.

## Validação antecipada

O schema interrompe a carga quando encontra uma combinação que só falharia em
runtime:

- `search.candidates` acima de `candidates_max`;
- `top_k` fora do intervalo configurado;
- limite padrão de `list_notes` acima do máximo;
- profundidade padrão de `folder_tree` acima do limite público;
- `num_sub_vectors` incompatível com a dimensão em `IVF_PQ`;
- enriquecimento habilitado sem consentimento, provider, comando ou modelo.

Campos desconhecidos também são rejeitados. Configurações antigas que ainda
tenham `security.rate_limit`, `security.reindex_timeout` ou
`security.log_query_max_length` precisam remover esses nomes: eles nunca tiveram
efeito no runtime e não fazem parte do contrato público atual.

## Validação programática

```python
from pathlib import Path

from vault_search.config import load_config_from_file

config = load_config_from_file(Path("config.example.yaml"))
print(config.search.top_k)
```

Erros de tipo ou faixa devem interromper a carga da configuração escolhida. Se
um fallback ocorrer, trate o log como falha operacional até confirmar o vault e
o diretório de dados efetivos.
