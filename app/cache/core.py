"""
Unified caching layer - Redis-backed with local fallback.

This is the canonical cache interface. Use get_cache() for all caching needs.

Keep logic here, not in __init__.py to avoid circular imports.
"""
from typing import Optional
from app.cache.redis_manager import RedisManager

# Re-export as canonical interface
Cache = RedisManager

# Singleton instance (lazy initialization)
_cache: Optional[Cache] = None


def get_cache() -> Cache:
    """
    Get singleton cache instance.

    Uses lazy initialization to avoid import-time Redis connections.
    """
    global _cache
    if _cache is None:
        _cache = Cache()
    return _cache


def reset_cache_for_tests() -> None:
    """Reset singleton for test isolation."""
    global _cache
    _cache = None
