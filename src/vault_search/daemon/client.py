"""
Cliente HTTP para conectar ao daemon de modelos.

O MCP server usa este cliente para delegar embed/rerank ao daemon
quando ele está rodando, evitando carregar modelos localmente.
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


def _models_are_loaded(loaded: Any) -> bool:
    """Normaliza os contratos booleano e detalhado de versões anteriores."""
    if isinstance(loaded, dict):
        return bool(loaded.get("embed_model") and loaded.get("reranker_model"))
    return bool(loaded)


def _health_is_ready(health: dict[str, Any]) -> bool:
    """Aceita o contrato atual e a resposta legada ``healthy``."""
    return health.get("status") in _READY_STATES and _models_are_loaded(health.get("models_loaded"))


def is_daemon_running(
    host: str | None = None,
    port: int | None = None,
    timeout: float = 5.0,
    retries: int = 3,
) -> bool:
    """
    Verifica se o daemon está rodando.

    Faz uma requisição de health check com retry para evitar
    falsos negativos quando o daemon está ocupado.

    Args:
        host: Host do daemon
        port: Porta do daemon
        timeout: Timeout de conexão em segundos (default: 5.0)
        retries: Número de tentativas (default: 3)
    """
    daemon_config = get_config().daemon
    effective_host = host or daemon_config.host
    effective_port = port or daemon_config.port

    for attempt in range(retries):
        try:
            client = DaemonClient(effective_host, effective_port, timeout)
            if _health_is_ready(client.health()):
                return True
        except ConnectionError, TimeoutError, OSError, RuntimeError, ValueError:
            if attempt < retries - 1:
                time.sleep(0.5)  # Espera 500ms entre tentativas
    return False


class DaemonClient:
    """
    Cliente para o daemon de modelos.

    Implementa a mesma interface do ModelManager para ser usado
    como drop-in replacement quando o daemon está disponível.
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
            raise ValueError("O cliente do daemon aceita apenas host de loopback")
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
        """Faz uma requisição HTTP ao daemon."""
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
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                payload = response.read(MAX_DAEMON_RESPONSE_BYTES + 1)
                if len(payload) > MAX_DAEMON_RESPONSE_BYTES:
                    raise ValueError("Resposta do daemon excede o limite")
                result = json.loads(payload.decode("utf-8"))
                if not isinstance(result, dict):
                    raise ValueError("Resposta inválida do daemon")
                return result
        except urllib.error.HTTPError as e:
            if e.code >= 500:
                self.invalidate()
            raise RuntimeError(f"Daemon request failed (HTTP {e.code})") from e
        except urllib.error.URLError as e:
            self.invalidate()
            raise ConnectionError("Não foi possível conectar ao daemon") from e
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
            self.invalidate()
            raise RuntimeError("Daemon retornou uma resposta inválida") from error

    def invalidate(self) -> None:
        """Invalida o último probe para permitir reconexão imediata."""
        self._available = False
        self._last_availability_check = 0.0

    def is_available(self, *, force: bool = False) -> bool:
        """Verifica readiness com cache curto e revalidação explícita."""
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
        """Health check do daemon."""
        return self._request("GET", "/health")

    def stats(self) -> dict[str, Any]:
        """Estatísticas do daemon."""
        return self._request("GET", "/stats")

    def embed_queries(self, texts: list[str]) -> np.ndarray:
        """
        Embed queries via daemon.

        Equivalente a ModelManager.embed_queries().
        """
        if not texts:
            return np.array([])

        response = self._request("POST", "/embed/queries", {"texts": texts})
        embeddings = response.get("embeddings", [])
        return np.array(embeddings, dtype=np.float32)

    def embed_corpus(self, texts: list[str]) -> np.ndarray:
        """
        Embed corpus via daemon.

        Equivalente a ModelManager.embed_corpus().
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
        Rerank via daemon.

        Equivalente a ModelManager.rerank().

        Returns:
            Lista de (index, score) ordenada por score decrescente.
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
        # scores já vem como lista de [index, score]
        return [(int(s[0]), float(s[1])) for s in scores]

    def is_loaded(self) -> bool:
        """Verifica se os modelos estão carregados no daemon."""
        try:
            health = self.health()
            return _models_are_loaded(health.get("models_loaded"))
        except Exception:
            return False

    def warmup(self) -> None:
        """
        Warmup - no cliente, apenas verifica se daemon está pronto.

        Os modelos são carregados no daemon, não no cliente.
        """
        if not self.is_available(force=True):
            raise ConnectionError("Daemon ainda não está pronto")

    def cleanup(self) -> None:
        """
        Cleanup - no cliente, não faz nada.

        O daemon mantém os modelos carregados.
        """
        pass


class HybridModelManager:
    """
    Model manager híbrido que usa daemon se disponível, senão carrega local.

    Uso:
        models = HybridModelManager()
        # Automaticamente usa daemon se rodando, senão carrega localmente
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
        """Retorna o backend a usar (daemon ou local)."""
        daemon_available = self._daemon_client.is_available(force=self._use_daemon is True)
        if self._use_daemon is None or self._use_daemon != daemon_available:
            self._use_daemon = daemon_available
            if self._use_daemon:
                logger.info("Usando daemon para modelos")
            else:
                logger.info("Daemon não disponível, carregando modelos localmente")

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
        """Verifica se modelos estão carregados."""
        return self._get_backend().is_loaded()

    def warmup(self) -> None:
        """Warmup dos modelos."""
        self._get_backend().warmup()

    def cleanup(self) -> None:
        """Cleanup."""
        self._get_backend().cleanup()

    @property
    def using_daemon(self) -> bool:
        """Retorna True se está usando o daemon."""
        return self._use_daemon is True
