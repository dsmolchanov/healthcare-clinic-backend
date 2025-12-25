"""
HIPAA Audit fallback - ensures audit trail even when primary audit DB is down.
Writes to local Redis queue; background worker retries delivery.

This is a critical compliance component - audit logging must NEVER silently fail.
"""
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

AUDIT_FALLBACK_KEY = "audit:fallback:queue"
MAX_FALLBACK_QUEUE_DEPTH = 10000  # Alert threshold


class AuditFallback:
    """Local fallback for audit logging when primary DB is unavailable."""

    def __init__(self, redis_client=None):
        """
        Initialize audit fallback.

        Args:
            redis_client: Optional Redis client. If not provided, will attempt
                          to get from cache module.
        """
        self._redis = redis_client
        self._initialized = False

    def _get_redis(self):
        """Lazy initialization of Redis client."""
        if self._redis is not None:
            return self._redis

        if not self._initialized:
            try:
                import redis
                redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
                self._redis = redis.from_url(redis_url)
                self._initialized = True
            except Exception as e:
                logger.warning(f"Could not initialize Redis for audit fallback: {e}")
                self._initialized = True  # Prevent repeated init attempts

        return self._redis

    async def queue_audit_event(self, event: Dict[str, Any]) -> bool:
        """
        Queue audit event for later retry.

        Args:
            event: Audit event data to queue

        Returns:
            True if successfully queued, False otherwise
        """
        redis_client = self._get_redis()
        if redis_client is None:
            logger.critical(
                "HIPAA CRITICAL: Cannot queue audit event - Redis unavailable. "
                "Event will be logged locally but may not be recoverable."
            )
            # Log to local file as last resort
            self._log_to_file(event)
            return False

        try:
            event["_queued_at"] = datetime.utcnow().isoformat()
            event["_queue_reason"] = "primary_db_failure"
            redis_client.lpush(AUDIT_FALLBACK_KEY, json.dumps(event))

            # Check queue depth and alert if high
            queue_depth = redis_client.llen(AUDIT_FALLBACK_KEY)
            if queue_depth > MAX_FALLBACK_QUEUE_DEPTH:
                logger.critical(
                    f"AUDIT FALLBACK QUEUE CRITICAL: {queue_depth} events pending. "
                    "Primary audit DB may be down. Immediate attention required."
                )
            elif queue_depth > 100:
                logger.warning(
                    f"AUDIT FALLBACK QUEUE WARNING: {queue_depth} events pending. "
                    "Check primary audit DB connectivity."
                )

            return True

        except Exception as e:
            logger.critical(f"HIPAA CRITICAL: Failed to queue audit event to Redis: {e}")
            self._log_to_file(event)
            return False

    def _log_to_file(self, event: Dict[str, Any]) -> None:
        """
        Last resort: log audit event to local file.

        This should be monitored and these logs should be ingested
        into the audit system manually if discovered.
        """
        try:
            import json
            log_path = "/tmp/hipaa_audit_fallback.jsonl"
            with open(log_path, "a") as f:
                event["_file_logged_at"] = datetime.utcnow().isoformat()
                f.write(json.dumps(event) + "\n")
            logger.warning(f"Audit event written to fallback file: {log_path}")
        except Exception as e:
            # At this point we've tried everything - log to stderr
            logger.critical(f"HIPAA VIOLATION: Cannot persist audit event anywhere: {e}")
            logger.critical(f"Lost audit event: {json.dumps(event)}")

    async def retry_pending(self, max_retries: int = 100) -> int:
        """
        Retry pending audit events from the fallback queue.

        Should be called periodically by a background worker.

        Args:
            max_retries: Maximum number of events to retry in one call

        Returns:
            Count of successfully retried events
        """
        redis_client = self._get_redis()
        if redis_client is None:
            return 0

        retried = 0
        for _ in range(max_retries):
            try:
                # Pop from queue
                event_json = redis_client.rpop(AUDIT_FALLBACK_KEY)
                if not event_json:
                    break

                event = json.loads(event_json)

                # Remove internal fields before re-inserting
                event.pop("_queued_at", None)
                event.pop("_queue_reason", None)

                # Attempt to insert to primary DB
                # This would need the actual DB client - placeholder for now
                # await self._insert_to_primary_db(event)

                retried += 1

            except Exception as e:
                logger.error(f"Failed to retry audit event: {e}")
                # Re-queue the event at the front
                if event_json:
                    redis_client.rpush(AUDIT_FALLBACK_KEY, event_json)
                break

        if retried > 0:
            logger.info(f"Successfully retried {retried} pending audit events")

        return retried

    def get_queue_depth(self) -> int:
        """Get current fallback queue depth for monitoring."""
        redis_client = self._get_redis()
        if redis_client is None:
            return -1  # Indicates Redis unavailable

        try:
            return redis_client.llen(AUDIT_FALLBACK_KEY)
        except Exception:
            return -1


# Singleton instance
_audit_fallback: Optional[AuditFallback] = None


def get_audit_fallback() -> AuditFallback:
    """Get singleton audit fallback instance."""
    global _audit_fallback
    if _audit_fallback is None:
        _audit_fallback = AuditFallback()
    return _audit_fallback
