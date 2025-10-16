"""
Observability Module

Provides metrics, tracing, and monitoring capabilities:
- Prometheus metrics
- OpenTelemetry tracing
- Grafana dashboards
- Alert rules
"""

from .metrics import (
    observe_request_latency,
    observe_cache_hit,
    observe_cache_miss,
    observe_cache_operation,
    observe_mem0_queue_size,
    observe_mem0_write,
    observe_lane_classification,
    observe_db_query,
    observe_duplicate_message,
    observe_idempotency_check,
    observe_error,
    observe_hydration,
    observe_hydration_query,
    track_latency,
    get_metrics,
    get_metrics_summary,
)

__all__ = [
    'observe_request_latency',
    'observe_cache_hit',
    'observe_cache_miss',
    'observe_cache_operation',
    'observe_mem0_queue_size',
    'observe_mem0_write',
    'observe_lane_classification',
    'observe_db_query',
    'observe_duplicate_message',
    'observe_idempotency_check',
    'observe_error',
    'observe_hydration',
    'observe_hydration_query',
    'track_latency',
    'get_metrics',
    'get_metrics_summary',
]
