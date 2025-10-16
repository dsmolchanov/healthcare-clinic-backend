# Observability - Task #7

Comprehensive monitoring and observability for the healthcare backend system.

## Components

### 1. Prometheus Metrics (`app/observability/metrics.py`)

Exposes key performance metrics:

#### Request Metrics
- `webhook_request_duration_seconds` - Request latency histogram (P50, P95, P99)
- `webhook_requests_total` - Total request counter by lane and status

#### Cache Metrics
- `cache_hits_total` / `cache_misses_total` - Cache performance by type
- `cache_operation_duration_seconds` - Cache operation latency

#### mem0 Metrics
- `mem0_write_queue_size` - Current queue size gauge
- `mem0_writes_total` - Write operations by status
- `mem0_write_duration_seconds` - Write operation latency

#### Router Metrics
- `lane_classification_total` - Lane distribution (FAQ, PRICE, SCHEDULING, COMPLEX)
- `classification_duration_seconds` - Classification latency

#### Database Metrics
- `database_query_duration_seconds` - Query latency by operation
- `database_queries_total` - Query counter by status

#### Idempotency Metrics
- `duplicate_messages_total` - Duplicate message detection counter
- `idempotency_check_duration_seconds` - Check latency

#### Error Metrics
- `errors_total` - Error counter by type and component

#### Hydration Metrics
- `context_hydration_duration_seconds` - Hydration latency
- `hydration_queries_total` - Query counter by type

### 2. OpenTelemetry Tracing (`app/observability/tracing.py`)

Distributed tracing across the request flow:

```python
from app.observability.tracing import trace_span

async def my_function():
    with trace_span("my_operation", {"key": "value"}):
        # Your code here
        pass
```

### 3. Grafana Dashboard (`grafana-dashboard.json`)

Pre-built dashboard with 8 panels:
1. Request Latency (P50, P95, P99)
2. Cache Hit Rate
3. Error Rate
4. Throughput & Lane Distribution
5. Context Hydration Performance
6. mem0 Write Queue & Operations
7. Database Query Performance
8. Idempotency Check Performance

#### Importing Dashboard

```bash
# Via Grafana UI
1. Navigate to Dashboards → Import
2. Upload grafana-dashboard.json
3. Select Prometheus data source
4. Click Import

# Via API
curl -X POST http://your-grafana:3000/api/dashboards/db \
  -H "Content-Type: application/json" \
  -d @grafana-dashboard.json
```

### 4. Prometheus Alert Rules (`prometheus-alerts.yaml`)

13 pre-configured alerts:

#### Critical Alerts
- `CriticalWebhookLatency` - P95 > 5s
- `CriticalCacheHitRate` - Hit rate < 50%
- `CriticalErrorRate` - > 1 error/sec

#### Warning Alerts
- `HighWebhookLatency` - P95 > 2s
- `LowCacheHitRate` - Hit rate < 80%
- `HighErrorRate` - > 0.1 error/sec
- `HighMem0QueueSize` - Queue > 100 items
- `Mem0WriteFailures` - Failure rate > 10%
- `SlowContextHydration` - P95 > 200ms
- `SlowDatabaseQueries` - P95 > 1s
- `HighDuplicateMessageRate` - > 0.5 duplicates/sec

#### Info Alerts
- `LowThroughput` - < 0.01 req/sec
- `HighThroughput` - > 100 req/sec
- `LowFastPathCoverage` - < 50% fast-path usage

#### Importing Alert Rules

```bash
# Add to prometheus.yml
rule_files:
  - "prometheus-alerts.yaml"

# Reload Prometheus
curl -X POST http://localhost:9090/-/reload
```

## Setup

### 1. Install Dependencies

```bash
pip install prometheus-client opentelemetry-api opentelemetry-sdk opentelemetry-instrumentation-fastapi
```

### 2. Add Metrics Endpoint

```python
# In your FastAPI app (main.py)
from app.api.metrics_endpoint import router as metrics_router

app.include_router(metrics_router)
```

### 3. Configure Prometheus Scraping

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'healthcare-backend'
    static_configs:
      - targets: ['healthcare-clinic-backend.fly.dev']
    metrics_path: '/metrics'
    scrape_interval: 15s
```

### 4. Enable OpenTelemetry (Optional)

```python
# In main.py
from app.observability.tracing import init_tracing

