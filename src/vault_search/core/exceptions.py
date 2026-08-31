"""
Exceções de domínio para core do vault-search.
"""


class DaemonRequiredError(RuntimeError):
    """
    Erro levantado quando o daemon é obrigatório e não está disponível.

    Usado para diferenciar erro de configuração (não retryable) de falhas
    transientes de inferência (retryable).
    """
