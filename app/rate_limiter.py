"""
Rate limiting implementation for webhook and API protection.
"""

import time
import asyncio
from typing import Dict, Optional, Tuple
from collections import defaultdict, deque
from datetime import datetime, timedelta
import hashlib

class RateLimiter:
    """
    Token bucket rate limiter with sliding window.
    Supports both in-memory and Redis-based implementations.
    """

    def __init__(self, redis_client=None):
        """
        Initialize rate limiter.

        Args:
            redis_client: Optional Redis client for distributed rate limiting
        """
        self.redis_client = redis_client

        # In-memory storage for when Redis is not available
        self.buckets: Dict[str, deque] = defaultdict(deque)
        self.lock = asyncio.Lock()

    async def is_allowed(
        self,
        identifier: str,
        max_requests: int = 30,
        window_seconds: int = 60
    ) -> bool:
        """
        Check if a request is allowed under the rate limit.

        Args:
            identifier: Unique identifier (e.g., phone number, IP address)
            max_requests: Maximum number of requests allowed
            window_seconds: Time window in seconds

        Returns:
            True if request is allowed, False if rate limit exceeded
        """
        if self.redis_client:
            return await self._check_redis(identifier, max_requests, window_seconds)
        else:
            return await self._check_memory(identifier, max_requests, window_seconds)

    async def _check_memory(
        self,
        identifier: str,
        max_requests: int,
        window_seconds: int
    ) -> bool:
        """
        Check rate limit using in-memory storage.

        Args:
            identifier: Unique identifier
            max_requests: Maximum requests allowed
            window_seconds: Time window

        Returns:
            True if allowed, False if exceeded
        """
        async with self.lock:
            now = time.time()
            cutoff = now - window_seconds

            # Get or create bucket for this identifier
            bucket = self.buckets[identifier]

            # Remove expired timestamps
            while bucket and bucket[0] < cutoff:
                bucket.popleft()

            # Check if we're under the limit
            if len(bucket) >= max_requests:
                return False

            # Add current timestamp
            bucket.append(now)
            return True

    async def _check_redis(
        self,
        identifier: str,
        max_requests: int,
        window_seconds: int
    ) -> bool:
        """
        Check rate limit using Redis for distributed rate limiting.

        Args:
            identifier: Unique identifier
            max_requests: Maximum requests allowed
            window_seconds: Time window

        Returns:
            True if allowed, False if exceeded
        """
        try:
            # Hash the identifier for privacy
            key = f"rate_limit:{self._hash_identifier(identifier)}"

            # Use Redis pipeline for atomic operations
            pipe = self.redis_client.pipeline()
            now = time.time()
            cutoff = now - window_seconds

            # Remove old entries
            pipe.zremrangebyscore(key, 0, cutoff)

            # Count current entries
            pipe.zcard(key)

            # Execute pipeline
            results = pipe.execute()
            current_count = results[1]

            # Check if under limit
            if current_count >= max_requests:
                return False

            # Add new entry
            self.redis_client.zadd(key, {str(now): now})

            # Set expiry on the key
            self.redis_client.expire(key, window_seconds + 60)

            return True

        except Exception:
            # Fallback to in-memory if Redis fails
            return await self._check_memory(identifier, max_requests, window_seconds)

    def _hash_identifier(self, identifier: str) -> str:
        """
        Hash identifier for privacy.

        Args:
            identifier: Raw identifier

        Returns:
            Hashed identifier
        """
        return hashlib.sha256(identifier.encode()).hexdigest()[:16]

    async def reset(self, identifier: str) -> None:
        """
        Reset rate limit for an identifier.

        Args:
            identifier: Identifier to reset
        """
        if self.redis_client:
            key = f"rate_limit:{self._hash_identifier(identifier)}"
            self.redis_client.delete(key)
        else:
            async with self.lock:
                if identifier in self.buckets:
                    del self.buckets[identifier]

    async def get_remaining(
        self,
        identifier: str,
        max_requests: int = 30,
        window_seconds: int = 60
    ) -> Tuple[int, float]:
        """
        Get remaining requests and reset time.

        Args:
            identifier: Unique identifier
            max_requests: Maximum requests allowed
            window_seconds: Time window

        Returns:
            Tuple of (remaining_requests, seconds_until_reset)
        """
        if self.redis_client:
            key = f"rate_limit:{self._hash_identifier(identifier)}"
            now = time.time()
            cutoff = now - window_seconds

            # Remove old entries and count current
            pipe = self.redis_client.pipeline()
            pipe.zremrangebyscore(key, 0, cutoff)
            pipe.zcard(key)
            pipe.zrange(key, 0, 0)  # Get oldest entry
            results = pipe.execute()

            current_count = results[1]
            oldest_entries = results[2]

            remaining = max(0, max_requests - current_count)

            if oldest_entries:
                oldest_timestamp = float(oldest_entries[0])
                reset_time = (oldest_timestamp + window_seconds) - now
            else:
                reset_time = 0

            return remaining, max(0, reset_time)

        else:
            async with self.lock:
                now = time.time()
                cutoff = now - window_seconds
                bucket = self.buckets[identifier]

                # Remove expired
                while bucket and bucket[0] < cutoff:
                    bucket.popleft()

                remaining = max(0, max_requests - len(bucket))

                if bucket:
                    reset_time = (bucket[0] + window_seconds) - now
                else:
                    reset_time = 0

                return remaining, max(0, reset_time)

    async def cleanup_expired(self) -> int:
        """
        Clean up expired entries from all buckets.

        Returns:
            Number of entries cleaned
        """
        cleaned = 0

        if self.redis_client:
            # Redis handles expiry automatically
            return 0
        else:
            async with self.lock:
                now = time.time()

                for identifier, bucket in list(self.buckets.items()):
                    initial_size = len(bucket)

                    # Remove all expired entries
                    cutoff = now - 3600  # Clean entries older than 1 hour
                    while bucket and bucket[0] < cutoff:
                        bucket.popleft()

                    cleaned += initial_size - len(bucket)

                    # Remove empty buckets
                    if not bucket:
                        del self.buckets[identifier]

            return cleaned

class DistributedRateLimiter(RateLimiter):
    """
    Distributed rate limiter that always uses Redis.
    Raises exception if Redis is not available.
    """

    def __init__(self, redis_client):
        """
        Initialize distributed rate limiter.

        Args:
            redis_client: Required Redis client

        Raises:
            ValueError: If redis_client is None
        """
        if not redis_client:
            raise ValueError("Redis client is required for DistributedRateLimiter")

        super().__init__(redis_client)

    async def _check_memory(self, *args, **kwargs):
        """Override to prevent fallback to memory"""
        raise RuntimeError("DistributedRateLimiter requires Redis connection")
