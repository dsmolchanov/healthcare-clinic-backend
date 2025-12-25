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

# LangGraph Integration - Always enabled (feature flags removed in Phase 2)
# All message processing flows through LangGraph orchestrator
# Lanes that use LangGraph routing (SCHEDULING/COMPLEX flows)
LANGGRAPH_ENABLED_LANES = ["SCHEDULING", "COMPLEX"]

# WhatsApp webhook routing (Phase 3b)
# When True, prefer token-based routing for new integrations
# Legacy instance-name routing remains available for backwards compatibility
USE_TOKEN_BASED_ROUTING = os.getenv("USE_TOKEN_BASED_ROUTING", "true").lower() == "true"

# Log deprecation warning for legacy routing
LOG_LEGACY_WEBHOOK_USAGE = os.getenv("LOG_LEGACY_WEBHOOK_USAGE", "true").lower() == "true"


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
