"""
HTTP client for the local model daemon.

The MCP server uses this client to delegate embedding and reranking when the
daemon is ready, avoiding a second local model load.
"""

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

import numpy as np

from vault_search.config.loader import get_config
from vault_search.utils.network import format_url_host, is_loopback_host

logger = logging.getLogger(__name__)

_READY_STATES = frozenset({"ready", "healthy"})
MAX_DAEMON_RESPONSE_BYTES = 64 * 1024 * 1024


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Keep daemon requests on the original loopback endpoint."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _open_loopback(request: urllib.request.Request, *, timeout: float) -> Any:
    """Open a loopback request without consulting environment proxy settings."""
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _RejectRedirects(),
    )
    return opener.open(request, timeout=timeout)


def _models_are_loaded(loaded: Any) -> bool:
    """Normalize legacy boolean and current detailed readiness contracts."""
    if isinstance(loaded, dict):
        return loaded.get("embed_model") is True and loaded.get("reranker_model") is True
    return type(loaded) is bool and loaded


def _health_is_ready(health: dict[str, Any], expected_pid: int | None = None) -> bool:
    """Validate readiness and, when supplied, bind it to one daemon process."""
    if expected_pid is None:
        return health.get("status") in _READY_STATES and _models_are_loaded(
            health.get("models_loaded")
        )
    return (
        health.get("status") == "ready"
        and _models_are_loaded(health.get("models_loaded"))
        and type(health.get("pid")) is int
        and health.get("pid") == expected_pid
    )


def is_daemon_running(
    host: str | None = None,
    port: int | None = None,
    timeout: float = 5.0,
    retries: int = 3,
    expected_pid: int | None = None,
) -> bool:
    """
    Check whether the daemon is ready.

    Retry a bounded health check to reduce false negatives while the daemon is
    briefly busy.

    Args:
        host: daemon host
        port: daemon port
        timeout: connection timeout in seconds
        retries: number of attempts
        expected_pid: require the ready response to belong to this process ID
    """
    if expected_pid is not None and (
        isinstance(expected_pid, bool) or not isinstance(expected_pid, int) or expected_pid <= 0
    ):
        raise ValueError("expected_pid must be a positive integer")

    daemon_config = get_config().daemon
    effective_host = host or daemon_config.host
    effective_port = port or daemon_config.port

    for attempt in range(retries):
        try:
            client = DaemonClient(effective_host, effective_port, timeout)
            if _health_is_ready(client.health(), expected_pid):
                return True
        except ConnectionError, TimeoutError, OSError, RuntimeError, ValueError:
            if attempt < retries - 1:
                time.sleep(0.5)  # Wait 500 ms between attempts.
    return False


