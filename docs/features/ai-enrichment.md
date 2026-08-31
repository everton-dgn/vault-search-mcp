# Enriquecimento externo de frontmatter

Esta integração preenche campos obrigatórios ausentes por meio de um comando
externo. Ela começa desativada e exige consentimento explícito porque conteúdo
da nota pode sair do processo principal.

## Antes de habilitar

Confirme:

- qual processo recebe o conteúdo;
- política de retenção e treinamento do provider;
- credenciais usadas pelo comando;
- campos que podem ser enviados;
- impacto de erro, timeout e saída inválida.

Use um vault sintético na primeira validação.

## Configuração segura

```yaml
frontmatter:
  enabled: true
  mode: "lenient"
  ai:
    enabled: true
    allow_external_processing: true
    provider: "provider-cli-local"
    allow_defer_required_on_create: true
    command:
      - "provider-cli"
      - "--model"
      - "{model}"
    primary_model: "modelo-principal"
    fallback_model: null
    timeout_seconds: 8.0
    max_attempts: 2
    max_note_chars: 12000
  schema:
    summary:
      type: "string"
      on_missing: "require"
      max_length: 500
```

O template aceita `{model}`. `{prompt}` é rejeitado. O conteúdo viaja por stdin
para evitar exposição no argv e em diagnósticos de processos.

## Fluxo

1. A validação identifica campos `on_missing: require` vazios.
2. O job limita o corpo a `max_note_chars`.
3. O comando recebe o modelo no argv e o prompt pelo stdin.
4. A saída precisa conter um objeto JSON.
5. Somente campos obrigatórios que estavam ausentes podem ser mesclados.
6. Falha no modelo principal pode acionar o fallback configurado.

## Tools

### `enrich_frontmatter`

Agenda o processamento de uma ou mais notas. A tool altera frontmatter quando o
job termina com saída válida. Paths repetidos são deduplicados sem mudar a ordem.
Cada job aceita no máximo 1.000 paths Markdown. Rejeições estáveis usam
`too_many_paths`, `queue_full` ou `stopped`.

### `enrich_frontmatter_status`

Consulta um job por ID ou lista estados recentes. O retorno público evita
conteúdo da nota e detalhes internos do processo. Cada job conserva no máximo
100 resultados detalhados e informa `returned` e `truncated`; os contadores
`processed`, `succeeded` e `failed` cobrem o job inteiro.

## Limites da fila

- até 200 jobs podem aguardar, além do job em execução;
- jobs `queued` e `running` não são removidos do histórico;
- o histórico conserva até 200 jobs terminais;
- erros de uma nota viram falha controlada e não encerram a worker;
- durante shutdown, a fila deixa de aceitar entradas e tenta drenar o trabalho
  dentro do timeout de encerramento.

Esses limites protegem memória e tempo de shutdown. Uma rejeição não significa
que a nota foi processada; o cliente precisa verificar `accepted` antes de guardar
o `job_id`.

## Erros esperados

| Condição | Resultado |
|---|---|
| `enabled: false` | Enriquecimento indisponível |
| Consentimento ausente | Configuração rejeitada |
| Provider vazio | Configuração rejeitada |
| `{prompt}` no comando | Configuração rejeitada |
| Comando ou modelo principal ausente | Configuração rejeitada |
| Timeout | Tentativa termina e segue política de retry |
| Saída sem JSON objeto | Resultado descartado |
| Mais de 1.000 paths após deduplicação | `too_many_paths` |
| 200 jobs aguardando | `queue_full` |
| Shutdown iniciado | `stopped` |

## Privacidade operacional

- Não coloque token no YAML nem no array `command`.
- Não registre stdin, stdout integral, corpo da nota ou path absoluto.
- O provider deve receber apenas o mínimo necessário.
- Desabilite a integração antes de trocar de vault ou política.
- Trate texto gerado como não confiável até passar pelo schema.
