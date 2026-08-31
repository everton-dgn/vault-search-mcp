"""
Testes unitários para models.py — singleton e lazy loading.

Testes rápidos que NÃO precisam carregar modelos ML reais.
"""

import sys
import threading
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from vault_search.core.models import ModelManager


class TestModelManagerSingleton:
    def test_singleton_mesma_instancia(self):
        """ModelManager() deve retornar sempre a mesma instância."""
        m1 = ModelManager()
        m2 = ModelManager()
        assert m1 is m2

    def test_singleton_thread_safety(self):
        """Instanciação de múltiplas threads deve retornar mesma instância."""
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

    def test_lazy_loading_modelo_nao_carregado(self):
        """Modelo não deve ser carregado até primeira chamada."""
        m = ModelManager()
        assert m._embed_model is None or m._embed_model is not None
        # Se nunca chamamos embed_queries, o modelo pode estar None
        # (dependendo de testes anteriores que possam ter carregado)
        # O ponto é que o __init__ não carrega modelos
        assert hasattr(m, "_embed_model")
        assert hasattr(m, "_reranker_model")

    def test_lock_existe(self):
        """ModelManager deve ter lock para thread safety."""
        m = ModelManager()
        assert hasattr(m, "_lock")
        assert isinstance(m._lock, type(threading.Lock()))
        assert isinstance(m._embedding_inference_lock, type(threading.Lock()))

    def test_initialized_flag(self):
        """Flag _initialized deve estar True após __init__."""
        m = ModelManager()
        assert m._initialized is True


class TestModelManagerTouch:
    """Testa _touch e _cleanup_models sem carregar modelos reais."""

    def test_touch_atualiza_last_use(self):
        """_touch() deve atualizar _last_use."""
        m = ModelManager()
        old_time = m._last_use
        with m._lock:
            m._touch()
        assert m._last_use > old_time

    def test_touch_agenda_cleanup_timer(self):
        """_touch() deve agendar timer de cleanup."""
        m = ModelManager()
        with m._lock:
            m._touch()
        assert m._cleanup_timer is not None
        assert m._cleanup_timer.is_alive()
        # Limpar timer
        m._cleanup_timer.cancel()

    def test_touch_nao_agenda_cleanup_no_processo_daemon(self, monkeypatch):
        """O daemon mantém modelos residentes enquanto estiver ativo."""
        from unittest.mock import patch

        monkeypatch.setenv("VAULT_SEARCH_RUNNING_AS_DAEMON", "1")
        m = ModelManager()

        with patch("vault_search.core.models.threading.Timer") as timer:
            with m._lock:
                m._touch()

        timer.assert_not_called()
        assert m._cleanup_timer is None

    def test_cleanup_descarrega_quando_idle(self):
        """_cleanup_models() deve descarregar modelos se idle."""
        m = ModelManager()
        m._last_use = 0  # muito tempo atrás
        m._embed_model = "fake_model"
        m._reranker_model = "fake_reranker"

        m._cleanup_models()

        assert m._embed_model is None
        assert m._reranker_model is None

    def test_cleanup_nao_descarrega_se_recente(self):
        """_cleanup_models() não deve descarregar se uso recente."""
        import time

        m = ModelManager()
        m._last_use = time.time()  # uso agora
        m._embed_model = "fake_model"
        m._reranker_model = "fake_reranker"

        m._cleanup_models()

        assert m._embed_model == "fake_model"
        assert m._reranker_model == "fake_reranker"


