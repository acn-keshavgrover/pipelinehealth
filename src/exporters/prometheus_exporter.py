"""
Prometheus metrics registry for PipelineHealth.
Exposes CI/CD metrics in Prometheus format.
"""

from prometheus_client import Gauge, Counter, Histogram, Info


class MetricsRegistry:
    """Centralized Prometheus metrics for all CI/CD sources."""

    def __init__(self):
        # Build counts
        self.build_total = Gauge(
            "pipelinehealth_builds_total",
            "Total number of builds/pipelines collected",
            ["source"]
        )
        self.build_failures = Gauge(
            "pipelinehealth_build_failures_total",
            "Total number of failed builds/pipelines",
            ["source"]
        )
        self.build_success_rate = Gauge(
            "pipelinehealth_build_success_rate",
            "Build success rate (0-100)",
            ["source"]
        )

        # Duration metrics
        self.build_duration = Gauge(
            "pipelinehealth_build_duration_avg_seconds",
            "Average build duration in seconds",
            ["source"]
        )
        self.build_duration_histogram = Histogram(
            "pipelinehealth_build_duration_seconds",
            "Build duration distribution",
            ["source"],
            buckets=[30, 60, 120, 300, 600, 900, 1200, 1800, 3600]
        )

        # Failure classification
        self.failure_by_cause = Counter(
            "pipelinehealth_failures_by_cause_total",
            "Failures classified by root cause",
            ["source", "cause"]
        )

        # Collection metadata
        self.last_collection = Gauge(
            "pipelinehealth_last_collection_timestamp",
            "Unix timestamp of last successful data collection",
            ["source"]
        )
        self.collection_errors = Counter(
            "pipelinehealth_collection_errors_total",
            "Number of data collection errors",
            ["source"]
        )

        # App info
        self.app_info = Info(
            "pipelinehealth",
            "PipelineHealth application metadata"
        )
        self.app_info.info({
            "version": "1.2.0",
            "author": "keshav.grover"
        })

    def update_build_total(self, source: str, count: int):
        """Update total build count for a source."""
        self.build_total.labels(source=source).set(count)

    def update_build_failures(self, source: str, count: int):
        """Update failure count and recalculate success rate."""
        self.build_failures.labels(source=source).set(count)
        total = self.build_total.labels(source=source)._value.get()
        if total > 0:
            rate = ((total - count) / total) * 100
            self.build_success_rate.labels(source=source).set(round(rate, 2))

    def update_avg_duration(self, source: str, duration_sec: float):
        """Update average build duration."""
        self.build_duration.labels(source=source).set(duration_sec)

    def record_build_duration(self, source: str, duration_sec: float):
        """Record a single build duration in the histogram."""
        self.build_duration_histogram.labels(source=source).observe(duration_sec)

    def record_failure_cause(self, source: str, cause: str):
        """Increment failure cause counter."""
        self.failure_by_cause.labels(source=source, cause=cause).inc()

    def record_collection_time(self, source: str, timestamp: float):
        """Record last successful collection timestamp."""
        self.last_collection.labels(source=source).set(timestamp)

    def record_collection_error(self, source: str):
        """Increment collection error counter."""
        self.collection_errors.labels(source=source).inc()
