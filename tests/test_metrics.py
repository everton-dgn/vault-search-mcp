"""
Tests for performance metrics.
"""

import time

from vault_search.utils.metrics import (
    CACHE_HIT_RATE_THRESHOLD,
    LATENCY_THRESHOLD_MS,
    MetricsCollector,
    OperationMetrics,
    check_cache_health,
    check_latency_health,
    get_metrics,
    reset_metrics,
    timed,
)


class TestOperationMetrics:
    """Tests for OperationMetrics."""

    def test_record_latency(self):
        metrics = OperationMetrics(name="test")
        metrics.record(10.0)
        metrics.record(20.0)
        assert metrics.count == 2

    def test_p50_single_value(self):
        metrics = OperationMetrics(name="test")
        metrics.record(10.0)
        assert metrics.p50 == 10.0

    def test_p50_multiple_values(self):
        metrics = OperationMetrics(name="test")
        for i in range(1, 11):
            metrics.record(float(i))
        # Mediana of 1-10 is 5.5
        assert metrics.p50 == 5.5

    def test_p95_few_samples(self):
        metrics = OperationMetrics(name="test")
        metrics.record(5.0)
        metrics.record(10.0)
        # With few samples, return the maximum.
        assert metrics.p95 == 10.0

    def test_p95_many_samples(self):
        metrics = OperationMetrics(name="test")
        for i in range(1, 101):
            metrics.record(float(i))
        # P95 of 1-100 ~= 95
        assert metrics.p95 >= 90

    def test_mean(self):
        metrics = OperationMetrics(name="test")
        metrics.record(10.0)
        metrics.record(20.0)
        assert metrics.mean == 15.0

    def test_empty_metrics(self):
        metrics = OperationMetrics(name="test")
        assert metrics.count == 0
        assert metrics.p50 == 0.0
        assert metrics.p95 == 0.0
        assert metrics.mean == 0.0

    def test_max_samples_limit(self):
        metrics = OperationMetrics(name="test", _max_samples=10)
        for i in range(20):
            metrics.record(float(i))
        # Must keep only lasts 10
        assert metrics.count == 10
        # Lasts 10 are 10-19, average = 14.5
        assert metrics.mean == 14.5

    def test_summary(self):
        metrics = OperationMetrics(name="test")
        metrics.record(10.0)
        summary = metrics.summary()
        assert summary["name"] == "test"
        assert summary["count"] == 1
        assert "p50_ms" in summary
        assert "p95_ms" in summary
        assert "mean_ms" in summary


class TestMetricsCollector:
    """Tests for MetricsCollector."""

    def setup_method(self):
        reset_metrics()

    def test_singleton(self):
        c1 = MetricsCollector()
        c2 = MetricsCollector()
        assert c1 is c2

    def test_measure_context_manager(self):
        collector = MetricsCollector()
        with collector.measure("test_op"):
            time.sleep(0.01)  # 10ms

        summary = collector.summary()
        assert "test_op" in summary
        assert summary["test_op"]["count"] == 1
        # Must have recorded a value greater than zero.
        assert summary["test_op"]["p50_ms"] > 0

    def test_record_direct(self):
        collector = MetricsCollector()
        collector.record("manual_op", 5.5)

        summary = collector.summary()
        assert summary["manual_op"]["count"] == 1
        assert summary["manual_op"]["p50_ms"] == 5.5

    def test_multiple_operations(self):
        collector = MetricsCollector()
        collector.record("op1", 10.0)
        collector.record("op2", 20.0)

        summary = collector.summary()
        assert "op1" in summary
        assert "op2" in summary

    def test_reset(self):
        collector = MetricsCollector()
        collector.record("test", 10.0)
        collector.reset()

        summary = collector.summary()
        assert len(summary) == 0

    def test_disable_enable(self):
        collector = MetricsCollector()
        collector.disable()
        collector.record("test", 10.0)

        summary = collector.summary()
        assert len(summary) == 0

        collector.enable()
        collector.record("test", 10.0)

        summary = collector.summary()
        assert "test" in summary


class TestTimedDecorator:
    """Tests for the @timed decorator."""

    def setup_method(self):
        reset_metrics()

    def test_timed_with_name(self):
        @timed("custom_name")
        def my_func():
            return 42

        result = my_func()

        assert result == 42
        summary = get_metrics()
        assert "custom_name" in summary
        assert summary["custom_name"]["count"] == 1

    def test_timed_auto_name(self):
        @timed()
        def auto_named_func():
            return 123

        result = auto_named_func()

        assert result == 123
        summary = get_metrics()
        assert "auto_named_func" in summary

    def test_timed_multiple_calls(self):
        @timed("repeat")
        def repeated():
            pass

        for _ in range(5):
            repeated()

        summary = get_metrics()
        assert summary["repeat"]["count"] == 5


class TestGlobalFunctions:
    """Tests for module-level functions."""

    def setup_method(self):
        reset_metrics()

    def test_get_metrics_empty(self):
        summary = get_metrics()
        assert summary == {}

    def test_get_metrics_after_recording(self):
        collector = MetricsCollector()
        collector.record("op", 100.0)

        summary = get_metrics()
        assert "op" in summary

    def test_reset_metrics(self):
        collector = MetricsCollector()
        collector.record("op", 100.0)
        reset_metrics()

        summary = get_metrics()
        assert summary == {}


class TestHealthChecks:
    """Tests for functions of health check."""

    def setup_method(self):
        reset_metrics()

    def test_thresholds_exist(self):
        """Check that thresholds are defined."""
        assert LATENCY_THRESHOLD_MS > 0
        assert 0 < CACHE_HIT_RATE_THRESHOLD < 1

    def test_check_latency_health_in_alerts(self):
        """No metrics produce no alerts."""
        alerts = check_latency_health()
        assert alerts == []

    def test_check_latency_health_below_threshold(self):
        """Latency below the threshold does not generate an alert."""
        collector = MetricsCollector()
        # Register at least 20 samples so p95 is meaningful.
        for _ in range(30):
            collector.record("fast_op", 100.0)  # 100ms < 500ms threshold

        alerts = check_latency_health()
        assert alerts == []

    def test_check_latency_health_above_threshold(self):
        """Latency above the threshold generates an alert."""
        collector = MetricsCollector()
        # Register at least 20 high values so p95 exceeds the threshold.
        for _ in range(30):
            collector.record("slow_op", 600.0)  # 600ms > 500ms threshold

        alerts = check_latency_health()
        assert len(alerts) == 1
        assert alerts[0]["type"] == "high_latency"
        assert alerts[0]["operation"] == "slow_op"
        assert alerts[0]["severity"] == "warning"
        assert alerts[0]["p95_ms"] >= LATENCY_THRESHOLD_MS

    def test_check_latency_health_multiple_ops(self):
        """Checks multiple operations."""
        collector = MetricsCollector()
        for _ in range(30):
            collector.record("fast_op", 100.0)  # OK
            collector.record("slow_op", 700.0)  # Problema

        alerts = check_latency_health()
        # Only slow_op must generate an alert.
        assert len(alerts) == 1
        assert alerts[0]["operation"] == "slow_op"

    def test_check_cache_health_returns_list(self):
        """check_cache_health returns a list, which may be empty before initialization."""
        alerts = check_cache_health()
        assert isinstance(alerts, list)

    def test_check_cache_health_graceful_failure(self):
        """check_cache_health does not raise when the searcher or cache is absent."""
        # Must not raise exception
        alerts = check_cache_health()
        # Return an empty list or valid alerts.
        assert isinstance(alerts, list)
        for alert in alerts:
            assert "type" in alert
            assert "severity" in alert
