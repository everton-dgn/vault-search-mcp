# Registros de decisão arquitetural

ADRs preservam o contexto de decisões que afetam mais de um módulo. Mudança que
invalide uma decisão cria um novo ADR e marca o anterior como substituído.

| ADR | Status | Decisão |
|---|---|---|
| [0001](adr/0001-vault-as-source-of-truth.md) | Aceito | Vault como fonte primária |
| [0002](adr/0002-local-model-daemon.md) | Aceito | Daemon local opcional para modelos |
| [0003](adr/0003-canonical-configuration.md) | Aceito | Um exemplo YAML alinhado ao schema |
| [0004](adr/0004-performance-evidence.md) | Aceito | Benchmark numérico exige manifesto |

## Quando criar um ADR

- mudança de fronteira de confiança;
- novo armazenamento ou formato persistido;
- alteração de compatibilidade MCP;
- decisão de dependência central;
- estratégia de migração ou concorrência;
- política de desempenho ou privacidade.

Um ADR contém contexto, decisão, consequências e alternativas consideradas.
Detalhes transitórios de implementação ficam próximos ao código.
