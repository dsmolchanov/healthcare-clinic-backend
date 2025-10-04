"""
Pricing Cache Module
Fast in-memory cache for service pricing to eliminate DB work on hot path.
Refreshes in background with 15-30min TTL.
"""
from datetime import datetime, timedelta
import asyncio
import logging
from typing import Dict, Any, Optional, Callable

logger = logging.getLogger(__name__)

# Module-level cache state
_cache: Dict[str, Any] = {}
_ts: datetime = datetime.min
_lock = asyncio.Lock()

TTL = timedelta(minutes=20)


async def get_prices(fetch_fn: Callable[[], Any]) -> Dict[str, Any]:
    """
    Get prices from cache or refresh if stale.

    Args:
        fetch_fn: Async function to fetch prices from Supabase

    Returns:
        Dict mapping service code to service data
    """
    global _cache, _ts

    # Fast path: return cached data if still fresh
    if datetime.utcnow() - _ts < TTL and _cache:
        logger.debug("Price cache HIT (age: %s)", datetime.utcnow() - _ts)
        return _cache

    # Slow path: acquire lock and refresh
    async with _lock:
        # Double-check after acquiring lock
        if datetime.utcnow() - _ts < TTL and _cache:
            logger.debug("Price cache HIT after lock (age: %s)", datetime.utcnow() - _ts)
            return _cache

        # Actually refresh
        try:
            logger.info("Refreshing price cache...")
            data = await fetch_fn()

            # Convert list to dict for fast lookup
            if isinstance(data, list):
                _cache = {row["code"]: row for row in data if "code" in row}
            else:
                _cache = data

            _ts = datetime.utcnow()
            logger.info("✅ Price cache refreshed (%d services)", len(_cache))
            return _cache

        except Exception as e:
            logger.error(f"Failed to refresh price cache: {e}")
            # If we have stale cache, return it anyway (degraded mode)
            if _cache:
                logger.warning("Returning stale cache due to refresh failure")
                return _cache
            raise


async def warmup(fetch_fn: Callable[[], Any]) -> None:
    """
    Warm up the cache on startup.

    Args:
        fetch_fn: Async function to fetch prices from Supabase
    """
    logger.info("Warming up price cache...")
    await get_prices(fetch_fn)


def invalidate() -> None:
    """Invalidate the cache (for testing or manual refresh)"""
    global _cache, _ts
    _cache = {}
    _ts = datetime.min
    logger.info("Price cache invalidated")


def get_cache_age() -> Optional[timedelta]:
    """Get the age of the current cache"""
    if not _cache:
        return None
    return datetime.utcnow() - _ts


def is_cached() -> bool:
    """Check if cache is populated"""
    return bool(_cache)
