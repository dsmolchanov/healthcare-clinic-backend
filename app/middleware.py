"""
Middleware for rate limiting and request processing
"""

import time
import redis
from typing import Optional, Dict, Any
from slowapi import Limiter
from slowapi.util import get_remote_address
from fastapi import Request, HTTPException
import asyncio
import json


def configure_rate_limiting(app):
    """
    Configure rate limiting for the FastAPI app

    Args:
        app: FastAPI application instance
    """
    limiter = Limiter(key_func=get_remote_address)
    app.state.limiter = limiter
    return limiter


class DistributedRateLimiter:
    """
    Distributed rate limiter using Redis
    """

    def __init__(self, redis_client=None, limit: int = 30, window: int = 60):
        """
        Initialize distributed rate limiter

        Args:
            redis_client: Redis client instance
            limit: Number of requests allowed
            window: Time window in seconds
        """
        self.redis_client = redis_client or redis.Redis(
            host='localhost',
            port=6379,
            decode_responses=True
        )
        self.limit = limit
        self.window = window

    async def check_rate_limit(self, identifier: str) -> bool:
        """
        Check if request should be rate limited

        Args:
            identifier: Unique identifier (e.g., IP address)

        Returns:
            True if request is allowed, False if rate limited
        """
        key = f"rate_limit:{identifier}"

        try:
            # Get current count
            current = self.redis_client.get(key)

            if current is None:
                # First request, set counter with expiry
                self.redis_client.setex(key, self.window, 1)
                return True

            current_count = int(current)

            if current_count >= self.limit:
                # Rate limit exceeded
                return False

            # Increment counter
            self.redis_client.incr(key)
            return True

        except Exception as e:
            # On error, allow request but log
            print(f"Rate limit check error: {e}")
            return True


class RateLimiter:
    """
    Simple rate limiter for testing
    """

    def __init__(self, limit: int = 30, window: int = 60):
        self.limit = limit
        self.window = window
        self.requests = {}

    async def check_limit(self, identifier: str) -> bool:
        """
        Check if identifier has exceeded rate limit

        Args:
            identifier: Unique identifier (e.g., IP address)

        Returns:
            True if allowed, False if rate limited
        """
        current_time = time.time()

        # Clean old entries
        self.requests = {
            k: v for k, v in self.requests.items()
            if current_time - v['first_request'] < self.window
        }

        if identifier not in self.requests:
            # First request from this identifier
            self.requests[identifier] = {
                'count': 1,
                'first_request': current_time
            }
            return True

        request_data = self.requests[identifier]

        # Check if window has expired
        if current_time - request_data['first_request'] >= self.window:
            # Reset counter
            self.requests[identifier] = {
                'count': 1,
                'first_request': current_time
            }
            return True

        # Check if limit exceeded
        if request_data['count'] >= self.limit:
            return False

        # Increment counter
        request_data['count'] += 1
        return True
