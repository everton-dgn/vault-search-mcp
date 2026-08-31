# ADR-0003: configuração YAML canônica

## Status

Aceito.

## Contexto

Exemplos com schemas diferentes fizeram documentação e runtime divergir. Alguns
campos antigos eram aceitos silenciosamente ou ignorados.

## Decisão

`config.example.yaml` é o único exemplo integral. Seu formato espelha
`VaultSearchConfig`. Configuração local usa `config.yaml` ou `config.yml`, ambos
ignorados pelo Git. `VAULT_SEARCH_CONFIG` escolhe outro arquivo explicitamente.
Caminhos relativos são ancorados no diretório do arquivo selecionado.

## Consequências

- Campo novo atualiza schema, exemplo, docs e teste no mesmo pull request.
- Exemplo legado não permanece como alternativa.
- Overrides de ambiente ficam restritos à operação e são documentados à parte.
- Configuração desconhecida deve falhar de forma visível quando o schema adotar
  validação estrita de campos extras.
