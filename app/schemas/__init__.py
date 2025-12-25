"""Shared schemas package."""

from app.schemas.messages import (
    MessageRequest,
    MessageResponse,
    AgentState,
)
from app.schemas.responses import (
    APIResponse,
    PaginatedResponse,
    ErrorResponse,
    HealthResponse,
    ResponseMetadata,
    success_response,
    error_response,
    paginated_response,
    add_metadata,
)

__all__ = [
    # Message schemas
    "MessageRequest",
    "MessageResponse",
    "AgentState",
    # Response schemas
    "APIResponse",
    "PaginatedResponse",
    "ErrorResponse",
    "HealthResponse",
    "ResponseMetadata",
    # Response helpers
    "success_response",
    "error_response",
    "paginated_response",
    "add_metadata",
]
