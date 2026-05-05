"""Prometheus metrics.

Falls back to no-op counters when ``prometheus_client`` is not installed,
so the rest of the app stays usable in lean dev environments.
"""
from __future__ import annotations

from typing import Iterable

try:
    from prometheus_client import (  # type: ignore[import-not-found]
        CONTENT_TYPE_LATEST,
        Counter,
        Histogram,
        generate_latest,
    )

    HTTP_REQUESTS = Counter(
        "http_requests_total",
        "HTTP requests by method, path template, and status.",
        ["method", "path", "status"],
    )
    HTTP_LATENCY = Histogram(
        "http_request_duration_seconds",
        "HTTP request latency in seconds.",
        ["method", "path"],
    )
    LLM_CALLS = Counter(
        "llm_calls_total",
        "LLM calls by provider and outcome.",
        ["provider", "kind", "outcome"],
    )
    RETRIEVAL_CALLS = Counter(
        "retrieval_calls_total",
        "Retrievals by leg outcome.",
        ["leg", "outcome"],
    )

    def render() -> tuple[bytes, str]:
        return generate_latest(), CONTENT_TYPE_LATEST

    AVAILABLE = True
except ImportError:  # pragma: no cover
    AVAILABLE = False

    class _Noop:
        def labels(self, *_: str, **__: str) -> "_Noop":
            return self

        def inc(self, _: float = 1.0) -> None:
            pass

        def observe(self, _: float) -> None:
            pass

    HTTP_REQUESTS = _Noop()  # type: ignore[assignment]
    HTTP_LATENCY = _Noop()  # type: ignore[assignment]
    LLM_CALLS = _Noop()  # type: ignore[assignment]
    RETRIEVAL_CALLS = _Noop()  # type: ignore[assignment]

    def render() -> tuple[bytes, str]:  # pragma: no cover
        return (
            b"# Prometheus client is not installed; metrics are disabled.\n",
            "text/plain; charset=utf-8",
        )


def labels_for_path(template: str, blacklist: Iterable[str] = ("/api/metrics",)) -> str:
    """Normalize a request path so route param values don't explode the cardinality."""
    if template in blacklist:
        return template
    return template
