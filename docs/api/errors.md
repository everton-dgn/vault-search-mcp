# Erros da API

A versão 0.1 usa três formas de falha pública. Clientes precisam aceitar as
três até que um envelope único seja adotado como contrato estável.

## Formas atuais

### Mensagem sanitizada

Exceções capturadas por uma tool retornam:

```text
Erro [internal_error]: A operação não pôde ser concluída. Referência: a1b2c3d4.
```

A referência tem oito caracteres hexadecimais e muda a cada falha. Códigos
específicos em uso incluem `invalid_request`, `search_unavailable`,
`daemon_unavailable` e `internal_error`.

O texto público não inclui tipo da exceção, stack trace, path absoluto, query ou
conteúdo da nota. O log local registra operação, referência e tipo da exceção
para correlação.

### Mensagem de validação direta

Guardrails simples podem retornar uma string antes de iniciar a operação:

```text
Erro: query não pode ser vazia.
```

Outros exemplos cobrem path ou pasta vazia, extensão inválida e seleção de
argumentos mutuamente exclusivos. O texto pode mudar durante a fase alpha.

### Resultado de operação

CRUD usa um objeto mesmo quando a operação de domínio falha:

```python
{
    "success": False,
    "message": "Tempo limite ao aguardar outra escrita. Tente novamente.",
    "path": "notes/example.md",
    "error_code": "write_lock_timeout",
}
```

O path é o valor relativo fornecido pelo cliente. Falhas inesperadas no wrapper
continuam usando a mensagem sanitizada.

Mutações CRUD podem retornar dois códigos recuperáveis:

| `error_code` | Significado | Próxima ação |
|---|---|---|
| `write_lock_timeout` | Outro escritor cooperativo reteve o lock além do prazo | Relê a nota e tenta novamente se o efeito ainda for necessário |
| `write_conflict` | A revisão do arquivo mudou durante a operação | Relê a versão atual antes de produzir novo conteúdo |

## Resources

Resources convertem exceções para um envelope:

```python
{
    "error": "Erro [internal_error]: A operação não pôde ser concluída. Referência: a1b2c3d4.",
    "code": "internal_error",
}
```

Validações focais de `vault://notes/{path*}` também podem devolver `error` e
`code` sem referência, por exemplo `invalid_path` ou `not_found`.

## Reindexação incremental

`reindex_note` descreve falhas esperadas pelo campo `status`, em vez de lançar
exceção para o cliente:

| Status | Significado |
|---|---|
| `updated` | Chunks da nota foram substituídos |
| `empty` | Arquivo válido sem conteúdo indexável |
| `deleted` | Arquivo ausente e registros removidos |
| `parse_error` | Parser falhou; versão anterior é preservada quando possível |
| `error_add_failed` | Escrita no índice falhou |
| `rejected_path_traversal` | Path saiu da raiz permitida |
| `rejected_extension` | Extensão fora da configuração |
| `circuit_breaker_open` | Índice suspendeu novas escritas após falhas repetidas |

Campos opcionais podem informar links, aliases, ID, enriquecimento e
compactação automática.

## Estratégia de cliente

Um cliente tolerante à versão alpha pode normalizar a resposta assim:

```python
def failure_message(result: object) -> str | None:
    if isinstance(result, str) and result.startswith("Erro"):
        return result
    if isinstance(result, dict):
        if result.get("success") is False:
            return str(result.get("message", "Falha de operação"))
        if "error" in result:
            return str(result["error"])
    return None
```

Não analise o texto para decidir se uma tentativa é segura. Use `code`,
`error_code`, `status` e `success` quando existirem. Confira a versão atual da
nota antes de repetir qualquer mutação.

## Privacidade no diagnóstico

Ao abrir uma issue:

1. informe o código e a referência;
2. descreva a operação e a versão;
3. substitua paths por nomes sintéticos;
4. não anexe configuração local, vault, índice ou log completo;
5. envie detalhes de vulnerabilidade pelo fluxo de [segurança](../../SECURITY.md).

O guia de [solução de problemas](../operation/troubleshooting.md) mostra checks
locais que evitam publicar dados da máquina.
