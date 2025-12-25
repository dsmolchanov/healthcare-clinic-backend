"""
Standardized API response schemas.
All endpoints should use these for consistency.

Phase 5 Implementation - Non-breaking API standardization.
Uses additive fields (Option A) to maintain backwards compatibility.
"""
from pydantic import BaseModel, Field
from typing import TypeVar, Generic, Optional, Any, List
from datetime import datetime

T = TypeVar('T')


class ResponseMetadata(BaseModel):
    """Metadata included with API responses."""
    request_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    processing_time_ms: Optional[int] = None
    version: str = "1.0"


class APIResponse(BaseModel, Generic[T]):
    """
    Standard API response wrapper.

    For V2 endpoints that use the wrapped format:
    {"success": true, "data": {...}, "metadata": {...}}
    """
    success: bool
    data: Optional[T] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    metadata: Optional[ResponseMetadata] = None


class PaginatedResponse(BaseModel, Generic[T]):
    """Paginated list response."""
    success: bool = True
    data: List[T]
    total: int
    page: int
    page_size: int
    has_more: bool
    metadata: Optional[ResponseMetadata] = None


class ErrorResponse(BaseModel):
    """Standard error response."""
    success: bool = False
    error: str
    error_code: Optional[str] = None
    details: Optional[dict] = None
    metadata: Optional[ResponseMetadata] = None


class HealthResponse(BaseModel):
    """Health check response."""
    status: str = "healthy"
    version: str = "1.0.0"
    services: dict = Field(default_factory=dict)


# Response helpers - use these for consistent response formatting
def success_response(
    data: Any,
    request_id: str | None = None,
    processing_time_ms: int | None = None
) -> dict:
    """
    Create standard success response.

    Args:
        data: The response data
        request_id: Optional correlation ID for tracing
        processing_time_ms: Optional processing time metric

    Returns:
        Standardized response dict
    """
    metadata = {
        "request_id": request_id,
        "timestamp": datetime.utcnow().isoformat(),
        "processing_time_ms": processing_time_ms,
        "version": "1.0"
    }
    return {
        "success": True,
        "data": data,
        "metadata": metadata
    }


def error_response(
    message: str,
    code: str | None = None,
    details: dict | None = None,
    request_id: str | None = None
) -> dict:
    """
    Create standard error response.

    Args:
        message: Error message
        code: Error code for programmatic handling
        details: Additional error details
        request_id: Optional correlation ID for tracing

    Returns:
        Standardized error response dict
    """
    metadata = {
        "request_id": request_id,
        "timestamp": datetime.utcnow().isoformat(),
        "version": "1.0"
    }
    return {
        "success": False,
        "error": message,
        "error_code": code,
        "details": details,
        "metadata": metadata
    }


def paginated_response(
    data: List[Any],
    total: int,
    page: int,
    page_size: int,
    request_id: str | None = None
) -> dict:
    """
    Create standard paginated response.

    Args:
        data: List of items for current page
        total: Total count of all items
        page: Current page number (1-indexed)
        page_size: Number of items per page
        request_id: Optional correlation ID for tracing

    Returns:
        Standardized paginated response dict
    """
    has_more = (page * page_size) < total
    metadata = {
        "request_id": request_id,
        "timestamp": datetime.utcnow().isoformat(),
        "version": "1.0"
    }
    return {
        "success": True,
        "data": data,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": has_more,
        "metadata": metadata
    }


# Additive metadata helper (Option A - non-breaking)
def add_metadata(response: dict, request_id: str | None = None) -> dict:
    """
    Add _metadata field to existing response without changing structure.

    This is the non-breaking approach (Option A) - adds metadata as a new field
    without wrapping the response in {"success": true, "data": {...}}.

    Example:
        Original: {"id": 1, "name": "John"}
        With metadata: {"id": 1, "name": "John", "_metadata": {"request_id": "abc"}}

    Args:
        response: Original response dict
        request_id: Optional correlation ID

    Returns:
        Response with _metadata field added
    """
    response["_metadata"] = {
        "request_id": request_id,
        "timestamp": datetime.utcnow().isoformat()
    }
    return response
