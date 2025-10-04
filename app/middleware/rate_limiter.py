"""
Rate limiting middleware for API endpoints.
Implements per-IP and per-endpoint rate limiting to prevent abuse.
"""

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from typing import Callable, Dict, Optional, Tuple
import time
import asyncio
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Simple in-memory rate limiter with sliding window.
    For production, use Redis-based implementation.
    """

    def __init__(
        self,
        requests_per_minute: int = 30,
        burst_size: int = 10,
        window_seconds: int = 60
    ):
        self.requests_per_minute = requests_per_minute
        self.burst_size = burst_size
        self.window_seconds = window_seconds
        self.request_history: Dict[str, list] = defaultdict(list)
        self._cleanup_task = None

    def _get_client_id(self, request: Request) -> str:
        """Extract client identifier from request."""
        # Try to get real IP from headers (for proxy/load balancer scenarios)
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()

        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip

        # Fallback to client host
        if request.client:
            return request.client.host

        return "unknown"

    def _cleanup_old_requests(self, client_id: str, current_time: float):
        """Remove requests older than the window."""
        cutoff_time = current_time - self.window_seconds
        self.request_history[client_id] = [
            timestamp for timestamp in self.request_history[client_id]
            if timestamp > cutoff_time
        ]

        # Clean up empty entries
        if not self.request_history[client_id]:
            del self.request_history[client_id]

    def is_allowed(self, request: Request) -> Tuple[bool, Optional[int]]:
        """
        Check if request is allowed under rate limits.

        Returns:
            Tuple of (is_allowed, retry_after_seconds)
        """
        client_id = self._get_client_id(request)
        current_time = time.time()

        # Clean up old requests
        self._cleanup_old_requests(client_id, current_time)

        # Get request history for this client
        request_times = self.request_history[client_id]

        # Check burst limit (requests in last few seconds)
        burst_window = 5  # 5 second burst window
        recent_requests = [
            t for t in request_times
            if t > current_time - burst_window
        ]

        if len(recent_requests) >= self.burst_size:
            # Calculate retry after
            oldest_burst = min(recent_requests)
            retry_after = int(burst_window - (current_time - oldest_burst)) + 1
            logger.warning(
                f"Rate limit burst exceeded for {client_id}: "
                f"{len(recent_requests)} requests in {burst_window}s"
            )
            return False, retry_after

        # Check overall rate limit
        if len(request_times) >= self.requests_per_minute:
            # Calculate retry after
            oldest_request = min(request_times)
            retry_after = int(self.window_seconds - (current_time - oldest_request)) + 1
            logger.warning(
                f"Rate limit exceeded for {client_id}: "
                f"{len(request_times)} requests in {self.window_seconds}s"
            )
            return False, retry_after

        # Request is allowed - record it
        self.request_history[client_id].append(current_time)
        return True, None

    async def __call__(self, request: Request, call_next: Callable) -> Response:
        """Middleware function to check rate limits."""
        # Skip rate limiting for health checks and test endpoints
        if request.url.path in ["/health", "/webhooks/evolution/test", "/docs", "/openapi.json"]:
            return await call_next(request)

        # Check rate limit
        is_allowed, retry_after = self.is_allowed(request)

        if not is_allowed:
            logger.info(f"Rate limit exceeded for {self._get_client_id(request)} on {request.url.path}")
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded",
                    "retry_after": retry_after
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(self.requests_per_minute),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(time.time()) + retry_after)
                }
            )

        # Process request
        response = await call_next(request)

        # Add rate limit headers to response
        client_id = self._get_client_id(request)
        remaining = max(0, self.requests_per_minute - len(self.request_history[client_id]))

        response.headers["X-RateLimit-Limit"] = str(self.requests_per_minute)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(int(time.time()) + self.window_seconds)

        return response


class EndpointRateLimiter:
    """
    Decorator for applying rate limits to specific endpoints.
    """

    def __init__(
        self,
        requests_per_minute: int = 30,
        burst_size: int = 10
    ):
        self.limiter = RateLimiter(
            requests_per_minute=requests_per_minute,
            burst_size=burst_size
        )

    def __call__(self, func: Callable) -> Callable:
        """Decorator to apply rate limiting to an endpoint."""
        async def wrapper(request: Request, *args, **kwargs):
            is_allowed, retry_after = self.limiter.is_allowed(request)

            if not is_allowed:
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded. Retry after {retry_after} seconds",
                    headers={"Retry-After": str(retry_after)}
                )

            return await func(request, *args, **kwargs)

        return wrapper


# Global rate limiter instances
default_limiter = RateLimiter(
    requests_per_minute=30,
    burst_size=10
)

strict_limiter = RateLimiter(
    requests_per_minute=10,
    burst_size=3
)

webhook_limiter = RateLimiter(
    requests_per_minute=60,  # Higher limit for webhooks
    burst_size=20
)


# Convenience decorators
def rate_limit(
    requests_per_minute: int = 30,
    burst_size: int = 10
):
    """
    Decorator to apply custom rate limiting to an endpoint.

    Usage:
        @router.post("/endpoint")
        @rate_limit(requests_per_minute=10, burst_size=3)
        async def endpoint(request: Request):
            ...
    """
    return EndpointRateLimiter(
        requests_per_minute=requests_per_minute,
        burst_size=burst_size
    )