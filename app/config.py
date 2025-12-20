"""
Application Configuration
Centralized configuration for Redis and other services
"""
import os
import warnings
from redis import Redis
from typing import Optional
from supabase import Client

# Redis Configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# Message history time windows (in hours)
MESSAGE_HISTORY_DEFAULT_WINDOW = int(os.getenv("MESSAGE_HISTORY_DEFAULT_WINDOW", "3"))
MESSAGE_HISTORY_MAX_WINDOW = int(os.getenv("MESSAGE_HISTORY_MAX_WINDOW", "24"))
MESSAGE_HISTORY_MAX_MESSAGES = int(os.getenv("MESSAGE_HISTORY_MAX_MESSAGES", "100"))
MESSAGE_HISTORY_MAX_TOKENS = int(os.getenv("MESSAGE_HISTORY_MAX_TOKENS", "4000"))

# LangGraph Integration (Phase 3B)
# Feature flag for gradual rollout
ENABLE_LANGGRAPH = os.getenv("ENABLE_LANGGRAPH", "false").lower() == "true"

# Clinic whitelist for gradual rollout (comma-separated clinic IDs)
# Empty string means LangGraph is disabled for all clinics regardless of ENABLE_LANGGRAPH
LANGGRAPH_CLINIC_WHITELIST_STR = os.getenv("LANGGRAPH_CLINIC_WHITELIST", "")
LANGGRAPH_CLINIC_WHITELIST = [
    c.strip() for c in LANGGRAPH_CLINIC_WHITELIST_STR.split(",")
    if c.strip()
]

# Lanes that trigger LangGraph routing (SCHEDULING/COMPLEX flows benefit most)
LANGGRAPH_ENABLED_LANES = os.getenv("LANGGRAPH_ENABLED_LANES", "SCHEDULING,COMPLEX").split(",")


def get_redis_client() -> Redis:
    """
    Get configured Redis client with optimized settings

    Returns:
        Redis: Configured Redis client instance
    """
    return Redis.from_url(
        REDIS_URL,
        decode_responses=True,  # Automatically decode responses to strings
        socket_connect_timeout=5,  # 5 second connection timeout
        socket_timeout=5,  # 5 second operation timeout
        retry_on_timeout=True,  # Retry operations that timeout
        health_check_interval=30  # Health check every 30 seconds
    )


# DEPRECATED - use app.database instead
def get_supabase_client() -> Optional[Client]:
    """
    DEPRECATED: Use app.database.get_healthcare_client() or get_main_client() instead.

    This function does not support schema selection and defaults to public.
    """
    warnings.warn(
        "get_supabase_client() is deprecated. Use app.database.get_healthcare_client() "
        "or app.database.get_main_client() instead.",
        DeprecationWarning,
        stacklevel=2
    )
    from app.database import get_main_client
    return get_main_client()
