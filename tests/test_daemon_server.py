"""
Testes para o servidor daemon HTTP.
"""

from errno import EADDRINUSE

from vault_search.daemon.server import DaemonServer


def test_start_returns_cleanly_when_port_is_in_use(monkeypatch):
    """Daemon deve encerrar sem exceção quando porta já está em uso."""
    server = DaemonServer(enable_watcher=False)

    warmup_calls = {"count": 0}

    class DummyModels:
        def warmup(self):
            warmup_calls["count"] += 1

    def raise_addr_in_use(*_args, **_kwargs):
        raise OSError(EADDRINUSE, "Address already in use")

    # Não registrar handlers reais de sinal durante teste.
    monkeypatch.setattr(server, "_setup_signal_handlers", lambda: None)
    monkeypatch.setattr("vault_search.daemon.server.HTTPServer", raise_addr_in_use)
    server.models = DummyModels()

    server.start(blocking=False)

    assert warmup_calls["count"] == 0
    assert server._server is None
