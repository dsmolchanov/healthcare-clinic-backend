"""
Graph-level tracing for LangGraph execution.
Attaches structured span attributes for observability.

IMPORTANT: Use as context managers to ensure spans are properly ended:
    with trace_graph_node("supervisor", thread_id, clinic_id):
        # node logic here

Integrates with OpenTelemetry for span management and exports to
configured backends (Langfuse, Arize, etc).
"""
from opentelemetry import trace
from contextlib import contextmanager
from typing import Optional, Dict, Any, Generator
import logging

logger = logging.getLogger(__name__)

tracer = trace.get_tracer(__name__)


@contextmanager
def trace_graph_node(
    node_name: str,
    thread_id: str,
    clinic_id: str,
    lane: Optional[str] = None,
    tool_name: Optional[str] = None,
    eval_result: Optional[str] = None,
) -> Generator[trace.Span, None, None]:
    """
    Create traced span for graph node execution.

    Usage:
        with trace_graph_node("supervisor", thread_id, clinic_id) as span:
            # node logic
            span.set_attribute("custom_attr", value)

    Args:
        node_name: Name of the LangGraph node
        thread_id: Session-scoped thread ID for checkpointer
        clinic_id: Clinic/tenant identifier
        lane: Optional processing lane (SCHEDULING, COMPLEX, etc)
        tool_name: Optional tool being executed in this node
        eval_result: Optional evaluation result for quality tracking
    """
    with tracer.start_as_current_span(f"langgraph.node.{node_name}") as span:
        span.set_attribute("thread_id", thread_id)
        span.set_attribute("clinic_id", clinic_id)
        span.set_attribute("node_name", node_name)
        if lane:
            span.set_attribute("lane", lane)
        if tool_name:
            span.set_attribute("tool_name", tool_name)
        if eval_result:
            span.set_attribute("eval_result", eval_result)
        try:
            yield span
        except Exception as e:
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
            span.record_exception(e)
            raise


@contextmanager
def trace_tool_call(
    tool_name: str,
    thread_id: str,
    arguments: Dict[str, Any],
    clinic_id: Optional[str] = None,
) -> Generator[trace.Span, None, None]:
    """
    Create traced span for tool execution.

    Usage:
        with trace_tool_call("check_availability", thread_id, args) as span:
            result = await tool(**args)
            span.set_attribute("tool_result_type", type(result).__name__)

    Args:
        tool_name: Name of the tool being called
        thread_id: Session-scoped thread ID
        arguments: Tool arguments (truncated for safety)
        clinic_id: Optional clinic identifier
    """
    with tracer.start_as_current_span(f"langgraph.tool.{tool_name}") as span:
        span.set_attribute("thread_id", thread_id)
        span.set_attribute("tool_name", tool_name)
        span.set_attribute("tool_arguments", str(arguments)[:500])  # Truncate for safety
        if clinic_id:
            span.set_attribute("clinic_id", clinic_id)
        try:
            yield span
            span.set_attribute("tool_success", True)
        except Exception as e:
            span.set_attribute("tool_success", False)
            span.set_attribute("tool_error", str(e))
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
            span.record_exception(e)
            raise


@contextmanager
def trace_graph_execution(
    graph_name: str,
    thread_id: str,
    clinic_id: str,
    session_id: str,
) -> Generator[trace.Span, None, None]:
    """
    Create traced span for entire graph execution.

    Usage:
        with trace_graph_execution("healthcare", thread_id, clinic_id, session_id) as span:
            result = await graph.ainvoke(state, config)
            span.set_attribute("response_length", len(result.get("response", "")))

    Args:
        graph_name: Name of the LangGraph (e.g., "healthcare")
        thread_id: Session-scoped thread ID
        clinic_id: Clinic identifier
        session_id: Session identifier
    """
    with tracer.start_as_current_span(f"langgraph.graph.{graph_name}") as span:
        span.set_attribute("thread_id", thread_id)
        span.set_attribute("clinic_id", clinic_id)
        span.set_attribute("session_id", session_id)
        span.set_attribute("graph_name", graph_name)
        try:
            yield span
            span.set_attribute("graph_success", True)
        except Exception as e:
            span.set_attribute("graph_success", False)
            span.set_attribute("graph_error", str(e))
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
            span.record_exception(e)
            raise


def add_graph_attributes(
    span: trace.Span,
    intent: Optional[str] = None,
    lane: Optional[str] = None,
    flow_state: Optional[str] = None,
    turn_status: Optional[str] = None,
) -> None:
    """
    Add common graph attributes to an existing span.

    Use this to enrich spans with state information after processing.

    Args:
        span: OpenTelemetry span to add attributes to
        intent: Detected intent (e.g., "appointment", "inquiry")
        lane: Processing lane (e.g., "SCHEDULING", "COMPLEX")
        flow_state: Current flow state (e.g., "collecting_slots", "awaiting_confirmation")
        turn_status: Current turn status (e.g., "user_turn", "agent_action_pending")
    """
    if intent:
        span.set_attribute("intent", intent)
    if lane:
        span.set_attribute("lane", lane)
    if flow_state:
        span.set_attribute("flow_state", flow_state)
    if turn_status:
        span.set_attribute("turn_status", turn_status)
