"""
Tests for the model daemon client.

Uses mocks instead of depending on a running daemon.
"""

import json
import time
import urllib.error
from unittest.mock import MagicMock, Mock, patch

import numpy as np
import pytest

from vault_search.daemon.client import (
    DaemonClient,
    HybridModelManager,
    is_daemon_running,
)


class TestIsDaemonRunning:
    """Tests for is_daemon_running()."""

    def test_daemon_running(self):
        """Return True when the daemon reports ready."""
        with patch("vault_search.daemon.client._open_loopback") as urlopen:
            response = MagicMock()
            response.read.return_value = b'{"status":"ready","models_loaded":true}'
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=None)
            urlopen.return_value = response
            assert is_daemon_running("localhost", 9847) is True

    def test_daemon_not_running_connection_refused(self):
        """Return False when the connection is refused."""
        with patch("vault_search.daemon.client._open_loopback") as urlopen:
            urlopen.side_effect = urllib.error.URLError("connection refused")
            assert is_daemon_running("localhost", 9847) is False

    def test_daemon_not_running_timeout(self):
        """Return False when the health request times out."""
        with patch("vault_search.daemon.client._open_loopback") as urlopen:
            urlopen.side_effect = TimeoutError()
            assert is_daemon_running("localhost", 9847) is False

    def test_daemon_not_running_os_error(self):
        """Return False when a network error occurs."""
        with patch("vault_search.daemon.client._open_loopback") as urlopen:
            urlopen.side_effect = OSError("network unreachable")
            assert is_daemon_running("localhost", 9847) is False