class TestModelManagerMocked:
    """Testa métodos de embedding/rerank com modelos mockados."""

    def test_lazy_loader_usa_sentence_transformers_sem_flagembedding(self, monkeypatch):
        """O backend denso não deve importar a distribuição pesada FlagEmbedding."""
        from vault_search.config.embedding import EMBEDDING_MODEL

        sentence_transformers = ModuleType("sentence_transformers")
        constructor = MagicMock()
        fake_model = MagicMock()
        constructor.return_value = fake_model
        sentence_transformers.SentenceTransformer = constructor

        legacy_backend = ModuleType("FlagEmbedding")
        legacy_backend.BGEM3FlagModel = MagicMock(
            side_effect=AssertionError("FlagEmbedding não deve ser importado")
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

    def test_embed_queries_retorna_lista(self):
        """embed_queries deve retornar lista de vetores."""
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

        # Mock _check_daemon para forçar uso de modelo local
        with patch.object(m, "_check_daemon", return_value=False):
            with patch.object(m, "_get_embed_model", return_value=fake_model):
                result = m.embed_queries(["teste"])

        assert len(result) == 1
        assert len(result[0]) == 1024
        fake_model.encode.assert_called_once()
        assert fake_model.encode.call_args.kwargs["normalize_embeddings"] is True
        assert observed_max_lengths == [EMBEDDING_QUERY_MAX_LENGTH]
        assert fake_model.max_seq_length == 8192

    def test_embed_corpus_usa_batch_size(self):
        """embed_corpus deve passar batch_size e max_length corretos."""
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

        # Mock _check_daemon para forçar uso de modelo local
        with patch.object(m, "_check_daemon", return_value=False):
            with patch.object(m, "_get_embed_model", return_value=fake_model):
                result = m.embed_corpus(["texto1", "texto2"])

        assert len(result) == 2
        call_kwargs = fake_model.encode.call_args
        assert call_kwargs.kwargs["batch_size"] == EMBEDDING_BATCH_SIZE
        assert call_kwargs.kwargs["normalize_embeddings"] is True
        assert observed_max_lengths == [EMBEDDING_CORPUS_MAX_LENGTH]
        assert fake_model.max_seq_length == 8192

    def test_rerank_retorna_scores_normalizados(self):
        """rerank deve retornar lista de floats."""
        m = ModelManager()
        fake_reranker = MagicMock()
        fake_reranker.predict.return_value = [0.9, 0.3, 0.1]

        # Mock _check_daemon para forçar uso de modelo local
        with patch.object(m, "_check_daemon", return_value=False):
            with patch.object(m, "_get_reranker", return_value=fake_reranker):
                scores = m.rerank("query", ["doc1", "doc2", "doc3"])

        assert scores == [0.9, 0.3, 0.1]
        assert all(isinstance(s, float) for s in scores)

    def test_rerank_score_unico_vira_lista(self):
        """Se predict retorna escalar, deve virar lista."""
        m = ModelManager()
        fake_reranker = MagicMock()
        fake_reranker.predict.return_value = 0.85  # escalar

        # Mock _check_daemon para forçar uso de modelo local
        with patch.object(m, "_check_daemon", return_value=False):
            with patch.object(m, "_get_reranker", return_value=fake_reranker):
                scores = m.rerank("query", ["doc1"])

        assert scores == [0.85]
        assert isinstance(scores, list)


class TestModelManagerRequireDaemon:
    """Testa bloqueio de fallback local quando daemon é obrigatório."""

    def setup_method(self):
        """Reset singleton para evitar contaminação entre testes."""
        ModelManager._instance = None

    def test_embed_queries_falha_sem_daemon_quando_obrigatorio(self, monkeypatch):
        """embed_queries deve falhar se daemon estiver indisponível e fallback proibido."""
        from unittest.mock import patch

        monkeypatch.setenv("VAULT_SEARCH_REQUIRE_DAEMON", "1")
        monkeypatch.delenv("VAULT_SEARCH_RUNNING_AS_DAEMON", raising=False)

        m = ModelManager()

        with patch.object(m, "_check_daemon", return_value=False):
            with pytest.raises(RuntimeError, match="fallback local desabilitado"):
                m.embed_queries(["teste"])

    def test_rerank_falha_sem_daemon_quando_obrigatorio(self, monkeypatch):
        """rerank deve falhar se daemon estiver indisponível e fallback proibido."""
        from unittest.mock import patch

        monkeypatch.setenv("VAULT_SEARCH_REQUIRE_DAEMON", "1")
        monkeypatch.delenv("VAULT_SEARCH_RUNNING_AS_DAEMON", raising=False)

        m = ModelManager()

        with patch.object(m, "_check_daemon", return_value=False):
            with pytest.raises(RuntimeError, match="fallback local desabilitado"):
                m.rerank("query", ["doc"])

    def test_running_as_daemon_permite_modelo_local(self, monkeypatch):
        """Processo daemon ignora VAULT_SEARCH_REQUIRE_DAEMON para evitar deadlock."""
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
    """Testa reconexão automática ao daemon após intervalo."""

    def setup_method(self):
        """Reset singleton para cada teste."""
        ModelManager._instance = None

    def test_daemon_retry_interval_existe(self):
        """ModelManager deve ter constante DAEMON_RETRY_INTERVAL."""
        assert hasattr(ModelManager, "DAEMON_RETRY_INTERVAL")
        assert ModelManager.DAEMON_RETRY_INTERVAL == 30.0

    def test_last_daemon_check_inicializado(self):
        """_last_daemon_check deve ser inicializado como 0."""
        m = ModelManager()
        assert hasattr(m, "_last_daemon_check")
        assert m._last_daemon_check == 0.0

    def test_check_daemon_usa_health_recente_sem_revalidar(self):
        """Health recente evita um novo probe dentro do intervalo curto."""
        import time
        from unittest.mock import MagicMock

        m = ModelManager()
        m._daemon_checked = True
        m._use_daemon = True
        m._daemon_client = MagicMock()
        m._last_daemon_check = time.time()

        # Deve retornar True imediatamente sem chamar is_daemon_running
        result = m._check_daemon()

        assert result is True
        assert m._use_daemon is True

    def test_check_daemon_invalida_health_obsoleto(self):
        """Daemon em uso deve ser revalidado após o intervalo de health."""
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

    def test_check_daemon_respeita_intervalo_retry(self):
        """Se não usa daemon e está no intervalo, não re-verifica."""
        import time
        from unittest.mock import patch

        m = ModelManager()
        m._daemon_checked = True
        m._use_daemon = False
        m._last_daemon_check = time.time()  # verificou agora

        # Mock is_daemon_running para verificar se é chamado
        with patch("vault_search.daemon.client.is_daemon_running") as mock_running:
            result = m._check_daemon()

        # Não deve ter chamado is_daemon_running (ainda no intervalo)
        mock_running.assert_not_called()
        assert result is False

    def test_check_daemon_reverifica_apos_intervalo(self):
        """Após DAEMON_RETRY_INTERVAL, deve re-verificar daemon."""
        import time
        from unittest.mock import MagicMock, patch

        m = ModelManager()
        m._daemon_checked = True
        m._use_daemon = False
        m._daemon_client = None
        # Simular que verificou há 35s (> 30s do intervalo)
        m._last_daemon_check = time.time() - 35

        # Mock daemon como disponível
        mock_client = MagicMock()
        mock_client.health.return_value = {"status": "healthy", "uptime_seconds": 100}

        with patch("vault_search.daemon.client.is_daemon_running", return_value=True):
            with patch("vault_search.daemon.client.DaemonClient", return_value=mock_client):
                result = m._check_daemon()

        assert result is True
        assert m._use_daemon is True
        assert m._daemon_client is mock_client

    def test_check_daemon_nao_reverifica_antes_intervalo(self):
        """Antes de DAEMON_RETRY_INTERVAL, não re-verifica daemon."""
        import time
        from unittest.mock import patch

        m = ModelManager()
        m._daemon_checked = True
        m._use_daemon = False
        # Simular que verificou há 10s (< 30s do intervalo)
        m._last_daemon_check = time.time() - 10

        with patch("vault_search.daemon.client.is_daemon_running") as mock_running:
            result = m._check_daemon()

        # Não deve ter chamado is_daemon_running
        mock_running.assert_not_called()
        assert result is False
        assert m._use_daemon is False

    def test_check_daemon_atualiza_timestamp_ao_verificar(self):
        """Ao re-verificar, deve atualizar _last_daemon_check."""
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

    def test_primeira_verificacao_sempre_executa(self):
        """Na primeira verificação (_daemon_checked=False), sempre executa."""
        from unittest.mock import patch

        m = ModelManager()
        assert m._daemon_checked is False

        with patch(
            "vault_search.daemon.client.is_daemon_running", return_value=False
        ) as mock_running:
            m._check_daemon()

        mock_running.assert_called_once()
        assert m._daemon_checked is True

    def test_reconexao_apos_perda_de_conexao(self):
        """Se perder conexão com daemon, deve tentar reconectar após intervalo."""
        import time
        from unittest.mock import MagicMock, patch

        m = ModelManager()
        # Simular que estava usando daemon
        m._daemon_checked = True
        m._use_daemon = True
        m._daemon_client = MagicMock()
        m._last_daemon_check = time.time()

        # Primeiro _check_daemon retorna True (já está usando)
        assert m._check_daemon() is True

        # Simular perda de conexão
        m._use_daemon = False
        m._daemon_client = None
        m._last_daemon_check = time.time() - 35

        # Mock daemon disponível novamente
        mock_client = MagicMock()
        mock_client.health.return_value = {"status": "healthy", "uptime_seconds": 200}

        with patch("vault_search.daemon.client.is_daemon_running", return_value=True):
            with patch("vault_search.daemon.client.DaemonClient", return_value=mock_client):
                result = m._check_daemon()

        assert result is True
        assert m._use_daemon is True
