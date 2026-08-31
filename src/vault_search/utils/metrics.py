"""
Instrumentação de métricas para operações de CRUD.

Coleta latências p50/p95 para identificar gargalos.
"""

import functools
import logging
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from types import TracebackType
from typing import Literal, ParamSpec, Self, TypedDict, TypeVar

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")


class MetricSummary(TypedDict):
    """Resumo serializável de uma operação medida."""

    name: str
    count: int
    p50_ms: float
    p95_ms: float
    mean_ms: float


type HealthAlert = dict[str, str | float]


@dataclass
class OperationMetrics:
    """Métricas coletadas para uma operação."""

    name: str
    latencies_ms: list[float] = field(default_factory=list)
    _max_samples: int = 1000

    def record(self, latency_ms: float) -> None:
        """Registra uma latência, mantendo últimas N amostras."""
        self.latencies_ms.append(latency_ms)
        if len(self.latencies_ms) > self._max_samples:
            self.latencies_ms = self.latencies_ms[-self._max_samples :]

    @property
    def count(self) -> int:
        return len(self.latencies_ms)

    @property
    def p50(self) -> float:
        """Mediana (percentil 50)."""
        if not self.latencies_ms:
            return 0.0
        return statistics.median(self.latencies_ms)

    @property
    def p95(self) -> float:
        """Percentil 95."""
        if not self.latencies_ms:
            return 0.0
        if len(self.latencies_ms) < 20:
            return max(self.latencies_ms)
        return statistics.quantiles(self.latencies_ms, n=20)[18]

    @property
    def mean(self) -> float:
        """Média."""
        if not self.latencies_ms:
            return 0.0
        return statistics.mean(self.latencies_ms)

    def summary(self) -> MetricSummary:
        """Retorna resumo das métricas."""
        return {
            "name": self.name,
            "count": self.count,
            "p50_ms": round(self.p50, 2),
            "p95_ms": round(self.p95, 2),
            "mean_ms": round(self.mean, 2),
        }


class MetricsCollector:
    """
    Coletor singleton de métricas de operações.

    Uso:
        collector = MetricsCollector()
        with collector.measure("list_notes"):
            # operação
        print(collector.summary())
    """

    _instance: MetricsCollector | None = None
    _metrics: dict[str, OperationMetrics]
    _enabled: bool

    def __new__(cls) -> MetricsCollector:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._metrics = {}
            cls._instance._enabled = True
        return cls._instance

    def _get_or_create(self, name: str) -> OperationMetrics:
        if name not in self._metrics:
            self._metrics[name] = OperationMetrics(name=name)
        return self._metrics[name]

    def measure(self, operation_name: str) -> _MeasureContext:
        """Context manager para medir latência de uma operação."""
        return _MeasureContext(self, operation_name)

    def record(self, operation_name: str, latency_ms: float) -> None:
        """Registra latência diretamente."""
        if self._enabled:
            self._get_or_create(operation_name).record(latency_ms)

    def summary(self) -> dict[str, MetricSummary]:
        """Retorna resumo de todas as métricas coletadas."""
        return {name: metrics.summary() for name, metrics in self._metrics.items()}

    def reset(self) -> None:
        """Limpa todas as métricas coletadas."""
        self._metrics.clear()

    def enable(self) -> None:
        """Habilita coleta de métricas."""
        self._enabled = True

    def disable(self) -> None:
        """Desabilita coleta de métricas."""
        self._enabled = False


class _MeasureContext:
    """Context manager para medição de latência."""

    def __init__(self, collector: MetricsCollector, name: str):
        self._collector = collector
        self._name = name
        self._start: float = 0.0

    def __enter__(self) -> Self:
        self._start = time.perf_counter()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> Literal[False]:
        elapsed_ms = (time.perf_counter() - self._start) * 1000
        self._collector.record(self._name, elapsed_ms)
        return False


def timed(
    operation_name: str | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    Decorador para medir latência de funções.

    Uso:
        @timed("list_notes")
        def list_notes(...):
            ...

        # Ou usa nome da função automaticamente:
        @timed()
        def list_notes(...):
            ...
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        name = operation_name or func.__name__
        collector = MetricsCollector()

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            with collector.measure(name):
                return func(*args, **kwargs)

        return wrapper

    return decorator


# Instância global para acesso conveniente
_collector = MetricsCollector()


def get_metrics() -> dict[str, MetricSummary]:
    """Retorna métricas coletadas."""
    return _collector.summary()


def reset_metrics() -> None:
    """Limpa métricas coletadas."""
    _collector.reset()


# Thresholds para alertas de saúde
LATENCY_THRESHOLD_MS = 500  # p95 > 500ms = warning
CACHE_HIT_RATE_THRESHOLD = 0.7  # hit rate < 70% = warning


def check_latency_health() -> list[HealthAlert]:
    """
    Verifica latência das operações e retorna alertas se p95 exceder threshold.

    Retorna:
        Lista de alertas (vazia se tudo OK).
    """
    alerts: list[HealthAlert] = []
    metrics = _collector.summary()

    for name, data in metrics.items():
        p95 = data.get("p95_ms", 0)
        if p95 > LATENCY_THRESHOLD_MS:
            alerts.append(
                {
                    "type": "high_latency",
                    "operation": name,
                    "p95_ms": p95,
                    "threshold_ms": LATENCY_THRESHOLD_MS,
                    "severity": "warning",
                }
            )

    return alerts


def check_cache_health() -> list[HealthAlert]:
    """
    Verifica saúde dos caches e retorna alertas se hit rate for baixo.

    Retorna:
        Lista de alertas (vazia se tudo OK).
    """
    alerts: list[HealthAlert] = []

    # Verificar cache de embeddings do searcher
    try:
        from vault_search.core.searcher import VaultSearcher

        searcher = VaultSearcher()
        embedding_stats = searcher.get_embedding_cache_stats()
        hit_rate = embedding_stats.get("hit_rate", 1.0)

        if hit_rate < CACHE_HIT_RATE_THRESHOLD:
            alerts.append(
                {
                    "type": "low_cache_hit_rate",
                    "cache": "embedding_cache",
                    "hit_rate": round(hit_rate, 2),
                    "threshold": CACHE_HIT_RATE_THRESHOLD,
                    "severity": "warning",
                }
            )
    except Exception:
        pass  # Searcher pode não estar inicializado ainda

    # Verificar cache de metadados
    try:
        from vault_search.crud.cache import get_metadata_cache

        metadata_cache = get_metadata_cache()
        metadata_stats = metadata_cache.stats()
        hit_rate = metadata_stats.get("hit_rate", 1.0)

        if hit_rate < CACHE_HIT_RATE_THRESHOLD:
            alerts.append(
                {
                    "type": "low_cache_hit_rate",
                    "cache": "metadata_cache",
                    "hit_rate": round(hit_rate, 2),
                    "threshold": CACHE_HIT_RATE_THRESHOLD,
                    "severity": "warning",
                }
            )
    except Exception:
        pass  # Cache pode não estar inicializado ainda

    return alerts
