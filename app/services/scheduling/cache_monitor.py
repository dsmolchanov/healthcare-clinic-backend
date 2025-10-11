"""
Cache Performance Monitoring Module.

Provides utilities for monitoring cache hit/miss rates and performance
to ensure cache effectiveness (target: >90% hit rate).
"""

import logging
from datetime import datetime, timedelta
from functools import wraps
from typing import Callable, Any, Dict

logger = logging.getLogger(__name__)


class CacheMonitor:
    """
    Monitor cache hit/miss rates.

    Tracks cache hits and misses to calculate effectiveness metrics
    and identify optimization opportunities.
    """

    def __init__(self):
        """Initialize cache monitor with zero hits/misses."""
        self.hits = 0
        self.misses = 0
        self.last_reset = datetime.now()

    def record_hit(self):
        """Record a cache hit."""
        self.hits += 1
        logger.debug("cache.hit", extra={"total_hits": self.hits})

    def record_miss(self):
        """Record a cache miss."""
        self.misses += 1
        logger.debug("cache.miss", extra={"total_misses": self.misses})

    def get_hit_rate(self) -> float:
        """
        Calculate cache hit rate.

        Returns:
            Hit rate as float between 0.0 and 1.0
        """
        total = self.hits + self.misses
        if total == 0:
            return 0.0
        return self.hits / total

    def report(self) -> Dict[str, Any]:
        """
        Generate cache performance report.

        Returns:
            Dict with hits, misses, hit_rate, total_requests, and period_minutes
        """
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": self.get_hit_rate(),
            "total_requests": self.hits + self.misses,
            "period_minutes": (datetime.now() - self.last_reset).total_seconds() / 60,
            "meets_target": self.get_hit_rate() >= 0.90
        }

    def reset(self):
        """Reset cache statistics."""
        self.hits = 0
        self.misses = 0
        self.last_reset = datetime.now()
        logger.info("cache.reset", extra={"message": "Cache statistics reset"})


def cached_with_monitoring(cache_key_fn: Callable, ttl_seconds: int = 60):
    """
    Decorator for cached functions with monitoring.

    Provides automatic caching with TTL and monitors hit/miss rates.

    Args:
        cache_key_fn: Function to generate cache key from args/kwargs
        ttl_seconds: Time-to-live for cache entries (default: 60)

    Returns:
        Decorated async function with caching and monitoring

    Example:
        def make_key(clinic_id):
            return f"settings:{clinic_id}"

        @cached_with_monitoring(make_key, ttl_seconds=60)
        async def get_settings(clinic_id: UUID) -> Dict:
            # Database query
            pass
    """
    cache: Dict[str, Any] = {}
    cache_timestamps: Dict[str, datetime] = {}
    monitor = CacheMonitor()

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            key = cache_key_fn(*args, **kwargs)
            now = datetime.now()

            # Check cache
            if key in cache:
                timestamp = cache_timestamps.get(key, datetime.min)
                if now - timestamp < timedelta(seconds=ttl_seconds):
                    monitor.record_hit()
                    logger.debug(f"cache.lookup.hit", extra={
                        "key": key,
                        "age_seconds": (now - timestamp).total_seconds()
                    })
                    return cache[key]

            # Cache miss - call original function
            monitor.record_miss()
            logger.debug(f"cache.lookup.miss", extra={"key": key})

            result = await func(*args, **kwargs)

            # Update cache
            cache[key] = result
            cache_timestamps[key] = now

            return result

        # Attach monitor to wrapper for external access
        wrapper.cache_monitor = monitor
        wrapper.clear_cache = lambda: (cache.clear(), cache_timestamps.clear())

        return wrapper

    return decorator


class GlobalCacheMonitor:
    """
    Global cache monitor for tracking all cached operations.

    Aggregates metrics from multiple cache monitors across the application.
    """

    def __init__(self):
        """Initialize global cache monitor."""
        self.monitors: Dict[str, CacheMonitor] = {}

    def register(self, name: str, monitor: CacheMonitor):
        """
        Register a cache monitor.

        Args:
            name: Name for this cache (e.g., "settings_cache")
            monitor: CacheMonitor instance
        """
        self.monitors[name] = monitor
        logger.info(f"cache.register", extra={"cache_name": name})

    def report(self) -> Dict[str, Dict[str, Any]]:
        """
        Generate report for all registered caches.

        Returns:
            Dict mapping cache names to their reports
        """
        return {
            name: monitor.report()
            for name, monitor in self.monitors.items()
        }

    def get_overall_hit_rate(self) -> float:
        """
        Calculate overall hit rate across all caches.

        Returns:
            Overall hit rate as float between 0.0 and 1.0
        """
        total_hits = sum(m.hits for m in self.monitors.values())
        total_misses = sum(m.misses for m in self.monitors.values())
        total = total_hits + total_misses

        if total == 0:
            return 0.0

        return total_hits / total


# Global instance for application-wide cache monitoring
global_cache_monitor = GlobalCacheMonitor()
