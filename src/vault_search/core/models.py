"""
Gerenciamento centralizado de modelos ML.

Carrega BGE-M3 (embedding) e MiniLM-L-6-v2 (reranking) sob demanda,
compartilhando instâncias entre indexer e searcher para evitar duplicação
de modelos em memória no mesmo processo.

Suporta modo daemon: se o daemon estiver rodando, delega para ele
(resposta instantânea). Senão, carrega modelos localmente, exceto quando
VAULT_SEARCH_REQUIRE_DAEMON=1.

Thread-safe com descarte automático por inatividade.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from vault_search.config.embedding import (
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_CORPUS_MAX_LENGTH,
    EMBEDDING_MODEL,
    EMBEDDING_QUERY_MAX_LENGTH,
    MODEL_DEVICE,
    MODEL_IDLE_TIMEOUT,
    MODEL_USE_FP16,
    RERANKER_BATCH_SIZE,
    RERANKER_MAX_LENGTH,
    RERANKER_MODEL,
    resolve_fp16,
    resolve_model_device,
)
from vault_search.config.loader import get_config
from vault_search.core.exceptions import DaemonRequiredError
from vault_search.utils.retry import retry_embedding

logger = logging.getLogger(__name__)


class ModelManager:
    """
    Singleton para gerenciamento de modelos ML compartilhados.

    Usa o daemon quando ele está pronto. Caso contrário, carrega modelos
    localmente, a menos que VAULT_SEARCH_REQUIRE_DAEMON=1.

    Uso:
        models = ModelManager()
        vec = models.embed_queries(["como funciona X?"])
        vec = models.embed_corpus(["Texto do documento..."])
        scores = models.rerank("query", ["doc1", "doc2"])
    """

    _instance: ModelManager | None = None
    _init_lock = threading.Lock()
    _initialized: bool

    def __new__(cls):
        with cls._init_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    # Intervalo mínimo entre tentativas de reconexão ao daemon (segundos)
    DAEMON_RETRY_INTERVAL = 30.0
    DAEMON_HEALTH_INTERVAL = 5.0
    REQUIRE_DAEMON_ENV_VAR = "VAULT_SEARCH_REQUIRE_DAEMON"
    RUNNING_AS_DAEMON_ENV_VAR = "VAULT_SEARCH_RUNNING_AS_DAEMON"

    def __init__(self):
        if self._initialized:
            return
        self._embed_model = None
        self._reranker_model = None
        self._last_use = 0.0
        self._cleanup_timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._embedding_inference_lock = threading.Lock()
        self._daemon_client: Any = None
        self._daemon_config = get_config().daemon
        self._daemon_checked = False
        self._use_daemon = False
        self._last_daemon_check = 0.0  # timestamp da última verificação
        self._initialized = True

    @staticmethod
    def _env_flag(name: str) -> bool:
        """Retorna True quando variável de ambiente está setada para '1'."""
        return os.environ.get(name) == "1"

    def _is_running_as_daemon(self) -> bool:
        """Retorna True quando este processo é o daemon de modelos."""
        return self._env_flag(self.RUNNING_AS_DAEMON_ENV_VAR)

    def _is_daemon_required(self) -> bool:
        """
        Retorna True quando fallback local está proibido.

        Nota: o próprio processo daemon sempre permite modelos locais
        para evitar deadlock.
        """
        return self._env_flag(self.REQUIRE_DAEMON_ENV_VAR) and not self._is_running_as_daemon()

    def _raise_if_local_fallback_forbidden(self, operation: str) -> None:
        """Falha com erro explícito quando daemon é obrigatório e indisponível."""
        if not self._is_daemon_required():
            return

        raise DaemonRequiredError(
            f"{operation} bloqueado: daemon indisponível e fallback local desabilitado "
            f"({self.REQUIRE_DAEMON_ENV_VAR}=1). Consulte o health check do daemon."
        )

    @staticmethod
    def _daemon_is_ready(health: Any) -> bool:
        """Valida readiness atual e mantém compatibilidade com ``healthy``."""
        if not isinstance(health, dict):
            return False
        if health.get("status") not in {"ready", "healthy"}:
            return False
        loaded = health.get("models_loaded")
        if isinstance(loaded, dict):
            return bool(loaded.get("embed_model") and loaded.get("reranker_model"))
        return loaded is not False

    def _invalidate_daemon(self, *, retry_immediately: bool = True) -> None:
        """Descarta estado obsoleto para a próxima tentativa reconectar."""
        client = self._daemon_client
        if client is not None and hasattr(client, "invalidate"):
            client.invalidate()
        self._daemon_client = None
        self._use_daemon = False
        if retry_immediately:
            self._last_daemon_check = 0.0

    def _check_daemon(self, *, force: bool = False) -> bool:
        """
        Verifica se o daemon está disponível.

        Se já está usando o daemon, retorna True imediatamente.
        Se não está usando, re-verifica periodicamente (a cada DAEMON_RETRY_INTERVAL)
        para detectar quando o daemon ficar disponível.

        Retorna:
            True se daemon disponível e funcionando.
        """
        # Se estamos rodando COMO daemon, nunca tentar conectar a si mesmo
        # (evita deadlock no servidor single-threaded)
        if self._is_running_as_daemon():
            return False

        if not self._daemon_config.auto_use and not self._is_daemon_required():
            return False

        now = time.time()
        interval = self.DAEMON_HEALTH_INTERVAL if self._use_daemon else self.DAEMON_RETRY_INTERVAL
        if not force and self._daemon_checked and (now - self._last_daemon_check) < interval:
            return self._use_daemon

        first_check = not self._daemon_checked
        # Atualizar timestamp e flag
        self._last_daemon_check = now
        self._daemon_checked = True

        try:
            from vault_search.daemon.client import DaemonClient, is_daemon_running

            client = self._daemon_client
            if client is None:
                if not is_daemon_running(
                    self._daemon_config.host,
                    self._daemon_config.port,
                    timeout=min(5.0, self._daemon_config.timeout),
                    retries=1,
                ):
                    raise ConnectionError("daemon_not_ready")
                client = DaemonClient(
                    self._daemon_config.host,
                    self._daemon_config.port,
                    self._daemon_config.timeout,
                )

            health = client.health()
            if self._daemon_is_ready(health):
                self._daemon_client = client
                self._use_daemon = True
                logger.info("daemon_connected")
                return True
        except Exception as error:
            logger.debug(
                "daemon_unavailable",
                extra={"error_type": type(error).__name__},
            )

        # Só logar se estava usando daemon (perdeu conexão) ou é primeira vez
        was_using_daemon = self._use_daemon
        self._invalidate_daemon(retry_immediately=False)

        if was_using_daemon:
            if self._is_daemon_required():
                logger.error(
                    "daemon_state_changed",
                    extra={"state": "unavailable", "fallback_allowed": False},
                )
            else:
                logger.warning(
                    "daemon_state_changed",
                    extra={"state": "unavailable", "fallback_allowed": True},
                )
        elif first_check:
            if self._is_daemon_required():
                logger.error(
                    "daemon_probe_completed",
                    extra={"state": "unavailable", "fallback_allowed": False},
                )
            else:
                logger.info(
                    "daemon_probe_completed",
                    extra={"state": "unavailable", "fallback_allowed": True},
                )

        return False

    def require_daemon(
        self,
        max_wait: float | None = None,
        check_interval: float = 2.0,
    ) -> None:
        """
        Exige que o daemon esteja disponível. Espera ou falha.

        Args:
            max_wait: Tempo máximo de espera em segundos.
                      None = espera indefinidamente.
            check_interval: Intervalo entre tentativas (default: 2s).

        Raises:
            RuntimeError: Se max_wait excedido e daemon não disponível.
        """
        import time

        from vault_search.daemon.client import DaemonClient, is_daemon_running

        daemon_config = self._daemon_config

        start = time.time()
        attempt = 0

        while True:
            attempt += 1
            try:
                if is_daemon_running(
                    daemon_config.host,
                    daemon_config.port,
                    timeout=min(5.0, daemon_config.timeout),
                    retries=1,
                ):
                    client = DaemonClient(
                        daemon_config.host,
                        daemon_config.port,
                        daemon_config.timeout,
                    )
                    health = client.health()
                    if self._daemon_is_ready(health):
                        self._daemon_client = client
                        self._use_daemon = True
                        self._daemon_checked = True
                        logger.info("daemon_connected")
                        return
            except Exception as error:
                logger.debug(
                    "daemon_wait_attempt_failed",
                    extra={
                        "attempt": attempt,
                        "error_type": type(error).__name__,
                    },
                )

            # Verificar timeout
            elapsed = time.time() - start
            if max_wait is not None and elapsed >= max_wait:
                raise RuntimeError(
                    f"Daemon não disponível após {elapsed:.1f}s ({attempt} tentativas)."
                )

            # Log de espera
            if max_wait is None:
                logger.info(
                    f"Aguardando daemon... (tentativa {attempt}, {elapsed:.0f}s decorridos)"
                )
            else:
                remaining = max_wait - elapsed
                logger.info(
                    f"Aguardando daemon... (tentativa {attempt}, {remaining:.0f}s restantes)"
                )

            time.sleep(check_interval)

    @property
    def using_daemon(self) -> bool:
        """Retorna True se está usando o daemon."""
        self._check_daemon()
        return self._use_daemon

    def _get_embed_model(self):
        """
        Carrega modelo de embedding sob demanda, com thread safety.

        Retorna referência local para evitar race condition TOCTOU
        entre liberação do lock e chamada a _touch().
        """
        with self._lock:
            if self._embed_model is None:
                from sentence_transformers import SentenceTransformer

                device = resolve_model_device(MODEL_DEVICE)
                logger.info("embedding_model_loading")
                self._embed_model = SentenceTransformer(EMBEDDING_MODEL, device=device)
                if resolve_fp16(device, MODEL_USE_FP16):
                    self._embed_model.half()
                logger.info("embedding_model_loaded")
            model = self._embed_model  # referência local imune a cleanup
            self._touch()
        return model

    def _get_reranker(self):
        """
        Carrega modelo de reranking sob demanda, com thread safety.

        Retorna referência local para evitar race condition TOCTOU.
        """
        with self._lock:
            if self._reranker_model is None:
                from sentence_transformers import CrossEncoder

                device = resolve_model_device(MODEL_DEVICE)
                logger.info("reranker_model_loading")
                self._reranker_model = CrossEncoder(
                    RERANKER_MODEL,
                    max_length=RERANKER_MAX_LENGTH,
                    device=device,
                )
                logger.info("reranker_model_loaded")
            reranker = self._reranker_model  # referência local imune a cleanup
            self._touch()
        return reranker

    def _touch(self):
        """
        Registra uso recente e agenda limpeza por inatividade.

        DEVE ser chamado dentro do self._lock para evitar race condition
        TOCTOU com _cleanup_models().
        """
        self._last_use = time.time()
        if self._cleanup_timer is not None:
            self._cleanup_timer.cancel()
            self._cleanup_timer = None  # FIX: Limpar referência após cancel
        if self._is_running_as_daemon():
            return
        self._cleanup_timer = threading.Timer(MODEL_IDLE_TIMEOUT, self._cleanup_models)
        self._cleanup_timer.daemon = True
        self._cleanup_timer.start()

    def _cleanup_models(self):
        """Descarrega modelos da memória se inativos."""
        with self._lock:
            elapsed = time.time() - self._last_use
            if elapsed >= MODEL_IDLE_TIMEOUT:
                logger.info("Descarregando modelos por inatividade...")
                self._embed_model = None
                self._reranker_model = None

    def cleanup(self):
        """Cancela timers e libera recursos. Chamar no shutdown."""
        with self._lock:
            if self._cleanup_timer is not None:
                self._cleanup_timer.cancel()
                self._cleanup_timer = None
            self._embed_model = None
            self._reranker_model = None

    def _call_daemon(self, operation: str, callback) -> tuple[bool, Any]:
        """Executa no daemon e invalida o backend ao detectar falha real."""
        if not self._check_daemon():
            return False, None
        try:
            return True, callback()
        except (
            ConnectionError,
            TimeoutError,
            OSError,
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            IndexError,
        ) as error:
            self._invalidate_daemon()
            logger.warning(
                "daemon_operation_failed",
                extra={
                    "operation": operation,
                    "state": "invalidated",
                    "error_type": type(error).__name__,
                },
            )
            self._raise_if_local_fallback_forbidden(operation)
            return False, None

    @retry_embedding
    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        """
        Gera embeddings otimizados para queries de busca.

        Se daemon disponível, delega para ele (instantâneo).
        Senão, usa modelo local com retry automático.

        Parâmetros:
            texts: lista de queries

        Retorna:
            Lista de vetores 1024-dim.
        """
        used_daemon, result = self._call_daemon(
            "embed_queries",
            lambda: self._daemon_client.embed_queries(texts),
        )
        if used_daemon:
            return result.tolist() if hasattr(result, "tolist") else result

        self._raise_if_local_fallback_forbidden("embed_queries")
        model = self._get_embed_model()
        with self._embedding_inference_lock:
            previous_max_length = model.max_seq_length
            model.max_seq_length = EMBEDDING_QUERY_MAX_LENGTH
            try:
                vectors = model.encode(
                    texts,
                    batch_size=EMBEDDING_BATCH_SIZE,
                    show_progress_bar=False,
                    normalize_embeddings=True,
                )
            finally:
                model.max_seq_length = previous_max_length
        return vectors.tolist() if hasattr(vectors, "tolist") else list(vectors)

    @retry_embedding
    def embed_corpus(self, texts: list[str]) -> list[list[float]]:
        """
        Gera embeddings otimizados para documentos/passagens.

        Se daemon disponível, delega para ele (instantâneo).
        Senão, usa modelo local com retry automático.

        Parâmetros:
            texts: lista de textos a indexar

        Retorna:
            Lista de vetores 1024-dim.
        """
        used_daemon, result = self._call_daemon(
            "embed_corpus",
            lambda: self._daemon_client.embed_corpus(texts),
        )
        if used_daemon:
            return result.tolist() if hasattr(result, "tolist") else result

        self._raise_if_local_fallback_forbidden("embed_corpus")
        model = self._get_embed_model()
        with self._embedding_inference_lock:
            previous_max_length = model.max_seq_length
            model.max_seq_length = EMBEDDING_CORPUS_MAX_LENGTH
            try:
                vectors = model.encode(
                    texts,
                    batch_size=EMBEDDING_BATCH_SIZE,
                    show_progress_bar=False,
                    normalize_embeddings=True,
                )
            finally:
                model.max_seq_length = previous_max_length
        return vectors.tolist() if hasattr(vectors, "tolist") else list(vectors)

    def rerank(self, query: str, texts: list[str]) -> list[float]:
        """
        Calcula scores de relevância query-documento via cross-encoder.

        Se daemon disponível, delega para ele.
        Senão, usa modelo local.

        Parâmetros:
            query: texto da consulta
            texts: lista de textos candidatos

        Retorna:
            Lista de scores normalizados [0, 1].
        """
        used_daemon, results = self._call_daemon(
            "rerank",
            lambda: self._daemon_client.rerank(query, texts, top_k=len(texts)),
        )
        if used_daemon:
            # Daemon retorna [(index, score), ...]
            # Precisamos converter para lista de scores na ordem original
            # Criar lista com scores na ordem original
            scores = [0.0] * len(texts)
            for idx, score in results:
                if idx < len(scores):
                    scores[idx] = score
            return scores

        self._raise_if_local_fallback_forbidden("rerank")
        reranker = self._get_reranker()
        pairs = [(query, t) for t in texts]
        scores = reranker.predict(
            pairs,
            batch_size=RERANKER_BATCH_SIZE,
            show_progress_bar=False,
        )
        if isinstance(scores, (int, float)):
            scores = [scores]
        return [float(s) for s in scores]

    def warmup(self) -> dict[str, Any]:
        """
        Pré-carrega modelos com queries dummy para eliminar cold start.

        Se daemon disponível, apenas verifica saúde.
        Senão, carrega modelos localmente.

        Retorna:
            Dict com tempos de warmup para cada modelo.
        """
        result: dict[str, Any] = {"embed_ms": 0.0, "rerank_ms": 0.0}

        if self._check_daemon(force=True):
            result["using_daemon"] = True
            result["daemon_uptime"] = self._daemon_client.health().get("uptime_seconds", 0)
            logger.info("Warmup via daemon (modelos já carregados)")
            return result

        self._raise_if_local_fallback_forbidden("warmup")

        # Warmup embedding
        start = time.time()
        try:
            self.embed_queries(["warmup query for semantic search"])
            result["embed_ms"] = round((time.time() - start) * 1000, 1)
            logger.info(f"Embedding model warmed up in {result['embed_ms']:.1f}ms")
        except Exception as error:
            logger.warning(
                "embedding_warmup_failed",
                extra={"error_type": type(error).__name__},
            )
            result["embed_error"] = type(error).__name__

        # Warmup reranker
        start = time.time()
        try:
            self.rerank("warmup query", ["warmup document text"])
            result["rerank_ms"] = round((time.time() - start) * 1000, 1)
            logger.info(f"Reranker model warmed up in {result['rerank_ms']:.1f}ms")
        except Exception as error:
            logger.warning(
                "reranker_warmup_failed",
                extra={"error_type": type(error).__name__},
            )
            result["rerank_error"] = type(error).__name__

        return result

    def is_loaded(self) -> dict[str, bool]:
        """
        Verifica se os modelos estão carregados na memória.

        Retorna:
            Dict indicando status de cada modelo e modo de operação.
        """
        if self._check_daemon():
            return {
                "embed_model": True,
                "reranker_model": True,
                "using_daemon": True,
                "daemon_required": self._is_daemon_required(),
            }

        with self._lock:
            return {
                "embed_model": self._embed_model is not None,
                "reranker_model": self._reranker_model is not None,
                "using_daemon": False,
                "daemon_required": self._is_daemon_required(),
            }
