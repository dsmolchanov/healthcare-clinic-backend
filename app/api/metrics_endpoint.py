"""
Metrics Endpoint for Prometheus Scraping

Exposes Prometheus metrics at /metrics endpoint.
FSM metrics removed in Phase 1.3 cleanup - LangGraph metrics planned for Phase 1.8.
"""

from fastapi import APIRouter, Response
from app.observability.metrics import get_metrics, get_metrics_summary

router = APIRouter(tags=["observability"])


@router.get("/metrics")
async def prometheus_metrics():
    """
    Prometheus metrics endpoint

    Returns metrics in Prometheus text format for scraping.

    Example Prometheus scrape config:
    ```yaml
    scrape_configs:
      - job_name: 'healthcare-backend'
        static_configs:
          - targets: ['healthcare-clinic-backend.fly.dev']
        metrics_path: '/metrics'
        scrape_interval: 15s
    ```
    """
    metrics_data, content_type = get_metrics()
    return Response(content=metrics_data, media_type=content_type)


@router.get("/metrics/summary")
async def metrics_summary():
    """
    Human-readable metrics summary

    Returns JSON summary of key metrics for quick health checks.
    """
    return get_metrics_summary()


@router.get("/health")
async def health_check():
    """
    Health check endpoint

    Returns basic health status and metrics.
    Includes process memory usage.
    """
    import psutil
    import os
    from datetime import datetime

    summary = get_metrics_summary()

    # Get process memory usage
    process = psutil.Process(os.getpid())
    memory_info = process.memory_info()
    memory_mb = memory_info.rss / (1024 * 1024)
    memory_percent = process.memory_percent()

    # Calculate health status
    cache_hit_rate = summary['cache']['hit_rate']
    error_rate = summary['errors']['total']

    # System is healthy if:
    # - Cache hit rate > 50%
    # - Error rate < 100
    # - Memory usage < 85%
    is_healthy = (
        cache_hit_rate > 50
        and error_rate < 100
        and memory_percent < 85
    )

    # Memory warning at 70%
    if memory_percent > 85:
        status = 'critical'
    elif memory_percent > 70 or not is_healthy:
        status = 'degraded'
    else:
        status = 'healthy'

    return {
        'status': status,
        'cache_hit_rate': cache_hit_rate,
        'errors': error_rate,
        'mem0_queue_size': summary['mem0']['queue_size'],
        'memory': {
            'used_mb': round(memory_mb, 1),
            'percent': round(memory_percent, 1),
        },
        'timestamp': datetime.utcnow().isoformat()
    }
