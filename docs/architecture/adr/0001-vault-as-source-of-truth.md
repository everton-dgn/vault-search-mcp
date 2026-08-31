# ADR-0001: vault como fonte primária

## Status

Aceito.

## Contexto

O sistema mantém LanceDB e catálogos auxiliares para acelerar recuperação e
navegação. Esses artefatos podem ficar incompletos depois de falha, mudança de
schema ou interrupção.

## Decisão

As notas no vault são a fonte primária. Índices e catálogos são derivados e
reconstruíveis. Uma operação de manutenção nunca usa o índice como única cópia
de conteúdo do usuário.

## Consequências

- Backup e recuperação concentram-se no vault.
- Rebuild pode descartar somente artefatos derivados.
- Troca de geração do índice precisa preservar a geração ativa até a nova estar
  pronta.
- Metadados que existem apenas no índice devem ser evitados ou explicitamente
  classificados como efêmeros.