class TestDaemonClient:
    """Tests for DaemonClient."""

    @pytest.fixture
    def client(self):
        """Create a client with default configuration."""
        return DaemonClient(host="localhost", port=9847, timeout=10.0)

    def test_init(self, client):
        """Initialization configures attributes correctly."""
        assert client.host == "localhost"
        assert client.port == 9847
        assert client.timeout == 10.0
        assert client.base_url == "http://localhost:9847"

    def test_is_available_cached(self, client):
        """is_available() caches its result."""
        client._last_availability_check = time.monotonic()
        with patch.object(client, "_available", True):
            assert client.is_available() is True

    def test_is_available_checks_daemon(self, client):
        """is_available() checks the health if the cache expired."""
        with patch.object(
            client,
            "health",
            return_value={"status": "ready", "models_loaded": True},
        ) as health:
            assert client.is_available() is True
        health.assert_called_once_with()

    @patch("vault_search.daemon.client._open_loopback")
    def test_health(self, mock_urlopen, client):
        """health() returns status of the daemon."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "status": "healthy",
                "models_loaded": True,
            }
        ).encode()
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=None)
        mock_urlopen.return_value = mock_response

        result = client.health()
        assert result["status"] == "healthy"
        assert result["models_loaded"] is True

    @patch("vault_search.daemon.client._open_loopback")
    def test_embed_queries_empty(self, mock_urlopen, client):
        """embed_queries() returns an empty array for empty input."""
        result = client.embed_queries([])
        assert len(result) == 0
        mock_urlopen.assert_not_called()

    @patch("vault_search.daemon.client._open_loopback")
    def test_embed_queries(self, mock_urlopen, client):
        """embed_queries() returns embeddings."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "embeddings": [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]],
            }
        ).encode()
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=None)
        mock_urlopen.return_value = mock_response

        result = client.embed_queries(["query1", "query2"])
        assert isinstance(result, np.ndarray)
        assert result.shape == (2, 3)
        assert result.dtype == np.float32

    @patch("vault_search.daemon.client._open_loopback")
    def test_embed_corpus_empty(self, mock_urlopen, client):
        """embed_corpus() returns an empty array for empty input."""
        result = client.embed_corpus([])
        assert len(result) == 0
        mock_urlopen.assert_not_called()

    @patch("vault_search.daemon.client._open_loopback")
    def test_embed_corpus(self, mock_urlopen, client):
        """embed_corpus() returns embeddings."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "embeddings": [[0.1, 0.2], [0.3, 0.4]],
            }
        ).encode()
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=None)
        mock_urlopen.return_value = mock_response

        result = client.embed_corpus(["text1", "text2"])
        assert isinstance(result, np.ndarray)
        assert result.shape == (2, 2)

    @patch("vault_search.daemon.client._open_loopback")
    def test_rerank_empty(self, mock_urlopen, client):
        """rerank() returns an empty list for empty input."""
        result = client.rerank("query", [])
        assert result == []
        mock_urlopen.assert_not_called()

    @patch("vault_search.daemon.client._open_loopback")
    def test_rerank(self, mock_urlopen, client):
        """rerank() returns ordered scores."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "scores": [[1, 0.9], [0, 0.8], [2, 0.7]],
            }
        ).encode()
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=None)
        mock_urlopen.return_value = mock_response

        result = client.rerank("query", ["text1", "text2", "text3"], top_k=3)
        assert result == [(1, 0.9), (0, 0.8), (2, 0.7)]

    @patch("vault_search.daemon.client._open_loopback")
    def test_is_loaded_true(self, mock_urlopen, client):
        """is_loaded() returns True when models are loaded."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "models_loaded": True,
            }
        ).encode()
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=None)
        mock_urlopen.return_value = mock_response

        assert client.is_loaded() is True

    @patch("vault_search.daemon.client._open_loopback")
    def test_is_loaded_exception(self, mock_urlopen, client):
        """is_loaded() returns False in case of exception."""
        mock_urlopen.side_effect = Exception("Connection error")
        assert client.is_loaded() is False

    def test_cleanup_does_nothing(self, client):
        """cleanup() does not nothing (models are in the daemon)."""
        client.cleanup()  # Must not raise exception


class TestDaemonClientErrors:
    """Tests for DaemonClient error handling."""

    @pytest.fixture
    def client(self):
        return DaemonClient()

    @patch("vault_search.daemon.client._open_loopback")
    def test_http_error_with_json_body(self, mock_urlopen, client):
        """HTTPError with body JSON is handled."""
        import urllib.error

        error = urllib.error.HTTPError(
            "http://localhost:9847/health",
            500,
            "Internal Server Error",
            {},
            None,
        )
        error.read = Mock(return_value=json.dumps({"error": "Model not loaded"}).encode())
        mock_urlopen.side_effect = error

        with pytest.raises(RuntimeError, match="HTTP 500"):
            client.health()

    @patch("vault_search.daemon.client._open_loopback")
    def test_http_error_with_plain_body(self, mock_urlopen, client):
        """HTTPError with body plain text is handled."""
        import urllib.error

        error = urllib.error.HTTPError(
            "http://localhost:9847/health",
            500,
            "Internal Server Error",
            {},
            None,
        )
        error.read = Mock(return_value=b"Plain text error")
        mock_urlopen.side_effect = error

        with pytest.raises(RuntimeError, match="HTTP 500"):
            client.health()

    @patch("vault_search.daemon.client._open_loopback")
    def test_url_error(self, mock_urlopen, client):
        """URLError is converted in ConnectionError."""
        import urllib.error

        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        with pytest.raises(ConnectionError, match="connect to the daemon"):
            client.health()


class TestHybridModelManager:
    """Tests for HybridModelManager."""

    @pytest.fixture
    def manager(self):
        """Creates a manager with configuration default."""
        return HybridModelManager(host="localhost", port=9847)

    def test_init(self, manager):
        """Initialization configures attributes."""
        assert manager._daemon_client is not None
        assert manager._local_models is None
        assert manager._use_daemon is None

    @patch.object(DaemonClient, "is_available")
    def test_get_backend_uses_daemon_when_available(self, mock_available, manager):
        """Uses daemon when available."""
        mock_available.return_value = True
        backend = manager._get_backend()
        assert backend is manager._daemon_client
        assert manager.using_daemon is True

    @patch.object(DaemonClient, "is_available")
    @patch("vault_search.core.models.ModelManager")
    def test_get_backend_uses_local_when_daemon_unavailable(
        self, mock_model_manager, mock_available, manager
    ):
        """Use the local model when the daemon is unavailable."""
        mock_available.return_value = False
        mock_local = MagicMock()
        mock_model_manager.return_value = mock_local

        backend = manager._get_backend()
        assert backend is mock_local
        assert manager.using_daemon is False

    @patch.object(DaemonClient, "is_available")
    @patch.object(DaemonClient, "embed_queries")
    def test_embed_queries_delegates_to_daemon(self, mock_embed, mock_available, manager):
        """embed_queries() delegates to the daemon."""
        mock_available.return_value = True
        mock_embed.return_value = np.array([[0.1, 0.2]])

        result = manager.embed_queries(["query"])
        mock_embed.assert_called_once_with(["query"])
        assert result.shape == (1, 2)

    @patch.object(DaemonClient, "is_available")
    @patch.object(DaemonClient, "rerank")
    def test_rerank_delegates_to_daemon(self, mock_rerank, mock_available, manager):
        """rerank() delegates to the daemon."""
        mock_available.return_value = True
        mock_rerank.return_value = [(0, 0.9)]

        result = manager.rerank("query", ["text"])
        mock_rerank.assert_called_once_with("query", ["text"], top_k=10)
        assert result == [(0, 0.9)]

    @patch.object(DaemonClient, "is_available")
    @patch.object(DaemonClient, "is_loaded")
    def test_is_loaded_delegates(self, mock_loaded, mock_available, manager):
        """is_loaded() delegates to the backend."""
        mock_available.return_value = True
        mock_loaded.return_value = True

        assert manager.is_loaded() is True

    @patch.object(DaemonClient, "is_available")
    @patch.object(DaemonClient, "warmup")
    def test_warmup_delegates(self, mock_warmup, mock_available, manager):
        """warmup() delegates to the backend."""
        mock_available.return_value = True

        manager.warmup()
        mock_warmup.assert_called_once()

    def test_using_daemon_initially_none(self, manager):
        """using_daemon is False before backend selection."""
        assert manager.using_daemon is False  # None is not True


class TestDaemonClientWarmup:
    """Tests for warmup of the DaemonClient."""

    @pytest.fixture
    def client(self):
        return DaemonClient()

    @patch.object(DaemonClient, "is_available")
    def test_warmup_raises_if_not_available(self, mock_available, client):
        """warmup() raises when the daemon is unavailable."""
        mock_available.return_value = False

        with pytest.raises(ConnectionError, match="not ready"):
            client.warmup()

    @patch.object(DaemonClient, "is_available")
    @patch.object(DaemonClient, "is_loaded")
    def test_warmup_warns_if_not_loaded(self, mock_loaded, mock_available, client):
        """warmup() warns when models are not loaded."""
        mock_available.return_value = True
        mock_loaded.return_value = False

        # Warn without raising an exception.
        client.warmup()
