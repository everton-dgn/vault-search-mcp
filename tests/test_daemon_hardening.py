"""Regressões para saúde, segurança e failover do daemon."""

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from vault_search.core.models import ModelManager
from vault_search.daemon import client as daemon_client_module
from vault_search.daemon.client import DaemonClient, is_daemon_running
from vault_search.daemon.server import (
    DaemonRequestHandler,
    DaemonServer,
    RequestValidationError,
)


def _http_response(payload: bytes) -> MagicMock:
    response = MagicMock()
    response.read.return_value = payload
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=None)
    return response


class TestDaemonReadiness:
    @patch("urllib.request.urlopen")
    def test_open_port_is_not_enough_without_ready_health(self, urlopen):
        urlopen.return_value = _http_response(b'{"status":"starting","models_loaded":false}')

        assert is_daemon_running("127.0.0.1", 9847, retries=1) is False

    @patch("urllib.request.urlopen")
    def test_ready_health_is_available(self, urlopen):
        urlopen.return_value = _http_response(b'{"status":"ready","models_loaded":true}')

        assert is_daemon_running("127.0.0.1", 9847, retries=1) is True

    def test_client_can_force_revalidation(self):
        client = DaemonClient(host="127.0.0.1", port=9847)
        client._available = True
        client._last_availability_check = 0.0

        with patch.object(
            client,
            "health",
            return_value={"status": "failed", "models_loaded": False},
        ):
            assert client.is_available(force=True) is False

    @pytest.mark.parametrize("host", ["192.0.2.10", "example.test", "0.0.0.0"])
    def test_client_rejects_remote_host(self, host):
        with pytest.raises(ValueError, match="loopback"):
            DaemonClient(host=host, port=9847)

    def test_client_formats_ipv6_loopback_url(self):
        client = DaemonClient(host="::1", port=9847)

        assert client.base_url == "http://[::1]:9847"


class TestDaemonHealthStates:
    @staticmethod
    def _server(warmup_result: dict, loaded: dict[str, bool]) -> DaemonServer:
        server = DaemonServer(enable_watcher=False)
        server.models = SimpleNamespace(
            warmup=lambda: warmup_result,
            is_loaded=lambda: loaded,
        )
        return server

    def test_successful_warmup_marks_daemon_ready(self):
        server = self._server(
            {"embed_ms": 10.0, "rerank_ms": 5.0},
            {"embed_model": True, "reranker_model": True},
        )

        server._initialize_runtime()

        assert server.health_state == "ready"

    def test_partial_warmup_marks_daemon_degraded(self):
        server = self._server(
            {"embed_ms": 10.0, "rerank_error": "unavailable"},
            {"embed_model": True, "reranker_model": False},
        )

        server._initialize_runtime()

        assert server.health_state == "degraded"

    def test_failed_warmup_marks_daemon_failed(self):
        server = self._server(
            {"embed_error": "unavailable", "rerank_error": "unavailable"},
            {"embed_model": False, "reranker_model": False},
        )

        server._initialize_runtime()

        assert server.health_state == "failed"

    @pytest.mark.parametrize(
        ("state", "expected_status"),
        [("starting", 503), ("degraded", 503), ("failed", 503), ("ready", 200)],
    )
    def test_health_http_status_tracks_readiness(self, state, expected_status):
        server = self._server(
            {},
            {"embed_model": state == "ready", "reranker_model": state == "ready"},
        )
        server._set_health_state(state)
        handler = object.__new__(DaemonRequestHandler)
        handler.daemon_server = server
        handler._send_json = MagicMock()

        handler._handle_health()

        snapshot, status = handler._send_json.call_args.args
        assert snapshot["status"] == state
        assert status == expected_status

    def test_ready_state_is_downgraded_when_models_are_unloaded(self):
        server = self._server(
            {},
            {"embed_model": False, "reranker_model": False},
        )
        server._set_health_state("ready")
        handler = object.__new__(DaemonRequestHandler)
        handler.daemon_server = server
        handler._send_json = MagicMock()

        handler._handle_health()

        snapshot, status = handler._send_json.call_args.args
        assert snapshot["status"] == "degraded"
        assert snapshot["warmup_errors"] == ["models_unloaded"]
        assert status == 503


