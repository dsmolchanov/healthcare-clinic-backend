"""
Unified rate limiting - canonical interface.

This module provides a single entry point for all rate limiting.

Usage:
    from app.middleware.rate_limiting import RateLimiter, webhook_limiter
"""
from typing import Optional

# Re-export from middleware rate limiter (FastAPI-focused)
from app.middleware.rate_limiter import RateLimiter as MiddlewareRateLimiter
from app.middleware.rate_limiter import webhook_limiter

# Re-export from generic rate limiter (Redis-capable)
from app.rate_limiter import RateLimiter as GenericRateLimiter

# Type aliases
RateLimiter = GenericRateLimiter  # Keep backwards compatibility

# Singleton instances
_webhook_limiter: Optional[MiddlewareRateLimiter] = None


def get_webhook_limiter() -> MiddlewareRateLimiter:
    """Get singleton webhook rate limiter."""
    global _webhook_limiter
    if _webhook_limiter is None:
        _webhook_limiter = MiddlewareRateLimiter(
            requests_per_minute=60,
            burst_size=10
        )
    return _webhook_limiter


def reset_for_tests() -> None:
    """Reset singletons for test isolation."""
    global _webhook_limiter
    _webhook_limiter = None
