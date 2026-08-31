# ADR-0004: evidência de desempenho

## Status

Aceito.

## Contexto

Números sem hardware, dataset e estado de cache pareciam promessas universais e
não podiam ser reproduzidos.

## Decisão

Documentos descrevem mecanismos e complexidade com qualificadores. Baselines
numéricas exigem o manifesto de `docs/performance/benchmarking.md`, dados brutos
e commit testado. Metas aparecem identificadas como metas.

## Consequências

- README não usa latência ou consumo de memória sem evidência anexada.
- Comparações mantêm dataset, modelo, lockfile e estado de cache constantes.
- Um resultado local pode orientar investigação, mas só vira baseline após
  publicação do protocolo completo.
