"""
Observability: Structured Logging & Prometheus Metrics (Phase 9).
"""
import structlog
from prometheus_client import Histogram, Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST
from fastapi import Response


def configure_logging():
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(structlog.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


logger = structlog.get_logger()

# Prometheus Metrics (Phase 9.3)
MATCH_LATENCY = Histogram(
    "matching_duration_seconds",
    "Time taken to find rescue candidates",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

ESCROW_DISPUTES = Counter(
    "escrow_disputes_total",
    "Total disputed escrows",
)

INCIDENTS_TOTAL = Counter(
    "incidents_total",
    "Total incidents reported",
    ["cargo_class"],
)

ACTIVE_WEBSOCKETS = Gauge(
    "active_websockets_count",
    "Active real-time WebSocket connections",
)


def metrics_endpoint_response() -> Response:
    """Return raw Prometheus metrics for /metrics scraping."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