# Initialize on startup
init_tracing(service_name="healthcare-backend", enable_console=False)
```

Set environment variables:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT="http://your-otel-collector:4317"
export OTEL_CONSOLE_EXPORTER="false"  # Set to "true" for debugging
```

## Endpoints

### `/metrics`
Prometheus metrics endpoint (text format)

```bash
curl http://localhost:8000/metrics
```

### `/metrics/summary`
Human-readable JSON summary

```bash
curl http://localhost:8000/metrics/summary
```

Example output:
```json
{
  "requests": {"total": 1523},
  "cache": {"hits": 1234, "misses": 289, "hit_rate": 81.0},
  "mem0": {"queue_size": 5, "writes": 456},
  "lanes": {"faq": 432, "price": 321, "scheduling": 234, "complex": 536},
  "errors": {"total": 12},
  "duplicates": {"total": 3}
}
```

### `/health`
Health check with key metrics

```bash
curl http://localhost:8000/health
```

## Usage in Code

### Recording Metrics

```python
from app.observability import (
    observe_request_latency,
    observe_cache_hit,
    observe_lane_classification,
    observe_hydration,
)

# Record request latency
observe_request_latency(
    endpoint="/webhooks/evolution",
    lane="faq",
    status="success",
    duration_seconds=0.245
)

# Record cache hit
observe_cache_hit(cache_type="bundle")

# Record lane classification
observe_lane_classification(lane="faq", duration_seconds=0.015)

# Record hydration
observe_hydration(duration_seconds=0.085)
```

### Using Tracing

```python
from app.observability.tracing import trace_span, add_span_event

async def process_message(message_id: str):
    with trace_span("process_message", {"message_id": message_id}):
        # Step 1
        add_span_event("hydration_started")
        context = await hydrate_context()

        # Step 2
        add_span_event("classification_started")
        lane = await classify_message()

        # Step 3
        add_span_event("response_generation")
        response = await generate_response()

        return response
```

## Performance Targets

| Metric | Target | Alert Threshold |
|--------|--------|-----------------|
| P95 Request Latency | <500ms | >2s (warning), >5s (critical) |
| Cache Hit Rate | >95% | <80% (warning), <50% (critical) |
| Context Hydration | <100ms | >200ms (warning) |
| Idempotency Check | <1ms | >10ms (info) |
| Error Rate | <0.01/sec | >0.1/sec (warning), >1.0/sec (critical) |
| Fast-Path Coverage | >70% | <50% (info) |

## Dashboard Views

Access your Grafana dashboard at:
```
http://your-grafana:3000/d/healthcare-backend
```

### Key Panels to Monitor

1. **Request Latency** - Ensure P95 stays below 500ms (green line)
2. **Cache Hit Rate** - Should be >80% (above red threshold line)
3. **Lane Distribution** - Verify >70% using fast-path (FAQ/PRICE)
4. **Error Rate** - Should trend near zero

## Alerting

Alerts will fire to your configured notification channels when thresholds are exceeded.

Example Alertmanager config:

```yaml
route:
  group_by: ['alertname', 'severity']
  receiver: 'slack-notifications'

receivers:
  - name: 'slack-notifications'
    slack_configs:
      - api_url: 'YOUR_SLACK_WEBHOOK_URL'
        channel: '#alerts-healthcare'
        title: '{{ .GroupLabels.alertname }}'
        text: '{{ range .Alerts }}{{ .Annotations.description }}{{ end }}'
```

## Troubleshooting

### Metrics not showing up in Prometheus

1. Check endpoint is accessible:
   ```bash
   curl http://your-app/metrics
   ```

2. Verify Prometheus scrape config and target health:
   ```
   http://your-prometheus:9090/targets
   ```

3. Check Prometheus logs for scrape errors

### High latency alerts firing

1. Check the Grafana dashboard to identify which lane
2. Review recent code changes or deployments
3. Check database query performance panel
4. Verify cache hit rates are normal
5. Check mem0 queue size for backlog

### Low cache hit rate

1. Verify Redis is running: `redis-cli ping`
2. Check cache key TTLs are appropriate
3. Review cache invalidation logic
4. Check for cache stampede (many concurrent misses)

## Next Steps

- [ ] Set up Alertmanager for notifications
- [ ] Configure Grafana alerts for dashboards
- [ ] Add custom SLO tracking
- [ ] Integrate with incident management (PagerDuty, Opsgenie)
- [ ] Add tracing visualization (Jaeger, Tempo)
