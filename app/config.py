"""
Application Configuration
Centralized configuration for Redis and other services
"""
import os
from redis import Redis
from supabase import create_client, Client
from typing import Optional

# Redis Configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# Message history time windows (in hours)
MESSAGE_HISTORY_DEFAULT_WINDOW = int(os.getenv("MESSAGE_HISTORY_DEFAULT_WINDOW", "3"))
MESSAGE_HISTORY_MAX_WINDOW = int(os.getenv("MESSAGE_HISTORY_MAX_WINDOW", "24"))
MESSAGE_HISTORY_MAX_MESSAGES = int(os.getenv("MESSAGE_HISTORY_MAX_MESSAGES", "100"))
MESSAGE_HISTORY_MAX_TOKENS = int(os.getenv("MESSAGE_HISTORY_MAX_TOKENS", "4000"))

_supabase_client: Optional[Client] = None

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

def get_supabase_client() -> Optional[Client]:
    """
    Get cached Supabase client instance

    Returns:
        Client: Supabase client or None if not configured
    """
    global _supabase_client
    if _supabase_client is None:
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_ANON_KEY")

        if not url or not key:
            return None

        _supabase_client = create_client(url, key)

    return _supabase_client
