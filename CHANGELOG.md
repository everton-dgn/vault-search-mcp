# Changelog

Este arquivo registra mudanças que afetam usuários e contribuidores. O formato
segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o projeto
usa [Versionamento Semântico](https://semver.org/lang/pt-BR/).

O histórico anterior à preparação pública não está disponível nesta cópia do
repositório. Por isso, nenhuma data ou release passada foi reconstruída por
suposição.

## [Unreleased]

### Adicionado

- Metadados de pacote e backend de build padronizado com Hatchling.
- CI para análise estática, testes sem modelos, build e auditoria de publicação.
- Gate de ShellCheck para instaladores e desinstaladores do daemon.
- Gate de cobertura mínima de 65% e typecheck do pacote Python completo.
- Política de segurança, guia de contribuição, suporte e código de conduta.
- Configuração canônica em `config.example.yaml`.
- Protocolo de benchmark que separa resultado medido de meta.
- Identidade visual própria para a página inicial do repositório.
- Comando `vault-search-config` para validar o YAML sem imprimir valores ou paths.

### Alterado

- README e documentação reorganizados em torno dos contratos reais do código.
- Comandos de remoção do daemon passam a exigir movimentação para a lixeira.
- Contagem documentada alinhada ao registro atual de 43 tools e 6 resources.
- Health do daemon passa a distinguir `ready` dos estados indisponíveis com
  HTTP 503; o endpoint HTTP de shutdown foi retirado.
- `vault://notes` expõe limite, tamanho do snapshot e `has_more`.
- Grafo usa densidade convencional e pontos de articulação exatos por Tarjan.
- Fila de enriquecimento limita jobs, paths, histórico e resultados; shutdown
  deixa de aceitar entradas e tenta drenar o trabalho pendente.
- Mutações CRUD compartilham lock por path, timeout limitado e detecção de
  revisão antes de substituir ou mover uma nota.
- Cliente do daemon limita respostas a 64 MiB antes de decodificar JSON.
- FTS usa tokenização neutra por padrão; stemming por idioma passa a ser opt-in.
- Modelos de enriquecimento externo deixam de carregar nomes de provider como
  default e passam a ser obrigatórios somente quando o recurso é habilitado.
- Gate de publicação passa a verificar a árvore Git, nomes, conteúdo textual e
  tipos de membro em wheel/sdist.
- Embeddings densos BGE-M3 passam a usar `SentenceTransformer`; o lock padrão
  seleciona PyTorch CPU e deixa variantes CUDA como escolha explícita.

### Corrigido

- Configuração rejeita limites contraditórios de busca, paginação e navegação
  antes de iniciar o runtime.
- Default de `folder_tree` passa a respeitar `navigation.folder_tree_max_depth`.
- `.git` entra nos diretórios ignorados mesmo quando nenhum YAML é carregado.
- `IVF_PQ` rejeita `num_sub_vectors` incompatível com a dimensão do embedding
  antes de iniciar uma indexação.
- Lock multiprocesso recupera uma única troca transitória do diretório interno
  sem abrir mão da validação contra symlinks.

### Removido

- Exemplo legado de configuração incompatível com o schema Pydantic atual.
- Gerador órfão de embeddings de ataque, sem corpus ou constantes no runtime.
- Campos de segurança reservados que nunca aplicaram quota, timeout ou
  truncamento no runtime.

### Segurança

- Arquivos de configuração local, índices, logs e dados do vault passam a ser
  ignorados explicitamente pelo Git.
- Workflow de CI usa permissões mínimas e actions fixadas por commit.
- Schema, cliente e servidor do daemon rejeitam hosts fora de loopback.
- Locks multiprocesso em sistemas com `fcntl` usam arquivos opacos dentro do
  vault; plataformas sem `fcntl` mantêm coordenação entre threads do processo.
- Destinos aninhados da lixeira rejeitam symlinks que escapem do vault.
- O bootstrap sanitiza também mensagens textuais de `SystemExit`.
- A configuração é validada antes do carregamento do runtime MCP e do daemon.
- Dependências de runtime foram atualizadas para versões corrigidas, incluindo
  FastMCP 3 e Transformers 5; a troca removeu os transitivos sem correção
  publicada `diskcache` e `lupa` do ambiente padrão.
