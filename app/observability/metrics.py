"""
Prometheus Metrics for Task #7: Observability

Tracks key performance indicators:
- Request latency (P50, P95, P99)
- Cache hit rates
- mem0 write queue metrics
- Circuit breaker states
- Lane classification distribution
"""

from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    Summary,
    CollectorRegistry,
    generate_latest,
    CONTENT_TYPE_LATEST
)
import time
from functools import wraps
from typing import Callable, Any
import logging

logger = logging.getLogger(__name__)

# Create registry
registry = CollectorRegistry()

# ==============================================================================
# REQUEST METRICS
# ==============================================================================

# Request latency histogram (buckets optimized for <500ms target)
REQUEST_LATENCY = Histogram(
    'webhook_request_duration_seconds',
    'Webhook request duration in seconds',
    ['endpoint', 'lane', 'status'],
    buckets=(0.05, 0.1, 0.2, 0.4, 0.6, 1.0, 2.0, 5.0, 10.0, 20.0),
    registry=registry
)

# Request counter
REQUEST_COUNTER = Counter(
    'webhook_requests_total',
    'Total webhook requests',
    ['endpoint', 'lane', 'status'],
    registry=registry
)

# ==============================================================================
# CACHE METRICS
# ==============================================================================

# Cache hit/miss counters
CACHE_HITS = Counter(
    'cache_hits_total',
    'Total cache hits',
    ['cache_type'],  # bundle, patient, session
    registry=registry
)

CACHE_MISSES = Counter(
    'cache_misses_total',
    'Total cache misses',
    ['cache_type'],
    registry=registry
)

# Cache operation latency
CACHE_OPERATION_LATENCY = Histogram(
    'cache_operation_duration_seconds',
    'Cache operation duration',
    ['operation', 'cache_type'],  # get, set, delete
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
    registry=registry
)

# ==============================================================================
# MEM0 METRICS
# ==============================================================================

# mem0 write queue size
MEM0_QUEUE_SIZE = Gauge(
    'mem0_write_queue_size',
    'Current mem0 write queue size',
    registry=registry
)

# mem0 write operations
MEM0_WRITES = Counter(
    'mem0_writes_total',
    'Total mem0 write operations',
    ['status'],  # success, failure, timeout
    registry=registry
)

# mem0 write latency
MEM0_WRITE_LATENCY = Histogram(
    'mem0_write_duration_seconds',
    'mem0 write operation duration',
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0),
    registry=registry
)

# ==============================================================================
# ROUTER METRICS
# ==============================================================================

# Lane classification distribution
LANE_CLASSIFICATION = Counter(
    'lane_classification_total',
    'Total lane classifications',
    ['lane'],  # faq, price, scheduling, complex
    registry=registry
)

# Classification latency
CLASSIFICATION_LATENCY = Histogram(
    'classification_duration_seconds',
    'Message classification duration',
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25),
    registry=registry
)

# ==============================================================================
# DATABASE METRICS
# ==============================================================================

# Database query latency
DB_QUERY_LATENCY = Histogram(
    'database_query_duration_seconds',
    'Database query duration',
    ['operation', 'table'],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0),
    registry=registry
)

# Database query counter
DB_QUERIES = Counter(
    'database_queries_total',
    'Total database queries',
    ['operation', 'table', 'status'],
    registry=registry
)

# ==============================================================================
# IDEMPOTENCY METRICS
# ==============================================================================

# Duplicate message detection
DUPLICATE_MESSAGES = Counter(
    'duplicate_messages_total',
    'Total duplicate messages detected',
    registry=registry
)

# Idempotency check latency
IDEMPOTENCY_CHECK_LATENCY = Histogram(
    'idempotency_check_duration_seconds',
    'Idempotency check duration',
    buckets=(0.0001, 0.0005, 0.001, 0.005, 0.01),
    registry=registry
)

# ==============================================================================
# ERROR METRICS
# ==============================================================================

# Error counter
ERRORS = Counter(
    'errors_total',
    'Total errors',
    ['error_type', 'component'],
    registry=registry
)

# ==============================================================================
# HYDRATION METRICS
# ==============================================================================

# Context hydration latency
HYDRATION_LATENCY = Histogram(
    'context_hydration_duration_seconds',
    'Context hydration duration',
    buckets=(0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 1.0),
    registry=registry
)

# Hydration query count
HYDRATION_QUERIES = Counter(
    'hydration_queries_total',
    'Total queries in hydration',
    ['query_type'],  # bundle, patient, session
    registry=registry
)

# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

