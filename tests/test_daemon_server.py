"""
Tests for the server daemon HTTP.
"""

from errno import EADDRINUSE

import pytest

from vault_search.daemon.server import DaemonServer


def test_start_fails_when_port_is_in_use(monkeypatch):
    """The daemon must fail before warmup when the port is already in use."""
    server = DaemonServer(enable_watcher=False)

    warmup_calls = {"count": 0}

    class DummyModels:
        def warmup(self):
            warmup_calls["count"] += 1

    def raise_addr_in_use(*_args, **_kwargs):
        raise OSError(EADDRINUSE, "Address already in use")

    # Do not register real signal handlers during the test.
    monkeypatch.setattr(server, "_setup_signal_handlers", lambda: None)
    monkeypatch.setattr(
        "vault_search.daemon.server._server_class_for_host",
        lambda host: raise_addr_in_use,
    )
    server.models = DummyModels()

    with pytest.raises(RuntimeError, match="already in use"):
        server.start(blocking=False)

    assert warmup_calls["count"] == 0
    assert server._server is None
