# ADR-0002: daemon local de modelos

## Status

Aceito.

## Contexto

Clientes MCP podem iniciar processos curtos. Recarregar modelos a cada processo
repete custo de inicialização e uso de memória.

## Decisão

Oferecer um daemon HTTP opcional, limitado a loopback, que mantém embedding e
reranking residentes. O processo MCP continua responsável pelo protocolo, pelo
vault e pelos índices.

## Consequências

- Vários clientes locais podem reutilizar modelos.
- O cliente precisa verificar identidade e saúde semântica do daemon.
- Falhas e reinícios do daemon fazem parte do caminho normal de recuperação.
- Bind fora de loopback permanece fora do contrato suportado enquanto não
  houver TLS, autenticação, quotas e um modelo de ameaças para acesso remoto.
