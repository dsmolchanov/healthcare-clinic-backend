"""
WhatsApp Queue-Worker Configuration
Centralized configuration for queue system
"""
import os
import logging

# Inherit Redis URL from main app config
from app.config import REDIS_URL

# Evolution API settings
# Support both EVOLUTION_API_URL and EVOLUTION_SERVER_URL for compatibility
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL") or os.getenv("EVOLUTION_SERVER_URL", "https://evolution-api-prod.fly.dev")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "evolution_api_key_2024")

# Consumer group and delivery settings
CONSUMER_GROUP = os.getenv("WA_CONSUMER_GROUP", "wa_workers")
MAX_DELIVERIES = int(os.getenv("WA_MAX_DELIVERIES", "5"))

# Retry and backoff settings
BASE_BACKOFF = float(os.getenv("WA_BASE_BACKOFF", "2.0"))  # seconds
MAX_BACKOFF = float(os.getenv("WA_MAX_BACKOFF", "60.0"))  # seconds

# Rate limiting (conservative to avoid WhatsApp bans)
TOKENS_PER_SECOND = float(os.getenv("WA_TOKENS_PER_SECOND", "1.0"))  # 1 message per second per instance
BUCKET_CAPACITY = int(os.getenv("WA_BUCKET_CAPACITY", "5"))  # allow burst of 5 messages

# HTTP timeouts
EVOLUTION_HTTP_TIMEOUT = float(os.getenv("WA_EVOLUTION_HTTP_TIMEOUT", "15.0"))  # seconds

# Instance change notification channels
INSTANCE_ADDED_CHANNEL = "wa:instances:added"
INSTANCE_REMOVED_CHANNEL = "wa:instances:removed"

# Logging configuration
logger = logging.getLogger("whatsapp_queue")
logger.setLevel(logging.INFO)

# Add handler if not already configured
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "[%(asctime)s] [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)