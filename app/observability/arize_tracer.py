"""
Arize Cloud Integration for LangGraph + Gemini Observability
Uses OpenTelemetry multi-exporter to send traces to BOTH Arize and Langfuse

This avoids the TracerProvider conflict by using a single provider with multiple exporters.
"""

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

ARIZE_ENABLED = bool(os.getenv("ARIZE_SPACE_ID") and os.getenv("ARIZE_API_KEY"))

_tracer_provider = None
_initialized = False


class ProjectNameSpanProcessor:
    """
    Custom SpanProcessor that adds openinference.project.name attribute to all spans.
    This is required by Arize Cloud when using an existing TracerProvider.
    """

    def __init__(self, project_name: str, exporter):
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        self.project_name = project_name
        self._batch_processor = BatchSpanProcessor(exporter)

    def on_start(self, span, parent_context=None):
        # Add project name attribute to every span
        span.set_attribute("openinference.project.name", self.project_name)

    def on_end(self, span):
        self._batch_processor.on_end(span)

    def shutdown(self):
        self._batch_processor.shutdown()

    def force_flush(self, timeout_millis: int = 30000):
        return self._batch_processor.force_flush(timeout_millis)


def init_arize():
    """
    Initialize Arize Cloud as an additional exporter alongside Langfuse.

    Instead of using arize-otel's register() which overrides the TracerProvider,
    we add Arize as an additional OTLP exporter to the existing OpenTelemetry setup.

    This allows traces to flow to BOTH:
    - Langfuse (via their OTLP endpoint)
    - Arize Cloud (via otlp.arize.com)
    """
    global _tracer_provider, _initialized

    if _initialized:
        logger.info("Arize already initialized, skipping")
        return _tracer_provider

    if not ARIZE_ENABLED:
        logger.info("Arize disabled (no ARIZE_SPACE_ID/ARIZE_API_KEY set)")
        return None

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        project_name = os.getenv("ARIZE_PROJECT_NAME", "healthcare-langgraph")
        arize_space_id = os.getenv("ARIZE_SPACE_ID")
        arize_api_key = os.getenv("ARIZE_API_KEY")

        # Get existing provider or create new one
        existing_provider = trace.get_tracer_provider()

        # Create Arize OTLP exporter
        arize_exporter = OTLPSpanExporter(
            endpoint="https://otlp.arize.com/v1/traces",
            headers={
                "space_id": arize_space_id,
                "api_key": arize_api_key,
            }
        )

        # Check if it's already a real TracerProvider (not the default noop)
        if isinstance(existing_provider, TracerProvider):
            _tracer_provider = existing_provider
            logger.info("Using existing TracerProvider, adding Arize exporter with project injection")

            # Use custom processor that adds project name to each span
            _tracer_provider.add_span_processor(
                ProjectNameSpanProcessor(project_name, arize_exporter)
            )
        else:
            # Create new provider with OpenInference resource attributes
            resource = Resource.create({
                "openinference.project.name": project_name,
                "service.name": "healthcare-backend",
            })
            _tracer_provider = TracerProvider(resource=resource)
            trace.set_tracer_provider(_tracer_provider)
            logger.info("Created new TracerProvider for Arize")

            # Standard batch processor since resource has the project name
            _tracer_provider.add_span_processor(
                BatchSpanProcessor(arize_exporter)
            )

        # Now instrument Google GenAI and LangChain
        try:
            from openinference.instrumentation.google_genai import GoogleGenAIInstrumentor
            GoogleGenAIInstrumentor().instrument(tracer_provider=_tracer_provider)
            logger.info("  - Google GenAI instrumented")
        except ImportError:
            logger.warning("  - openinference-instrumentation-google-genai not installed")
        except Exception as e:
            logger.warning(f"  - Failed to instrument Google GenAI: {e}")

        try:
            from openinference.instrumentation.langchain import LangChainInstrumentor
            LangChainInstrumentor().instrument(tracer_provider=_tracer_provider)
            logger.info("  - LangChain/LangGraph instrumented")
        except ImportError:
            logger.warning("  - openinference-instrumentation-langchain not installed")
        except Exception as e:
            logger.warning(f"  - Failed to instrument LangChain: {e}")

        _initialized = True
        logger.info(f"✅ Arize Cloud observability enabled (multi-exporter mode)")
        logger.info(f"   Project: {os.getenv('ARIZE_PROJECT_NAME', 'default')}")

        return _tracer_provider

    except ImportError as e:
        logger.warning(f"OpenTelemetry packages not installed: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to initialize Arize: {e}")
        return None


def get_tracer_provider():
    """Get the initialized tracer provider for manual instrumentation if needed."""
    return _tracer_provider
