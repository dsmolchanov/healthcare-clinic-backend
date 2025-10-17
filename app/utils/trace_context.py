"""
Distributed Tracing Context

Provides trace ID propagation across services for end-to-end observability.
Enables filtering and correlating logs for a single request across:
- API server
- Background tasks
- Queue workers
- Database operations
- External API calls
"""

import uuid
import logging
import contextvars
from typing import Optional, Dict, Any
from datetime import datetime
import json

logger = logging.getLogger(__name__)

# Context variable for trace ID (thread-safe)
_trace_id_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    'trace_id', default=None
)
_request_id_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    'request_id', default=None
)


class TraceContext:
    """
    Trace context manager for distributed tracing

    Usage:
        # Start a new trace
        with TraceContext.start() as trace_id:
            logger.info("Processing request")
            # trace_id automatically included in logs

        # Continue existing trace
        with TraceContext.continue_from(trace_id):
            logger.info("Background task")
            # Same trace_id propagated
    """

    @staticmethod
    def generate_trace_id() -> str:
        """Generate new trace ID"""
        return f"trace_{uuid.uuid4().hex[:16]}"

    @staticmethod
    def generate_request_id() -> str:
        """Generate new request ID"""
        return f"req_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def get_trace_id() -> Optional[str]:
        """Get current trace ID from context"""
        return _trace_id_ctx.get()

    @staticmethod
    def get_request_id() -> Optional[str]:
        """Get current request ID from context"""
        return _request_id_ctx.get()

    @staticmethod
    def set_trace_id(trace_id: str):
        """Set trace ID in context"""
        _trace_id_ctx.set(trace_id)

    @staticmethod
    def set_request_id(request_id: str):
        """Set request ID in context"""
        _request_id_ctx.set(request_id)

    @classmethod
    def start(cls, trace_id: Optional[str] = None, request_id: Optional[str] = None):
        """
        Start a new trace context

        Args:
            trace_id: Optional existing trace ID to continue
            request_id: Optional request ID

        Returns:
            Context manager
        """
        return cls(
            trace_id=trace_id or cls.generate_trace_id(),
            request_id=request_id or cls.generate_request_id()
        )

    @classmethod
    def continue_from(cls, trace_id: str, request_id: Optional[str] = None):
        """
        Continue an existing trace

        Args:
            trace_id: Existing trace ID
            request_id: Optional request ID

        Returns:
            Context manager
        """
        return cls(
            trace_id=trace_id,
            request_id=request_id or cls.generate_request_id()
        )

    def __init__(self, trace_id: str, request_id: str):
        self.trace_id = trace_id
        self.request_id = request_id
        self.prev_trace_id = None
        self.prev_request_id = None

    def __enter__(self):
        """Enter context, set trace IDs"""
        self.prev_trace_id = _trace_id_ctx.get()
        self.prev_request_id = _request_id_ctx.get()

        _trace_id_ctx.set(self.trace_id)
        _request_id_ctx.set(self.request_id)

        return self.trace_id

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context, restore previous trace IDs"""
        _trace_id_ctx.set(self.prev_trace_id)
        _request_id_ctx.set(self.prev_request_id)

    def to_dict(self) -> Dict[str, str]:
        """Export trace context as dict for propagation"""
        return {
            'trace_id': self.trace_id,
            'request_id': self.request_id,
            'timestamp': datetime.utcnow().isoformat()
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]):
        """Import trace context from dict"""
        return cls(
            trace_id=data.get('trace_id') or cls.generate_trace_id(),
            request_id=data.get('request_id') or cls.generate_request_id()
        )


class TraceContextFilter(logging.Filter):
    """
    Logging filter that adds trace ID to all log records

    Usage:
        import logging
        logger = logging.getLogger(__name__)
        logger.addFilter(TraceContextFilter())
    """

    def filter(self, record):
        """Add trace context to log record"""
        record.trace_id = TraceContext.get_trace_id() or '-'
        record.request_id = TraceContext.get_request_id() or '-'
        return True


def configure_trace_logging():
    """
    Configure root logger to include trace IDs

    Call this during app initialization:
        from app.utils.trace_context import configure_trace_logging
        configure_trace_logging()
    """
    # Add filter to root logger
    root_logger = logging.getLogger()
    root_logger.addFilter(TraceContextFilter())

    # Update format to include trace IDs
    for handler in root_logger.handlers:
        formatter = logging.Formatter(
            '[%(trace_id)s] [%(request_id)s] %(levelname)s - %(name)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        handler.setFormatter(formatter)

    logger.info("✅ Trace logging configured")


# FastAPI middleware for automatic trace ID injection
class TraceMiddleware:
    """
    FastAPI middleware that automatically creates trace context for each request

    Usage:
        from fastapi import FastAPI
        from app.utils.trace_context import TraceMiddleware

        app = FastAPI()
        app.add_middleware(TraceMiddleware)
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        # Extract trace ID from headers if present
        headers = dict(scope.get('headers', []))
        trace_id = headers.get(b'x-trace-id', b'').decode('utf-8')
        request_id = headers.get(b'x-request-id', b'').decode('utf-8')

        # Start trace context
        with TraceContext.start(trace_id=trace_id or None, request_id=request_id or None) as tid:
            # Add trace ID to response headers
            async def send_with_trace(message):
                if message['type'] == 'http.response.start':
                    headers = message.get('headers', [])
                    headers.append((b'x-trace-id', tid.encode('utf-8')))
                    headers.append((b'x-request-id', TraceContext.get_request_id().encode('utf-8')))
                    message['headers'] = headers
                await send(message)

            await self.app(scope, receive, send_with_trace)


def add_trace_to_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add trace context to dictionary (for Redis messages, etc.)

    Usage:
        message = {'phone': '+1234', 'text': 'Hello'}
        message_with_trace = add_trace_to_dict(message)
        # Now includes 'trace_id' and 'request_id'
    """
    trace_id = TraceContext.get_trace_id()
    request_id = TraceContext.get_request_id()

    if trace_id:
        data['trace_id'] = trace_id
    if request_id:
        data['request_id'] = request_id

    return data


def extract_trace_from_dict(data: Dict[str, Any]) -> Optional[TraceContext]:
    """
    Extract trace context from dictionary

    Usage:
        # In worker receiving message from queue
        trace_ctx = extract_trace_from_dict(message)
        if trace_ctx:
            with trace_ctx:
                logger.info("Processing message")  # trace_id included
    """
    trace_id = data.get('trace_id')
    request_id = data.get('request_id')

    if trace_id:
        return TraceContext.continue_from(trace_id, request_id)

    return None