class TestDaemonRequestLimits:
    @patch("urllib.request.urlopen")
    def test_client_rejects_response_above_memory_limit(self, urlopen, monkeypatch):
        monkeypatch.setattr(daemon_client_module, "MAX_DAEMON_RESPONSE_BYTES", 16)
        urlopen.return_value = _http_response(b'{"payload":"0123456789"}')

        with pytest.raises(RuntimeError, match="resposta inválida"):
            DaemonClient(host="127.0.0.1", port=9847).health()

        urlopen.return_value.read.assert_called_once_with(17)

    def test_access_log_does_not_include_request_target(self):
        handler = object.__new__(DaemonRequestHandler)

        with patch("vault_search.daemon.server.logger") as safe_logger:
            handler.log_message(
                '"%s" %s %s',
                "GET /private?token=synthetic-secret HTTP/1.1",
                "200",
                "12",
            )

        rendered_args = repr(safe_logger.debug.call_args)
        assert "private" not in rendered_args
        assert "synthetic-secret" not in rendered_args

    def test_rejects_body_above_content_length_limit(self):
        handler = object.__new__(DaemonRequestHandler)
        handler.headers = {
            "Content-Length": "11",
            "Content-Type": "application/json",
        }
        handler.rfile = BytesIO(b'{"x":"123"}')
        handler.daemon_server = SimpleNamespace(max_request_bytes=10)

        with pytest.raises(RequestValidationError) as error:
            handler._read_json()

        assert error.value.status == 413

    def test_rejects_non_string_texts(self):
        server = DaemonServer(enable_watcher=False)

        with pytest.raises(RequestValidationError, match="texts"):
            server.validate_texts(["valid", 42])

    def test_rejects_invalid_top_k(self):
        server = DaemonServer(enable_watcher=False)

        with pytest.raises(RequestValidationError, match="top_k"):
            server.validate_rerank("query", ["document"], 0)


class TestDaemonShutdownBoundary:
    @pytest.mark.parametrize("host", ["0.0.0.0", "192.0.2.10", "example.test"])
    def test_remote_binding_is_always_rejected(self, host):
        with pytest.raises(ValueError, match="loopback"):
            DaemonServer(host=host, enable_watcher=False)


class TestModelManagerFailover:
    def setup_method(self):
        ModelManager._instance = None

    def test_failed_daemon_call_invalidates_and_falls_back_locally(self, monkeypatch):
        monkeypatch.delenv("VAULT_SEARCH_REQUIRE_DAEMON", raising=False)
        manager = ModelManager()
        manager._daemon_client = MagicMock()
        manager._daemon_client.embed_queries.side_effect = ConnectionError("offline")
        local_model = MagicMock()
        local_model.max_seq_length = 8192
        local_model.encode.return_value = [[0.25, 0.5]]

        with patch.object(manager, "_check_daemon", return_value=True):
            with patch.object(manager, "_get_embed_model", return_value=local_model):
                assert manager.embed_queries(["query"]) == [[0.25, 0.5]]

        assert manager._use_daemon is False

    def test_failed_daemon_call_does_not_fallback_when_required(self, monkeypatch):
        monkeypatch.setenv("VAULT_SEARCH_REQUIRE_DAEMON", "1")
        monkeypatch.delenv("VAULT_SEARCH_RUNNING_AS_DAEMON", raising=False)
        manager = ModelManager()
        manager._daemon_client = MagicMock()
        manager._daemon_client.embed_queries.side_effect = ConnectionError("offline")

        with patch.object(manager, "_check_daemon", return_value=True):
            with patch.object(manager, "_get_embed_model") as local_model:
                with pytest.raises(RuntimeError, match="fallback local desabilitado"):
                    manager.embed_queries(["query"])

        local_model.assert_not_called()


class TestMCPStartupOrdering:
    def test_data_services_share_one_deterministic_sequence(self, tmp_path, monkeypatch):
        import vault_search.server.mcp as mcp_server

        events: list[str] = []
        monkeypatch.setattr(mcp_server, "DATA_DIR", tmp_path / "index")
        monkeypatch.setattr(
            mcp_server,
            "_init_catalog",
            lambda: (events.append("catalog"), True)[1],
        )
        monkeypatch.setattr(mcp_server, "_init_sync_check", lambda: events.append("sync"))
        monkeypatch.setattr(mcp_server, "_init_prewarm", lambda: events.append("prewarm"))
        monkeypatch.setattr(
            mcp_server,
            "_start_catalog_reconciliation",
            lambda: events.append("reconciliation"),
        )
        monkeypatch.setattr(
            mcp_server,
            "_watcher",
            SimpleNamespace(start=lambda: events.append("watcher")),
        )

        mcp_server._init_data_services()

        assert (tmp_path / "index").is_dir()
        assert events == ["catalog", "sync", "prewarm", "reconciliation", "watcher"]

    def test_import_does_not_force_daemon_requirement(self, monkeypatch):
        monkeypatch.delenv("VAULT_SEARCH_REQUIRE_DAEMON", raising=False)

        import vault_search.server.mcp  # noqa: F401

        assert "VAULT_SEARCH_REQUIRE_DAEMON" not in __import__("os").environ
