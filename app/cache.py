"""
Caching for performance optimization
"""

import redis
from typing import Any, Optional


class RedisCache:
    """Redis-based cache"""

    def __init__(self, redis_client=None):
        self.redis_client = redis_client or redis.Redis(
            host='localhost',
            port=6379,
            decode_responses=True
        )

    def get(self, key: str) -> Optional[Any]:
        """Get value from cache"""
        return self.redis_client.get(key)

    def set(self, key: str, value: Any, ttl: int = 300):
        """Set value in cache with TTL"""
        self.redis_client.setex(key, ttl, value)
