"""
Metrics Endpoint for Prometheus Scraping

Exposes Prometheus metrics at /metrics endpoint
Includes both general observability metrics and FSM-specific metrics
"""

from fastapi import APIRouter, Response
from app.observability.metrics import get_metrics, get_metrics_summary
from app.fsm.metrics import get_metrics as get_fsm_metrics, get_metrics_summary as get_fsm_summary

router = APIRouter(tags=["observability"])


@router.get("/metrics")
async def prometheus_metrics():
    """
    Prometheus metrics endpoint

    Returns metrics in Prometheus text format for scraping.
    Includes both general observability metrics and FSM-specific metrics.

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
    # Get general observability metrics
    general_metrics_data, content_type = get_metrics()

    # Get FSM-specific metrics
    fsm_metrics_data = get_fsm_metrics()

    # Combine both metric sets
    combined_metrics = general_metrics_data + b"\n" + fsm_metrics_data

    return Response(content=combined_metrics, media_type=content_type)


@router.get("/metrics/summary")
async def metrics_summary():
    """
    Human-readable metrics summary

    Returns JSON summary of key metrics for quick health checks.
    Includes both general observability and FSM metrics.
    """
    general_summary = get_metrics_summary()
    fsm_summary = get_fsm_summary()

    return {
        "general": general_summary,
        "fsm": fsm_summary
    }


@router.get("/metrics/fsm")
async def fsm_metrics_only():
    """
    FSM metrics only (Prometheus format)

    Returns only FSM-specific metrics for focused monitoring.
    """
    fsm_metrics_data = get_fsm_metrics()
    return Response(content=fsm_metrics_data, media_type='text/plain; charset=utf-8')


@router.get("/metrics/fsm/summary")
async def fsm_summary_only():
    """
    FSM metrics summary (JSON format)

    Returns human-readable FSM metrics for debugging and monitoring.
    """
    return get_fsm_summary()


@router.get("/health")
async def health_check():
    """
    Health check endpoint

    Returns basic health status and metrics.
    Includes FSM health indicators and process memory usage.
    """
    import psutil
    import os
    from datetime import datetime

    summary = get_metrics_summary()
    fsm_summary = get_fsm_summary()

    # Get process memory usage
    process = psutil.Process(os.getpid())
    memory_info = process.memory_info()
    memory_mb = memory_info.rss / (1024 * 1024)
    memory_percent = process.memory_percent()

    # Calculate health status
    cache_hit_rate = summary['cache']['hit_rate']
    error_rate = summary['errors']['total']
    fsm_escalations = fsm_summary.get('escalations', {}).get('total', 0)
    fsm_bad_bookings = fsm_summary.get('data_quality', {}).get('bad_bookings', 0)

    # System is healthy if:
    # - Cache hit rate > 50%
    # - Error rate < 100
    # - FSM escalations < 10
    # - Bad bookings < 20
    # - Memory usage < 85%
    is_healthy = (
        cache_hit_rate > 50
        and error_rate < 100
        and fsm_escalations < 10
        and fsm_bad_bookings < 20
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
        'fsm_escalations': fsm_escalations,
        'fsm_bad_bookings': fsm_bad_bookings,
        'memory': {
            'used_mb': round(memory_mb, 1),
            'percent': round(memory_percent, 1),
        },
        'timestamp': datetime.utcnow().isoformat()
    }
