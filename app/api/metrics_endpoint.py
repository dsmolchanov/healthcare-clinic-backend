"""
Metrics Endpoint for Prometheus Scraping

Exposes Prometheus metrics at /metrics endpoint
"""

from fastapi import APIRouter, Response
from app.observability.metrics import get_metrics, get_metrics_summary

router = APIRouter(tags=["observability"])


@router.get("/metrics")
async def prometheus_metrics():
    """
    Prometheus metrics endpoint

    Returns metrics in Prometheus text format for scraping

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

    Returns JSON summary of key metrics for quick health checks
    """
    return get_metrics_summary()


@router.get("/health")
async def health_check():
    """
    Health check endpoint

    Returns basic health status and metrics
    """
    summary = get_metrics_summary()

    # Calculate health status
    cache_hit_rate = summary['cache']['hit_rate']
    error_rate = summary['errors']['total']

    is_healthy = cache_hit_rate > 50 and error_rate < 100

    return {
        'status': 'healthy' if is_healthy else 'degraded',
        'cache_hit_rate': cache_hit_rate,
        'errors': error_rate,
        'mem0_queue_size': summary['mem0']['queue_size'],
        'timestamp': None  # Add timestamp if needed
    }
