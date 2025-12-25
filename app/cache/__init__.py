"""
Cache package - re-exports only, no logic.

Use get_cache() for the canonical cache interface.
"""
from app.cache.core import Cache, get_cache, reset_cache_for_tests
from app.cache.warming import CacheWarmer
from app.cache.invalidation import CacheInvalidator

__all__ = ["Cache", "get_cache", "reset_cache_for_tests", "CacheWarmer", "CacheInvalidator"]
