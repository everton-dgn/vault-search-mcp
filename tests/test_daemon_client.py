"""
Testes para o cliente do daemon de modelos.

Usa mocks para não depender de um daemon real rodando.
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
    """Testes para is_daemon_running()."""

    def test_daemon_running(self):
        """Retorna True se daemon responde como pronto."""
        with patch("urllib.request.urlopen") as urlopen:
            response = MagicMock()
            response.read.return_value = b'{"status":"ready","models_loaded":true}'
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=None)
            urlopen.return_value = response
            assert is_daemon_running("localhost", 9847) is True

    def test_daemon_not_running_connection_refused(self):
        """Retorna False se conexão for recusada."""
        with patch("urllib.request.urlopen") as urlopen:
            urlopen.side_effect = urllib.error.URLError("connection refused")
            assert is_daemon_running("localhost", 9847) is False

    def test_daemon_not_running_timeout(self):
        """Retorna False se ocorrer timeout."""
        with patch("urllib.request.urlopen") as urlopen:
            urlopen.side_effect = TimeoutError()
            assert is_daemon_running("localhost", 9847) is False

    def test_daemon_not_running_os_error(self):
        """Retorna False se ocorrer erro de rede."""
        with patch("urllib.request.urlopen") as urlopen:
            urlopen.side_effect = OSError("network unreachable")
            assert is_daemon_running("localhost", 9847) is False


class TestDaemonClient:
    """Testes para DaemonClient."""

    @pytest.fixture
    def client(self):
        """Cria um cliente com configuração padrão."""
        return DaemonClient(host="localhost", port=9847, timeout=10.0)

    def test_init(self, client):
        """Inicialização configura atributos corretamente."""
        assert client.host == "localhost"
        assert client.port == 9847
        assert client.timeout == 10.0
        assert client.base_url == "http://localhost:9847"

    def test_is_available_cached(self, client):
        """is_available() cacheia resultado."""
        client._last_availability_check = time.monotonic()
        with patch.object(client, "_available", True):
            assert client.is_available() is True

    def test_is_available_checks_daemon(self, client):
        """is_available() verifica o health se o cache expirou."""
        with patch.object(
            client,
            "health",
            return_value={"status": "ready", "models_loaded": True},
        ) as health:
            assert client.is_available() is True
        health.assert_called_once_with()

    @patch("urllib.request.urlopen")
    def test_health(self, mock_urlopen, client):
        """health() retorna status do daemon."""
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

    @patch("urllib.request.urlopen")
    def test_embed_queries_empty(self, mock_urlopen, client):
        """embed_queries() com lista vazia retorna array vazio."""
        result = client.embed_queries([])
        assert len(result) == 0
        mock_urlopen.assert_not_called()

    @patch("urllib.request.urlopen")
    def test_embed_queries(self, mock_urlopen, client):
        """embed_queries() retorna embeddings."""
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

    @patch("urllib.request.urlopen")
    def test_embed_corpus_empty(self, mock_urlopen, client):
        """embed_corpus() com lista vazia retorna array vazio."""
        result = client.embed_corpus([])
        assert len(result) == 0
        mock_urlopen.assert_not_called()

    @patch("urllib.request.urlopen")
    def test_embed_corpus(self, mock_urlopen, client):
        """embed_corpus() retorna embeddings."""
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

    @patch("urllib.request.urlopen")
    def test_rerank_empty(self, mock_urlopen, client):
        """rerank() com lista vazia retorna lista vazia."""
        result = client.rerank("query", [])
        assert result == []
        mock_urlopen.assert_not_called()

    @patch("urllib.request.urlopen")
    def test_rerank(self, mock_urlopen, client):
        """rerank() retorna scores ordenados."""
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

    @patch("urllib.request.urlopen")
    def test_is_loaded_true(self, mock_urlopen, client):
        """is_loaded() retorna True se modelos carregados."""
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

    @patch("urllib.request.urlopen")
    def test_is_loaded_exception(self, mock_urlopen, client):
        """is_loaded() retorna False em caso de exceção."""
        mock_urlopen.side_effect = Exception("Connection error")
        assert client.is_loaded() is False

    def test_cleanup_does_nothing(self, client):
        """cleanup() não faz nada (modelos estão no daemon)."""
        client.cleanup()  # Não deve lançar exceção


class TestDaemonClientErrors:
    """Testes de tratamento de erros do DaemonClient."""

    @pytest.fixture
    def client(self):
        return DaemonClient()

    @patch("urllib.request.urlopen")
    def test_http_error_with_json_body(self, mock_urlopen, client):
        """HTTPError com body JSON é tratado."""
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

    @patch("urllib.request.urlopen")
    def test_http_error_with_plain_body(self, mock_urlopen, client):
        """HTTPError com body plain text é tratado."""
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

    @patch("urllib.request.urlopen")
    def test_url_error(self, mock_urlopen, client):
        """URLError é convertido em ConnectionError."""
        import urllib.error

        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        with pytest.raises(ConnectionError, match="conectar ao daemon"):
            client.health()


class TestHybridModelManager:
    """Testes para HybridModelManager."""

    @pytest.fixture
    def manager(self):
        """Cria um manager com configuração padrão."""
        return HybridModelManager(host="localhost", port=9847)

    def test_init(self, manager):
        """Inicialização configura atributos."""
        assert manager._daemon_client is not None
        assert manager._local_models is None
        assert manager._use_daemon is None

    @patch.object(DaemonClient, "is_available")
    def test_get_backend_uses_daemon_when_available(self, mock_available, manager):
        """Usa daemon quando disponível."""
        mock_available.return_value = True
        backend = manager._get_backend()
        assert backend is manager._daemon_client
        assert manager.using_daemon is True

    @patch.object(DaemonClient, "is_available")
    @patch("vault_search.core.models.ModelManager")
    def test_get_backend_uses_local_when_daemon_unavailable(
        self, mock_model_manager, mock_available, manager
    ):
        """Usa modelo local quando daemon não disponível."""
        mock_available.return_value = False
        mock_local = MagicMock()
        mock_model_manager.return_value = mock_local

        backend = manager._get_backend()
        assert backend is mock_local
        assert manager.using_daemon is False

    @patch.object(DaemonClient, "is_available")
    @patch.object(DaemonClient, "embed_queries")
    def test_embed_queries_delegates_to_daemon(self, mock_embed, mock_available, manager):
        """embed_queries() delega para daemon."""
        mock_available.return_value = True
        mock_embed.return_value = np.array([[0.1, 0.2]])

        result = manager.embed_queries(["query"])
        mock_embed.assert_called_once_with(["query"])
        assert result.shape == (1, 2)

    @patch.object(DaemonClient, "is_available")
    @patch.object(DaemonClient, "rerank")
    def test_rerank_delegates_to_daemon(self, mock_rerank, mock_available, manager):
        """rerank() delega para daemon."""
        mock_available.return_value = True
        mock_rerank.return_value = [(0, 0.9)]

        result = manager.rerank("query", ["text"])
        mock_rerank.assert_called_once_with("query", ["text"], top_k=10)
        assert result == [(0, 0.9)]

    @patch.object(DaemonClient, "is_available")
    @patch.object(DaemonClient, "is_loaded")
    def test_is_loaded_delegates(self, mock_loaded, mock_available, manager):
        """is_loaded() delega para backend."""
        mock_available.return_value = True
        mock_loaded.return_value = True

        assert manager.is_loaded() is True

    @patch.object(DaemonClient, "is_available")
    @patch.object(DaemonClient, "warmup")
    def test_warmup_delegates(self, mock_warmup, mock_available, manager):
        """warmup() delega para backend."""
        mock_available.return_value = True

        manager.warmup()
        mock_warmup.assert_called_once()

    def test_using_daemon_initially_none(self, manager):
        """using_daemon é False antes de determinar backend."""
        assert manager.using_daemon is False  # None is not True


class TestDaemonClientWarmup:
    """Testes de warmup do DaemonClient."""

    @pytest.fixture
    def client(self):
        return DaemonClient()

    @patch.object(DaemonClient, "is_available")
    def test_warmup_raises_if_not_available(self, mock_available, client):
        """warmup() levanta erro se daemon não disponível."""
        mock_available.return_value = False

        with pytest.raises(ConnectionError, match="não está pronto"):
            client.warmup()

    @patch.object(DaemonClient, "is_available")
    @patch.object(DaemonClient, "is_loaded")
    def test_warmup_warns_if_not_loaded(self, mock_loaded, mock_available, client):
        """warmup() avisa se modelos não carregados."""
        mock_available.return_value = True
        mock_loaded.return_value = False

        # Não deve lançar exceção, apenas avisar
        client.warmup()