def observe_request_latency(endpoint: str, lane: str, status: str, duration_seconds: float):
    """Record request latency"""
    REQUEST_LATENCY.labels(endpoint=endpoint, lane=lane, status=status).observe(duration_seconds)
    REQUEST_COUNTER.labels(endpoint=endpoint, lane=lane, status=status).inc()


def observe_cache_hit(cache_type: str):
    """Record cache hit"""
    CACHE_HITS.labels(cache_type=cache_type).inc()


def observe_cache_miss(cache_type: str):
    """Record cache miss"""
    CACHE_MISSES.labels(cache_type=cache_type).inc()


def observe_cache_operation(operation: str, cache_type: str, duration_seconds: float):
    """Record cache operation latency"""
    CACHE_OPERATION_LATENCY.labels(operation=operation, cache_type=cache_type).observe(duration_seconds)


def observe_mem0_queue_size(size: int):
    """Update mem0 queue size gauge"""
    MEM0_QUEUE_SIZE.set(size)


def observe_mem0_write(status: str, duration_seconds: float):
    """Record mem0 write operation"""
    MEM0_WRITES.labels(status=status).inc()
    MEM0_WRITE_LATENCY.observe(duration_seconds)


def observe_lane_classification(lane: str, duration_seconds: float):
    """Record lane classification"""
    LANE_CLASSIFICATION.labels(lane=lane).inc()
    CLASSIFICATION_LATENCY.observe(duration_seconds)


def observe_db_query(operation: str, table: str, status: str, duration_seconds: float):
    """Record database query"""
    DB_QUERIES.labels(operation=operation, table=table, status=status).inc()
    DB_QUERY_LATENCY.labels(operation=operation, table=table).observe(duration_seconds)


def observe_duplicate_message():
    """Record duplicate message detection"""
    DUPLICATE_MESSAGES.inc()


def observe_idempotency_check(duration_seconds: float):
    """Record idempotency check latency"""
    IDEMPOTENCY_CHECK_LATENCY.observe(duration_seconds)


def observe_error(error_type: str, component: str):
    """Record error"""
    ERRORS.labels(error_type=error_type, component=component).inc()


def observe_hydration(duration_seconds: float):
    """Record context hydration"""
    HYDRATION_LATENCY.observe(duration_seconds)


def observe_hydration_query(query_type: str):
    """Record hydration query"""
    HYDRATION_QUERIES.labels(query_type=query_type).inc()


# ==============================================================================
# DECORATORS
# ==============================================================================

def track_latency(metric_name: str = None, **labels):
    """Decorator to track function latency"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs) -> Any:
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                duration = time.time() - start_time

                if metric_name:
                    # Use custom metric
                    pass
                else:
                    logger.debug(f"{func.__name__} completed in {duration:.3f}s")

                return result
            except Exception as e:
                duration = time.time() - start_time
                observe_error(type(e).__name__, func.__name__)
                raise

        @wraps(func)
        def sync_wrapper(*args, **kwargs) -> Any:
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                duration = time.time() - start_time
                return result
            except Exception as e:
                duration = time.time() - start_time
                observe_error(type(e).__name__, func.__name__)
                raise

        # Return appropriate wrapper based on function type
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


# ==============================================================================
# METRICS ENDPOINT
# ==============================================================================

def get_metrics() -> tuple:
    """Generate Prometheus metrics output"""
    return generate_latest(registry), CONTENT_TYPE_LATEST


# ==============================================================================
# SUMMARY METRICS
# ==============================================================================

def get_metrics_summary() -> dict:
    """Get human-readable metrics summary"""
    return {
        'requests': {
            'total': REQUEST_COUNTER._value.sum(),
        },
        'cache': {
            'hits': CACHE_HITS._value.sum(),
            'misses': CACHE_MISSES._value.sum(),
            'hit_rate': CACHE_HITS._value.sum() / max(CACHE_HITS._value.sum() + CACHE_MISSES._value.sum(), 1) * 100
        },
        'mem0': {
            'queue_size': MEM0_QUEUE_SIZE._value.get(),
            'writes': MEM0_WRITES._value.sum(),
        },
        'lanes': {
            'faq': LANE_CLASSIFICATION.labels(lane='faq')._value.get(),
            'price': LANE_CLASSIFICATION.labels(lane='price')._value.get(),
            'scheduling': LANE_CLASSIFICATION.labels(lane='scheduling')._value.get(),
            'complex': LANE_CLASSIFICATION.labels(lane='complex')._value.get(),
        },
        'errors': {
            'total': ERRORS._value.sum(),
        },
        'duplicates': {
            'total': DUPLICATE_MESSAGES._value.get(),
        }
    }
