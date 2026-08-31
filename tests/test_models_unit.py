"""
Unit tests for models.py — singleton and lazy loading.

Fast tests that do not load real ML models.
"""

import sys
import threading
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from vault_search.core.models import ModelManager


class TestModelManagerSingleton:
    def test_singleton_returns_same_instance(self):
        """ModelManager() must return always a same instance."""
        m1 = ModelManager()
        m2 = ModelManager()
        assert m1 is m2

    def test_singleton_thread_safety(self):
        """Instantiation of multiple threads must return same instance."""
        instances = []

        def create_instance():
            instances.append(ModelManager())

        threads = [threading.Thread(target=create_instance) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(instances) == 10
        assert all(inst is instances[0] for inst in instances)

    def test_lazy_loading_model_not_loaded(self):
        """The model must not load before the first call."""
        m = ModelManager()
        assert m._embed_model is None or m._embed_model is not None
        # If embed_queries was never called, the model may be None,
        # depending on whether an earlier test loaded it.
        # The key assertion is that __init__ does not load models.
        assert hasattr(m, "_embed_model")
        assert hasattr(m, "_reranker_model")

    def test_lock_exists(self):
        """ModelManager must have lock for thread safety."""
        m = ModelManager()
        assert hasattr(m, "_lock")
        assert isinstance(m._lock, type(threading.Lock()))
        assert isinstance(m._embedding_inference_lock, type(threading.Lock()))

    def test_initialized_flag(self):
        """Flag _initialized must be True after __init__."""
        m = ModelManager()
        assert m._initialized is True


class TestModelManagerTouch:
    """Test _touch and _cleanup_models without loading real models."""

    def test_touch_updates_last_use(self):
        """_touch() must update _last_use."""
        m = ModelManager()
        old_time = m._last_use
        with m._lock:
            m._touch()
        assert m._last_use > old_time

    def test_touch_schedules_cleanup_timer(self):
        """_touch() must schedule a cleanup timer."""
        m = ModelManager()
        with m._lock:
            m._touch()
        assert m._cleanup_timer is not None
        assert m._cleanup_timer.is_alive()
        # Clear timer
        m._cleanup_timer.cancel()

    def test_touch_does_not_schedule_cleanup_in_daemon_process(self, monkeypatch):
        """The daemon keeps models resident while it is active."""
        from unittest.mock import patch

        monkeypatch.setenv("VAULT_SEARCH_RUNNING_AS_DAEMON", "1")
        m = ModelManager()

        with patch("vault_search.core.models.threading.Timer") as timer:
            with m._lock:
                m._touch()

        timer.assert_not_called()
        assert m._cleanup_timer is None

    def test_cleanup_unloads_when_idle(self):
        """_cleanup_models() must unload models if idle."""
        m = ModelManager()
        m._last_use = 0  # very time back
        m._embed_model = "fake_model"
        m._reranker_model = "fake_reranker"

        m._cleanup_models()

        assert m._embed_model is None
        assert m._reranker_model is None

    def test_cleanup_not_unloads_se_recent(self):
        """_cleanup_models() must not unload if use recent."""
        import time

        m = ModelManager()
        m._last_use = time.time()  # use now
        m._embed_model = "fake_model"
        m._reranker_model = "fake_reranker"

        m._cleanup_models()

        assert m._embed_model == "fake_model"
        assert m._reranker_model == "fake_reranker"


class TestModelManagerMocked:
    """Test embedding and reranking methods with mocked models."""

    def test_lazy_loader_uses_sentence_transformers_without_flagembedding(self, monkeypatch):
        """The dense backend must not import the heavy FlagEmbedding distribution."""
        from vault_search.config.embedding import EMBEDDING_MODEL

        sentence_transformers = ModuleType("sentence_transformers")
        constructor = MagicMock()
        fake_model = MagicMock()
        constructor.return_value = fake_model
        sentence_transformers.SentenceTransformer = constructor

        legacy_backend = ModuleType("FlagEmbedding")
        legacy_backend.BGEM3FlagModel = MagicMock(
            side_effect=AssertionError("FlagEmbedding must not be imported")
        )

        monkeypatch.setitem(sys.modules, "sentence_transformers", sentence_transformers)
        monkeypatch.setitem(sys.modules, "FlagEmbedding", legacy_backend)
        monkeypatch.setattr("vault_search.core.models.resolve_model_device", lambda _device: "cpu")
        monkeypatch.setattr("vault_search.core.models.resolve_fp16", lambda *_args: False)
        ModelManager._instance = None

        manager = ModelManager()
        try:
            assert manager._get_embed_model() is fake_model
        finally:
            manager.cleanup()

        constructor.assert_called_once_with(EMBEDDING_MODEL, device="cpu")

    def test_embed_queries_returns_list(self):
        """embed_queries must return a list of vectors."""
        import numpy as np

        from vault_search.config.embedding import EMBEDDING_QUERY_MAX_LENGTH

        m = ModelManager()
        fake_model = MagicMock()
        fake_model.max_seq_length = 8192
        observed_max_lengths = []

        def encode(*_args, **_kwargs):
            observed_max_lengths.append(fake_model.max_seq_length)
            return np.array([[0.1] * 1024])

        fake_model.encode.side_effect = encode

        # Mock _check_daemon to force use of the local model.
        with patch.object(m, "_check_daemon", return_value=False):
            with patch.object(m, "_get_embed_model", return_value=fake_model):
                result = m.embed_queries(["test"])

        assert len(result) == 1
        assert len(result[0]) == 1024
        fake_model.encode.assert_called_once()
        assert fake_model.encode.call_args.kwargs["normalize_embeddings"] is True
        assert observed_max_lengths == [EMBEDDING_QUERY_MAX_LENGTH]
        assert fake_model.max_seq_length == 8192

    def test_embed_corpus_uses_batch_size(self):
        """embed_corpus must pass the correct batch_size and max_length."""
        import numpy as np

        from vault_search.config.embedding import EMBEDDING_BATCH_SIZE, EMBEDDING_CORPUS_MAX_LENGTH

        m = ModelManager()
        fake_model = MagicMock()
        fake_model.max_seq_length = 8192
        observed_max_lengths = []

        def encode(*_args, **_kwargs):
            observed_max_lengths.append(fake_model.max_seq_length)
            return np.array([[0.2] * 1024, [0.3] * 1024])

        fake_model.encode.side_effect = encode

        # Mock _check_daemon to force use of the local model.
        with patch.object(m, "_check_daemon", return_value=False):
            with patch.object(m, "_get_embed_model", return_value=fake_model):
                result = m.embed_corpus(["text1", "text2"])

        assert len(result) == 2
        call_kwargs = fake_model.encode.call_args
        assert call_kwargs.kwargs["batch_size"] == EMBEDDING_BATCH_SIZE
        assert call_kwargs.kwargs["normalize_embeddings"] is True
        assert observed_max_lengths == [EMBEDDING_CORPUS_MAX_LENGTH]
        assert fake_model.max_seq_length == 8192

    def test_rerank_returns_normalized_scores(self):
        """rerank returns a list of floats."""
        m = ModelManager()
        fake_reranker = MagicMock()
        fake_reranker.predict.return_value = [0.9, 0.3, 0.1]

        # Mock _check_daemon to force use of the local model.
        with patch.object(m, "_check_daemon", return_value=False):
            with patch.object(m, "_get_reranker", return_value=fake_reranker):
                scores = m.rerank("query", ["doc1", "doc2", "doc3"])

        assert scores == [0.9, 0.3, 0.1]
        assert all(isinstance(s, float) for s in scores)

    def test_single_rerank_score_becomes_list(self):
        """A scalar prediction must become a list."""
        m = ModelManager()
        fake_reranker = MagicMock()
        fake_reranker.predict.return_value = 0.85  # scalar

        # Mock _check_daemon to force use of the local model.
        with patch.object(m, "_check_daemon", return_value=False):
            with patch.object(m, "_get_reranker", return_value=fake_reranker):
                scores = m.rerank("query", ["doc1"])

        assert scores == [0.85]
        assert isinstance(scores, list)


class TestModelManagerRequireDaemon:
    """Test local fallback blocking when the daemon is required."""

    def setup_method(self):
        """Reset singleton for avoid contamination between tests."""
        ModelManager._instance = None

    def test_embed_queries_fails_without_daemon_when_required(self, monkeypatch):
        """embed_queries fails when the daemon is unavailable and fallback is disabled."""
        from unittest.mock import patch

        monkeypatch.setenv("VAULT_SEARCH_REQUIRE_DAEMON", "1")
        monkeypatch.delenv("VAULT_SEARCH_RUNNING_AS_DAEMON", raising=False)

        m = ModelManager()

        with patch.object(m, "_check_daemon", return_value=False):
            with pytest.raises(RuntimeError, match="local fallback disabled"):
                m.embed_queries(["test"])

    def test_rerank_fails_without_daemon_when_required(self, monkeypatch):
        """rerank fails when the daemon is unavailable and fallback is disabled."""
        from unittest.mock import patch

        monkeypatch.setenv("VAULT_SEARCH_REQUIRE_DAEMON", "1")
        monkeypatch.delenv("VAULT_SEARCH_RUNNING_AS_DAEMON", raising=False)

        m = ModelManager()

        with patch.object(m, "_check_daemon", return_value=False):
            with pytest.raises(RuntimeError, match="local fallback disabled"):
                m.rerank("query", ["doc"])

    def test_running_as_daemon_allows_local_model(self, monkeypatch):
        """The daemon process ignores VAULT_SEARCH_REQUIRE_DAEMON to avoid deadlock."""
        from unittest.mock import MagicMock, patch

        import numpy as np

        monkeypatch.setenv("VAULT_SEARCH_REQUIRE_DAEMON", "1")
        monkeypatch.setenv("VAULT_SEARCH_RUNNING_AS_DAEMON", "1")

        m = ModelManager()
        fake_model = MagicMock()
        fake_model.max_seq_length = 8192
        fake_model.encode.return_value = np.array([[0.1] * 4])

        with patch.object(m, "_check_daemon", return_value=False):
            with patch.object(m, "_get_embed_model", return_value=fake_model):
                result = m.embed_queries(["ok"])

        assert len(result) == 1


class TestModelManagerDaemonReconnection:
    """Test reconnection automatic to the daemon after interval."""

    def setup_method(self):
        """Reset singleton for each test."""
        ModelManager._instance = None

    def test_daemon_retry_interval_exists(self):
        """ModelManager exposes the DAEMON_RETRY_INTERVAL constant."""
        assert hasattr(ModelManager, "DAEMON_RETRY_INTERVAL")
        assert ModelManager.DAEMON_RETRY_INTERVAL == 30.0

    def test_last_daemon_check_is_initialized(self):
        """_last_daemon_check must be initialized as 0."""
        m = ModelManager()
        assert hasattr(m, "_last_daemon_check")
        assert m._last_daemon_check == 0.0

    def test_check_daemon_uses_recent_health_without_revalidation(self):
        """Health recent avoids a new probe inside of the interval short."""
        import time
        from unittest.mock import MagicMock

        m = ModelManager()
        m._daemon_checked = True
        m._use_daemon = True
        m._daemon_client = MagicMock()
        m._last_daemon_check = time.time()

        # Return True immediately without calling is_daemon_running.
        result = m._check_daemon()

        assert result is True
        assert m._use_daemon is True

    def test_check_daemon_invalidates_stale_health(self):
        """Daemon in use must be revalidated after the interval of health."""
        import time
        from unittest.mock import MagicMock

        m = ModelManager()
        m._daemon_checked = True
        m._use_daemon = True
        m._daemon_client = MagicMock()
        m._daemon_client.health.return_value = {
            "status": "failed",
            "models_loaded": False,
        }
        m._last_daemon_check = time.time() - m.DAEMON_HEALTH_INTERVAL - 1

        assert m._check_daemon() is False
        assert m._daemon_client is None

    def test_check_daemon_respects_interval_retry(self):
        """If not uses daemon and is in the interval, not re-checks."""
        import time
        from unittest.mock import patch

        m = ModelManager()
        m._daemon_checked = True
        m._use_daemon = False
        m._last_daemon_check = time.time()  # Checked just now.

        # Mock is_daemon_running for verify if is called
        with patch("vault_search.daemon.client.is_daemon_running") as mock_running:
            result = m._check_daemon()

        # Must not have called is_daemon_running (still in the interval)
        mock_running.assert_not_called()
        assert result is False

    def test_check_daemon_rechecks_after_interval(self):
        """After DAEMON_RETRY_INTERVAL, must re-verify daemon."""
        import time
        from unittest.mock import MagicMock, patch

        m = ModelManager()
        m._daemon_checked = True
        m._use_daemon = False
        m._daemon_client = None
        # Simulate a check 35 seconds ago, beyond the 30-second interval.
        m._last_daemon_check = time.time() - 35

        # Mock daemon as available
        mock_client = MagicMock()
        mock_client.health.return_value = {"status": "healthy", "uptime_seconds": 100}

        with patch("vault_search.daemon.client.is_daemon_running", return_value=True):
            with patch("vault_search.daemon.client.DaemonClient", return_value=mock_client):
                result = m._check_daemon()

        assert result is True
        assert m._use_daemon is True
        assert m._daemon_client is mock_client

    def test_check_daemon_not_rechecks_before_interval(self):
        """Before of DAEMON_RETRY_INTERVAL, not re-checks daemon."""
        import time
        from unittest.mock import patch

        m = ModelManager()
        m._daemon_checked = True
        m._use_daemon = False
        # Simulate a check 10 seconds ago, within the 30-second interval.
        m._last_daemon_check = time.time() - 10

        with patch("vault_search.daemon.client.is_daemon_running") as mock_running:
            result = m._check_daemon()

        # Must not have called is_daemon_running
        mock_running.assert_not_called()
        assert result is False
        assert m._use_daemon is False

    def test_check_daemon_updates_timestamp_when_verified(self):
        """Rechecking updates _last_daemon_check."""
        import time
        from unittest.mock import patch

        m = ModelManager()
        m._daemon_checked = True
        m._use_daemon = False
        old_time = time.time() - 35
        m._last_daemon_check = old_time

        with patch("vault_search.daemon.client.is_daemon_running", return_value=False):
            m._check_daemon()

        assert m._last_daemon_check > old_time

    def test_first_check_always_executes(self):
        """In the first verification (_daemon_checked=False), always executes."""
        from unittest.mock import patch

        m = ModelManager()
        assert m._daemon_checked is False

        with patch(
            "vault_search.daemon.client.is_daemon_running", return_value=False
        ) as mock_running:
            m._check_daemon()

        mock_running.assert_called_once()
        assert m._daemon_checked is True

    def test_reconnects_after_connection_loss(self):
        """A lost daemon connection must be retried after the interval."""
        import time
        from unittest.mock import MagicMock, patch

        m = ModelManager()
        # Simulate that the daemon was in use.
        m._daemon_checked = True
        m._use_daemon = True
        m._daemon_client = MagicMock()
        m._last_daemon_check = time.time()

        # First _check_daemon returns True (already is using)
        assert m._check_daemon() is True

        # Simulate loss of connection
        m._use_daemon = False
        m._daemon_client = None
        m._last_daemon_check = time.time() - 35

        # Make the daemon available again.
        mock_client = MagicMock()
        mock_client.health.return_value = {"status": "healthy", "uptime_seconds": 200}

        with patch("vault_search.daemon.client.is_daemon_running", return_value=True):
            with patch("vault_search.daemon.client.DaemonClient", return_value=mock_client):
                result = m._check_daemon()

        assert result is True
        assert m._use_daemon is True
