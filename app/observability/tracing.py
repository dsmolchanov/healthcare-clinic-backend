"""
OpenTelemetry Tracing Configuration

Provides distributed tracing across:
- Webhook ingestion
- Message routing
- Database queries
- Cache operations
- LLM calls
- Response generation
"""

import os
import logging
from contextlib import contextmanager
from typing import Optional, Dict, Any

try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.requests import RequestsInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    trace = None

logger = logging.getLogger(__name__)

# Global tracer
_tracer: Optional[Any] = None


def init_tracing(service_name: str = "healthcare-backend", enable_console: bool = False) -> bool:
    """
    Initialize OpenTelemetry tracing

    Args:
        service_name: Service name for traces
        enable_console: Enable console exporter for debugging

    Returns:
        True if tracing initialized, False otherwise
    """
    global _tracer

    if not OTEL_AVAILABLE:
        logger.warning("OpenTelemetry not available, tracing disabled")
        return False

    try:
        # Create resource with service name
        resource = Resource(attributes={
            SERVICE_NAME: service_name
        })

        # Create tracer provider
        provider = TracerProvider(resource=resource)

        # Add exporters
        if enable_console or os.getenv("OTEL_CONSOLE_EXPORTER", "false").lower() == "true":
            console_exporter = ConsoleSpanExporter()
            provider.add_span_processor(BatchSpanProcessor(console_exporter))
            logger.info("✅ Console span exporter enabled")

        # Add OTLP exporter if endpoint configured
        otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        if otlp_endpoint:
            otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
            provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
            logger.info(f"✅ OTLP span exporter enabled: {otlp_endpoint}")

        # Set global tracer provider
        trace.set_tracer_provider(provider)

        # Get tracer
        _tracer = trace.get_tracer(__name__)

        # Auto-instrument libraries
        try:
            FastAPIInstrumentor().instrument()
            RequestsInstrumentor().instrument()
            RedisInstrumentor().instrument()
            logger.info("✅ Auto-instrumented FastAPI, Requests, Redis")
        except Exception as e:
            logger.warning(f"Could not auto-instrument libraries: {e}")

        logger.info(f"✅ OpenTelemetry tracing initialized for {service_name}")
        return True

    except Exception as e:
        logger.error(f"Failed to initialize tracing: {e}")
        return False


def get_tracer():
    """Get the global tracer"""
    return _tracer


@contextmanager
def trace_span(name: str, attributes: Optional[Dict[str, Any]] = None):
    """
    Create a traced span

    Usage:
        with trace_span("hydrate_context", {"clinic_id": clinic_id}):
            # Your code here
            pass
    """
    if _tracer is None or not OTEL_AVAILABLE:
        # Tracing disabled, no-op context manager
        yield None
        return

    with _tracer.start_as_current_span(name) as span:
        if attributes:
            for key, value in attributes.items():
                span.set_attribute(key, str(value))
        yield span


def add_span_event(name: str, attributes: Optional[Dict[str, Any]] = None):
    """Add an event to the current span"""
    if not OTEL_AVAILABLE or _tracer is None:
        return

    span = trace.get_current_span()
    if span:
        span.add_event(name, attributes or {})


def set_span_attribute(key: str, value: Any):
    """Set attribute on current span"""
    if not OTEL_AVAILABLE or _tracer is None:
        return

    span = trace.get_current_span()
    if span:
        span.set_attribute(key, str(value))


def record_exception(exception: Exception):
    """Record exception in current span"""
    if not OTEL_AVAILABLE or _tracer is None:
        return

    span = trace.get_current_span()
    if span:
        span.record_exception(exception)