class DaemonClient:
    """
    Client for the local model daemon.

    Implements the ModelManager interface for use as a drop-in backend when
    the daemon is available.
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        timeout: float | None = None,
        availability_ttl: float = 2.0,
    ):
        daemon_config = get_config().daemon
        self.host = host or daemon_config.host
        if not is_loopback_host(self.host):
            raise ValueError("The daemon client accepts only a loopback host")
        self.port = port or daemon_config.port
        self.timeout = timeout or daemon_config.timeout
        self.base_url = f"http://{format_url_host(self.host)}:{self.port}"
        self.availability_ttl = max(0.0, availability_ttl)
        self._available: bool | None = None
        self._last_availability_check = 0.0

    def _request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send an HTTP request to the daemon."""
        url = f"{self.base_url}{path}"

        if data is not None:
            body = json.dumps(data).encode("utf-8")
            headers = {"Content-Type": "application/json"}
        else:
            body = None
            headers = {}

        req = urllib.request.Request(
            url,
            data=body,
            headers=headers,
            method=method,
        )

        try:
            with _open_loopback(req, timeout=self.timeout) as response:
                payload = response.read(MAX_DAEMON_RESPONSE_BYTES + 1)
                if len(payload) > MAX_DAEMON_RESPONSE_BYTES:
                    raise ValueError("Daemon response exceeds the size limit")
                result = json.loads(payload.decode("utf-8"))
                if not isinstance(result, dict):
                    raise ValueError("Daemon response is invalid")
                return result
        except urllib.error.HTTPError as e:
            if e.code >= 500:
                self.invalidate()
            raise RuntimeError(f"Daemon request failed (HTTP {e.code})") from e
        except urllib.error.URLError as e:
            self.invalidate()
            raise ConnectionError("Could not connect to the daemon") from e
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
            self.invalidate()
            raise RuntimeError("Daemon returned an invalid response") from error

    def invalidate(self) -> None:
        """Invalidate the last probe so the next call reconnects immediately."""
        self._available = False
        self._last_availability_check = 0.0

    def is_available(self, *, force: bool = False) -> bool:
        """Check readiness with a short cache and explicit forced refresh."""
        now = time.monotonic()
        cache_is_fresh = (
            self._available is not None
            and now - self._last_availability_check < self.availability_ttl
        )
        if cache_is_fresh and not force:
            return bool(self._available)

        try:
            self._available = _health_is_ready(self.health())
        except ConnectionError, TimeoutError, OSError, RuntimeError, ValueError:
            self._available = False
        self._last_availability_check = now
        return bool(self._available)

    def health(self) -> dict[str, Any]:
        """Return the daemon health snapshot."""
        return self._request("GET", "/health")

    def stats(self) -> dict[str, Any]:
        """Return daemon statistics."""
        return self._request("GET", "/stats")

    def embed_queries(self, texts: list[str]) -> np.ndarray:
        """
        Embed queries through the daemon.

        Equivalent to ModelManager.embed_queries().
        """
        if not texts:
            return np.array([])

        response = self._request("POST", "/embed/queries", {"texts": texts})
        embeddings = response.get("embeddings", [])
        return np.array(embeddings, dtype=np.float32)

    def embed_corpus(self, texts: list[str]) -> np.ndarray:
        """
        Embed corpus text through the daemon.

        Equivalent to ModelManager.embed_corpus().
        """
        if not texts:
            return np.array([])

        response = self._request("POST", "/embed/corpus", {"texts": texts})
        embeddings = response.get("embeddings", [])
        return np.array(embeddings, dtype=np.float32)

    def rerank(
        self,
        query: str,
        texts: list[str],
        top_k: int = 10,
    ) -> list[tuple[int, float]]:
        """
        Rerank through the daemon.

        Equivalent to ModelManager.rerank().

        Returns:
            List of (index, score) pairs ordered by descending score.
        """
        if not texts:
            return []

        response = self._request(
            "POST",
            "/rerank",
            {
                "query": query,
                "texts": texts,
                "top_k": top_k,
            },
        )

        scores = response.get("scores", [])
        # Scores arrive as [index, score] pairs.
        return [(int(s[0]), float(s[1])) for s in scores]

    def is_loaded(self) -> bool:
        """Return whether both daemon models are loaded."""
        try:
            health = self.health()
            return _models_are_loaded(health.get("models_loaded"))
        except Exception:
            return False

    def warmup(self) -> None:
        """
        Verify daemon readiness.

        Models are loaded in the daemon rather than this client.
        """
        if not self.is_available(force=True):
            raise ConnectionError("Daemon is not ready yet")

    def cleanup(self) -> None:
        """
        Perform no client-side cleanup.

        The daemon owns and retains the loaded models.
        """
        pass


class HybridModelManager:
    """
    Model manager that prefers the daemon and falls back to local models.

    Example:
        models = HybridModelManager()
        # Automatically select a ready daemon or local models.
        embeddings = models.embed_queries(["query"])
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        timeout: float | None = None,
    ):
        self._daemon_client = DaemonClient(host, port, timeout)
        self._local_models: Any = None  # Lazy load
        self._use_daemon: bool | None = None

    def _get_backend(self) -> DaemonClient | Any:
        """Return the active daemon or local backend."""
        daemon_available = self._daemon_client.is_available(force=self._use_daemon is True)
        if self._use_daemon is None or self._use_daemon != daemon_available:
            self._use_daemon = daemon_available
            if self._use_daemon:
                logger.info("model_backend selected=daemon")
            else:
                logger.info("model_backend selected=local")

        if self._use_daemon:
            return self._daemon_client
        else:
            if self._local_models is None:
                from vault_search.core.models import ModelManager

                self._local_models = ModelManager()
            return self._local_models

    def embed_queries(self, texts: list[str]) -> np.ndarray:
        """Embed queries."""
        return self._get_backend().embed_queries(texts)

    def embed_corpus(self, texts: list[str]) -> np.ndarray:
        """Embed corpus."""
        return self._get_backend().embed_corpus(texts)

    def rerank(
        self,
        query: str,
        texts: list[str],
        top_k: int = 10,
    ) -> list[tuple[int, float]]:
        """Rerank."""
        return self._get_backend().rerank(query, texts, top_k=top_k)

    def is_loaded(self) -> bool:
        """Return whether the selected backend has loaded its models."""
        return self._get_backend().is_loaded()

    def warmup(self) -> None:
        """Warm up the selected backend."""
        self._get_backend().warmup()

    def cleanup(self) -> None:
        """Cleanup."""
        self._get_backend().cleanup()

    @property
    def using_daemon(self) -> bool:
        """Return true when the daemon backend is active."""
        return self._use_daemon is True
