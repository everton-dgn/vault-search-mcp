"""
Servidor HTTP do daemon para manter modelos em memória e monitorar vault.

Endpoints:
- POST /embed/queries - embed queries (max_length=512)
- POST /embed/corpus - embed corpus (max_length=1024)
- POST /rerank - rerank results
- GET /health - health check
- GET /stats - estatísticas

File Watcher:
- Monitora o vault Obsidian
- Reindexar notas automaticamente
- Enriquece frontmatter via IA (campos required faltantes)
"""

from __future__ import annotations

import json
import logging
import signal
import threading
import time
from errno import EADDRINUSE
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer as HTTPServer
from typing import Any, Literal

import numpy as np

from vault_search.config.loader import get_config
from vault_search.core.models import ModelManager
from vault_search.utils.logging import PrivacyFilter
from vault_search.utils.network import is_loopback_host
from vault_search.utils.shutdown import (
    ShutdownManager,
    request_shutdown,
    shutdown_requested,
)

logger = logging.getLogger(__name__)

HealthState = Literal["starting", "ready", "degraded", "failed"]


class RequestValidationError(ValueError):
    """Erro de protocolo HTTP seguro para retornar ao cliente."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class DaemonRequestHandler(BaseHTTPRequestHandler):
    """Handler HTTP para requests do daemon."""

    # Referência para o servidor (set em DaemonServer)
    daemon_server: DaemonServer | None = None

    def log_message(self, format: str, *args: Any) -> None:
        """Registra apenas metadados controlados, sem request target."""
        status_code = args[1] if len(args) > 1 else "unknown"
        logger.debug("http_request_completed status=%s", status_code)

    def _send_json(self, data: dict[str, Any], status: int = 200) -> None:
        """Envia resposta JSON."""
        response = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def _read_json(self) -> dict[str, Any] | None:
        """Lê body JSON da request."""
        server = self.daemon_server
        if server is None:
            raise RequestValidationError("Server not initialized", 503)

        try:
            raw_content_length = self.headers.get("Content-Length")
            if raw_content_length is None:
                raise RequestValidationError("Content-Length is required", 411)
            content_length = int(raw_content_length)
            if content_length <= 0:
                raise RequestValidationError("JSON body is required")
            if content_length > server.max_request_bytes:
                raise RequestValidationError("Request body is too large", 413)

            content_type = self.headers.get("Content-Type", "").split(";", 1)[0]
            if content_type.lower() != "application/json":
                raise RequestValidationError("Content-Type must be application/json", 415)

            body = self.rfile.read(content_length)
            data = json.loads(body.decode("utf-8"))
            if not isinstance(data, dict):
                raise RequestValidationError("JSON body must be an object")
            return data
        except RequestValidationError:
            raise
        except json.JSONDecodeError, UnicodeDecodeError, ValueError:
            raise RequestValidationError("Invalid JSON") from None

    def _read_validated_json(self) -> dict[str, Any] | None:
        """Converte falhas de protocolo em uma única resposta HTTP."""
        try:
            return self._read_json()
        except RequestValidationError as error:
            self._send_json({"error": str(error)}, error.status)
            return None

    def _require_ready(self) -> DaemonServer | None:
        """Bloqueia inferência enquanto o warmup não estiver íntegro."""
        server = self.daemon_server
        if server is None:
            self._send_json({"error": "Server not initialized"}, 503)
            return None
        snapshot = server.health_snapshot()
        if snapshot["status"] != "ready":
            self._send_json(
                {"error": "Daemon is not ready", "status": snapshot["status"]},
                503,
            )
            return None
        return server

    def do_GET(self) -> None:
        """Handle GET requests."""
        if self.path == "/health":
            self._handle_health()
        elif self.path == "/stats":
            self._handle_stats()
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_POST(self) -> None:
        """Handle POST requests."""
        if self.path == "/embed/queries":
            self._handle_embed_queries()
        elif self.path == "/embed/corpus":
            self._handle_embed_corpus()
        elif self.path == "/rerank":
            self._handle_rerank()
        else:
            self._send_json({"error": "Not found"}, 404)

    def _handle_health(self) -> None:
        """Health check."""
        server = self.daemon_server
        if server is None:
            self._send_json({"status": "failed", "message": "Server not initialized"}, 503)
            return

        snapshot = server.health_snapshot()
        http_status = 200 if snapshot["status"] == "ready" else 503
        self._send_json(snapshot, http_status)

    def _handle_stats(self) -> None:
        """Retorna estatísticas do daemon."""
        server = self.daemon_server
        if server is None:
            self._send_json({"error": "Server not initialized"}, 500)
            return

        snapshot = server.health_snapshot()
        stats = {
            "status": snapshot["status"],
            "uptime_seconds": time.time() - server.start_time,
            "models_loaded": server.models_loaded,
            "requests_served": server.request_count,
            "embed_queries_count": server.embed_queries_count,
            "embed_corpus_count": server.embed_corpus_count,
            "rerank_count": server.rerank_count,
            "watcher_enabled": server._watcher is not None,
            "watcher_running": server._watcher.is_running if server._watcher else False,
            "reindex_count": server.reindex_count,
        }
        self._send_json(stats)

    def _handle_embed_queries(self) -> None:
        """Embed queries (max_length=512)."""
        server = self._require_ready()
        if server is None:
            return

        data = self._read_validated_json()
        if data is None:
            return

        try:
            texts = server.validate_texts(data.get("texts"))
        except RequestValidationError as error:
            self._send_json({"error": str(error)}, error.status)
            return

        try:
            embeddings = server.models.embed_queries(texts)
            server.embed_queries_count += len(texts)
            server.request_count += 1

            # Converter numpy para lista
            if isinstance(embeddings, np.ndarray):
                embeddings = embeddings.tolist()

            self._send_json({"embeddings": embeddings})
        except Exception as error:
            logger.error(
                "embed_queries_failed",
                extra={"error_type": type(error).__name__},
            )
            self._send_json({"error": "Model inference failed"}, 500)

    def _handle_embed_corpus(self) -> None:
        """Embed corpus (max_length=1024)."""
        server = self._require_ready()
        if server is None:
            return

        data = self._read_validated_json()
        if data is None:
            return

        try:
            texts = server.validate_texts(data.get("texts"))
        except RequestValidationError as error:
            self._send_json({"error": str(error)}, error.status)
            return

        try:
            embeddings = server.models.embed_corpus(texts)
            server.embed_corpus_count += len(texts)
            server.request_count += 1

            # Converter numpy para lista
            if isinstance(embeddings, np.ndarray):
                embeddings = embeddings.tolist()

            self._send_json({"embeddings": embeddings})
        except Exception as error:
            logger.error(
                "embed_corpus_failed",
                extra={"error_type": type(error).__name__},
            )
            self._send_json({"error": "Model inference failed"}, 500)

    def _handle_rerank(self) -> None:
        """Rerank results."""
        server = self._require_ready()
        if server is None:
            return

        data = self._read_validated_json()
        if data is None:
            return

        try:
            query, texts, top_k = server.validate_rerank(
                data.get("query"),
                data.get("texts"),
                data.get("top_k"),
            )
        except RequestValidationError as error:
            self._send_json({"error": str(error)}, error.status)
            return

        try:
            # ModelManager.rerank() retorna lista de scores na ordem dos textos
            all_scores = server.models.rerank(query, texts)
            server.rerank_count += 1
            server.request_count += 1

            # Criar lista de (index, score) ordenada por score
            indexed_scores = list(enumerate(all_scores))
            indexed_scores.sort(key=lambda x: x[1], reverse=True)

            # Limitar ao top_k
            top_scores = indexed_scores[:top_k]

            self._send_json({"scores": top_scores})
        except Exception as error:
            logger.error(
                "rerank_failed",
                extra={"error_type": type(error).__name__},
            )
            self._send_json({"error": "Model inference failed"}, 500)


class DaemonServer:
    """Servidor daemon que mantém modelos em memória e monitora vault."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        enable_watcher: bool = True,
    ):
        config = get_config()
        daemon_config = config.daemon
        self.host = host or daemon_config.host
        self.port = port or daemon_config.port
        if not is_loopback_host(self.host):
            raise ValueError("O daemon aceita apenas bind em loopback")

        self.enable_watcher = enable_watcher
        self.max_request_bytes = daemon_config.max_request_bytes
        self.max_texts = daemon_config.max_texts
        self.max_text_length = daemon_config.max_text_length
        self.max_query_length = config.security.max_query_length
        self.models = ModelManager()
        self.start_time = time.time()
        self.request_count = 0
        self.embed_queries_count = 0
        self.embed_corpus_count = 0
        self.rerank_count = 0
        self.reindex_count = 0  # Contagem de reindex via watcher
        self._server: HTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._init_thread: threading.Thread | None = None
        self._watcher: Any = None
        self._indexer: Any = None
        self._health_state: HealthState = "starting"
        self._health_lock = threading.Lock()
        self._warmup_errors: tuple[str, ...] = ()

    @property
    def health_state(self) -> HealthState:
        """Estado atual, protegido contra leituras concorrentes."""
        with self._health_lock:
            return self._health_state

    @property
    def model_status(self) -> dict[str, bool]:
        """Normaliza o contrato legado de ``ModelManager.is_loaded``."""
        try:
            status = self.models.is_loaded()
        except Exception:
            return {"embed_model": False, "reranker_model": False}
        if isinstance(status, dict):
            return {
                "embed_model": bool(status.get("embed_model")),
                "reranker_model": bool(status.get("reranker_model")),
            }
        loaded = bool(status)
        return {"embed_model": loaded, "reranker_model": loaded}

    @property
    def models_loaded(self) -> bool:
        """Indica que os dois modelos necessários estão disponíveis."""
        status = self.model_status
        return status["embed_model"] and status["reranker_model"]

    def _set_health_state(
        self,
        state: HealthState,
        errors: tuple[str, ...] = (),
    ) -> None:
        with self._health_lock:
            self._health_state = state
            self._warmup_errors = errors

    def health_snapshot(self) -> dict[str, Any]:
        """Retorna health sem expor exceções, paths ou conteúdo processado."""
        with self._health_lock:
            state = self._health_state
            errors = self._warmup_errors
        model_status = self.model_status
        models_loaded = model_status["embed_model"] and model_status["reranker_model"]
        if state == "ready" and not models_loaded:
            state = "degraded"
            errors = (*errors, "models_unloaded")
        return {
            "status": state,
            "models_loaded": models_loaded,
            "model_status": model_status,
            "warmup_errors": list(errors),
            "watcher_running": self._watcher.is_running if self._watcher else False,
            "uptime_seconds": time.time() - self.start_time,
        }

    def validate_texts(self, value: Any) -> list[str]:
        """Valida batches antes de alocar memória para inferência."""
        if not isinstance(value, list) or not value:
            raise RequestValidationError("texts must be a non-empty list")
        if len(value) > self.max_texts:
            raise RequestValidationError("texts exceeds the configured batch limit", 413)
        if any(not isinstance(text, str) or not text.strip() for text in value):
            raise RequestValidationError("texts must contain non-empty strings")
        if any(len(text) > self.max_text_length for text in value):
            raise RequestValidationError("a text exceeds the configured length limit", 413)
        return value

    def validate_rerank(
        self,
        query: Any,
        texts: Any,
        top_k: Any = None,
    ) -> tuple[str, list[str], int]:
        """Valida a operação de reranking e normaliza ``top_k``."""
        if not isinstance(query, str) or not query.strip():
            raise RequestValidationError("query must be a non-empty string")
        if len(query) > self.max_query_length:
            raise RequestValidationError("query exceeds the configured length limit", 413)
        validated_texts = self.validate_texts(texts)
        effective_top_k = len(validated_texts) if top_k is None else top_k
        if (
            isinstance(effective_top_k, bool)
            or not isinstance(effective_top_k, int)
            or not 1 <= effective_top_k <= len(validated_texts)
        ):
            raise RequestValidationError(
                "top_k must be an integer between 1 and the number of texts"
            )
        return query, validated_texts, effective_top_k

    def _initialize_runtime(self) -> None:
        """Executa warmup e deriva o estado operacional observado."""
        self._set_health_state("starting")
        logger.info("model_warmup_started")
        try:
            result = self.models.warmup() or {}
        except Exception as error:
            logger.error(
                "model_warmup_failed",
                extra={"error_type": type(error).__name__},
            )
            self._set_health_state("failed", ("warmup",))
            return

        errors = tuple(
            component for component in ("embed", "rerank") if f"{component}_error" in result
        )
        if len(errors) == 2:
            state: HealthState = "failed"
        elif errors or not self.models_loaded:
            state = "degraded"
        else:
            state = "ready"
        self._set_health_state(state, errors)

        if state != "failed" and self.enable_watcher:
            self._start_watcher()
        logger.info("model_warmup_finished", extra={"state": state})

    def _setup_signal_handlers(self) -> None:
        """Configura handlers para SIGTERM e SIGINT."""

        def signal_handler(signum: int, frame: Any) -> None:
            sig_name = signal.Signals(signum).name
            logger.info(f"Sinal {sig_name} recebido, iniciando shutdown...")
            request_shutdown()

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

    def start(self, blocking: bool = True) -> None:
        """
        Inicia o servidor daemon.

        Args:
            blocking: Se True, bloqueia até shutdown. Se False, roda em thread.
        """
        logger.info(f"Iniciando daemon em {self.host}:{self.port}")

        # Configurar signal handlers
        self._setup_signal_handlers()

        # Criar servidor HTTP antes de warmup/watcher para falhar rápido
        # em caso de porta ocupada (evita custo de carregar modelos à toa).
        try:
            handler = type(
                "BoundDaemonRequestHandler",
                (DaemonRequestHandler,),
                {"daemon_server": self},
            )
            self._server = HTTPServer((self.host, self.port), handler)
            self.port = int(self._server.server_address[1])
        except OSError as exc:
            if exc.errno == EADDRINUSE:
                self._set_health_state("failed", ("bind",))
                logger.warning(
                    "Porta %s já está em uso em %s. "
                    "Outro daemon pode estar rodando; encerrando esta instância.",
                    self.port,
                    self.host,
                )
                return
            raise

        self._server.timeout = 1.0  # Permite verificar shutdown periodicamente

        if blocking:
            self._init_thread = threading.Thread(
                target=self._initialize_runtime,
                name="daemon-runtime-init",
                daemon=True,
            )
            self._init_thread.start()
            self._serve_forever()
        else:
            self._server_thread = threading.Thread(
                target=self._serve_forever,
                name="daemon-http-server",
                daemon=True,
            )
            self._server_thread.start()
            self._initialize_runtime()

    def _start_watcher(self) -> None:
        """Inicia o file watcher para monitorar o vault."""
        try:
            from vault_search.config.paths import VAULT_PATH
            from vault_search.core.indexer import VaultIndexer
            from vault_search.server.watcher import VaultWatcher

            if not VAULT_PATH.exists():
                logger.warning("watcher_disabled", extra={"reason": "vault_unavailable"})
                return

            self._indexer = VaultIndexer()
            self._watcher = VaultWatcher(
                self._indexer,
                on_reindex=self._on_reindex_callback,
            )
            self._watcher.start()
            logger.info("watcher_started")
        except Exception as error:
            logger.error(
                "watcher_start_failed",
                extra={"error_type": type(error).__name__},
            )
            self._watcher = None

    def _on_reindex_callback(self) -> None:
        """Callback chamado após cada reindex."""
        self.reindex_count += 1

    def _serve_forever(self) -> None:
        """Loop principal do servidor."""
        if self._server is None:
            return

        while not shutdown_requested():
            self._server.handle_request()

        logger.info("Shutdown iniciado...")

        # Parar watcher primeiro
        if self._watcher:
            logger.info("Parando watcher...")
            self._watcher.stop()
            self._watcher = None

        self._server.server_close()
        if self._init_thread and self._init_thread.is_alive():
            self._init_thread.join(timeout=5.0)
        self.models.cleanup()
        logger.info("Daemon finalizado")

    def stop(self) -> None:
        """Para o servidor."""
        # Parar watcher primeiro
        if self._watcher:
            self._watcher.stop()
            self._watcher = None

        request_shutdown()
        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=5.0)
        if self._init_thread and self._init_thread.is_alive():
            self._init_thread.join(timeout=5.0)


def main() -> None:
    """Entry point do daemon."""
    import os
    import sys

    # Marcar que estamos rodando como daemon (evita deadlock no ModelManager)
    os.environ["VAULT_SEARCH_RUNNING_AS_DAEMON"] = "1"

    # Configurar logging
    log_handler = logging.StreamHandler(sys.stderr)
    log_handler.addFilter(PrivacyFilter())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[log_handler],
        force=True,
    )

    # Inicializar shutdown manager
    ShutdownManager.initialize(timeout=30.0)

    # Iniciar servidor
    server = DaemonServer()

    try:
        server.start(blocking=True)
    except KeyboardInterrupt:
        logger.info("Interrompido pelo usuário")
    finally:
        ShutdownManager.shutdown()


if __name__ == "__main__":
    main()
