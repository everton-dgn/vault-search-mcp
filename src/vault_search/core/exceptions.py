"""
Domain exceptions for the vault-search core.
"""


class DaemonRequiredError(RuntimeError):
    """
    Raised when the daemon is required but unavailable.

    Distinguishes a non-retryable configuration error from transient,
    retryable inference failures.
    """
