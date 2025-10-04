"""
Application Configuration
Centralized configuration for Redis and other services
"""
import os
from redis import Redis

# Redis Configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

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