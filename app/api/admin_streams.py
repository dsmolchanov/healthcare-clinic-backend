"""
Admin endpoints for Redis Streams management
Provides operational tools for debugging and fixing queue issues

All endpoints require superadmin authentication.
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any, Optional
import os

from app.services.whatsapp_queue.queue import stream_key, dlq_key
from app.services.whatsapp_queue.config import CONSUMER_GROUP
from app.config import get_redis_client
from app.middleware.auth import require_superadmin, TokenPayload

router = APIRouter(prefix="/admin/streams", tags=["admin"])


def get_instance_name() -> str:
    """Get the instance name from environment"""
    return os.getenv("INSTANCE_NAME", "clinic-3e411ecb-3411-4add-91e2-8fa897310cb0-1757905315621")


@router.post("/reset-to-latest")
def reset_to_latest(instance: Optional[str] = None, _user: TokenPayload = Depends(require_superadmin())):
    """
    Reset consumer group to '$' (latest) - skip all existing messages.
    Use this to clear backlogs and start fresh from new messages only.
    """
    if not instance:
        instance = get_instance_name()

    try:
        r = get_redis_client()
        key = stream_key(instance)

        # Get current state
        try:
            groups = r.xinfo_groups(key)
            current_group = next((g for g in groups if g.get("name") == CONSUMER_GROUP), None)
            before_state = {
                "last_delivered": current_group.get("last-delivered-id") if current_group else "none",
                "pending": current_group.get("pending", 0) if current_group else 0
            }
        except Exception:
            before_state = {}

        # Reset to tail ($)
        r.xgroup_setid(key, CONSUMER_GROUP, id='$')

        # Get new state
        try:
            groups = r.xinfo_groups(key)
            current_group = next((g for g in groups if g.get("name") == CONSUMER_GROUP), None)
            after_state = {
                "last_delivered": current_group.get("last-delivered-id") if current_group else "none",
                "pending": current_group.get("pending", 0) if current_group else 0
            }
        except Exception:
            after_state = {}

        return {
            "ok": True,
            "mode": "latest",
            "instance": instance,
            "message": "Consumer group reset to tail - will only process new messages",
            "before": before_state,
            "after": after_state
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reset-to-begin")
def reset_to_begin(instance: Optional[str] = None, _user: TokenPayload = Depends(require_superadmin())):
    """
    Reset consumer group to '0' (beginning) - reprocess all messages.
    ⚠️ Use with caution: messages will be redelivered (relies on idempotency).
    """
    if not instance:
        instance = get_instance_name()

    try:
        r = get_redis_client()
        key = stream_key(instance)

        # Get current state
        try:
            groups = r.xinfo_groups(key)
            current_group = next((g for g in groups if g.get("name") == CONSUMER_GROUP), None)
            before_state = {
                "last_delivered": current_group.get("last-delivered-id") if current_group else "none",
                "pending": current_group.get("pending", 0) if current_group else 0
            }
        except Exception:
            before_state = {}

        # Reset to beginning
        r.xgroup_setid(key, CONSUMER_GROUP, id='0')

        # Get new state
        try:
            groups = r.xinfo_groups(key)
            current_group = next((g for g in groups if g.get("name") == CONSUMER_GROUP), None)
            after_state = {
                "last_delivered": current_group.get("last-delivered-id") if current_group else "none",
                "pending": current_group.get("pending", 0) if current_group else 0
            }
        except Exception:
            after_state = {}

        return {
            "ok": True,
            "mode": "begin",
            "instance": instance,
            "message": "Consumer group reset to beginning - will reprocess all messages",
            "warning": "Messages will be redelivered - ensure idempotency is working",
            "before": before_state,
            "after": after_state
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/destroy-recreate")
def destroy_recreate(instance: Optional[str] = None, _user: TokenPayload = Depends(require_superadmin())):
    """
    Destroy and recreate consumer group from scratch.
    ⚠️ DESTRUCTIVE: All pending message references are lost.
    Use this to completely reset and clear stuck states.
    """
    if not instance:
        instance = get_instance_name()

    try:
        r = get_redis_client()
        key = stream_key(instance)

        # Get before state
        try:
            groups_before = r.xinfo_groups(key)
            before_state = {
                "groups_count": len(groups_before),
                "consumers": sum(g.get("consumers", 0) for g in groups_before),
                "pending": sum(g.get("pending", 0) for g in groups_before)
            }
        except Exception:
            before_state = {"error": "Could not read before state"}

        # Destroy group
        try:
            r.xgroup_destroy(key, CONSUMER_GROUP)
            destroyed = True
        except Exception as e:
            if "no such key" in str(e).lower() or "NOGROUP" in str(e):
                destroyed = False  # Didn't exist
            else:
                raise

        # Recreate at tail ($)
        r.xgroup_create(key, CONSUMER_GROUP, id="$", mkstream=True)

        # Get after state
        try:
            groups_after = r.xinfo_groups(key)
            after_state = {
                "groups_count": len(groups_after),
                "consumers": sum(g.get("consumers", 0) for g in groups_after),
                "pending": sum(g.get("pending", 0) for g in groups_after)
            }
        except Exception:
            after_state = {"error": "Could not read after state"}

        return {
            "ok": True,
            "mode": "recreated",
            "instance": instance,
            "message": "Consumer group destroyed and recreated at tail",
            "destroyed": destroyed,
            "before": before_state,
            "after": after_state
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/claim-pending-to-worker")
def claim_pending_to_worker(instance: Optional[str] = None, worker_consumer: Optional[str] = None, _user: TokenPayload = Depends(require_superadmin())):
    """
    Claim all pending messages from idle/dead consumers to specified worker.
    Uses XAUTOCLAIM for efficient bulk transfer.

    Args:
        instance: WhatsApp instance name
        worker_consumer: Target consumer name (defaults to current worker)
    """
    if not instance:
        instance = get_instance_name()

    try:
        r = get_redis_client()
        key = stream_key(instance)

        # If no worker specified, try to get from app state
        if not worker_consumer:
            # Default to a pattern - in production this should come from the running worker
            import time
            worker_consumer = f"worker-{int(time.time())}"

        # Get pending summary
        try:
            pending_summary = r.xpending(key, CONSUMER_GROUP)
            pending_count = pending_summary.get("pending", 0)

            if pending_count == 0:
                return {
                    "ok": True,
                    "message": "No pending messages to claim",
                    "claimed": 0
                }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to get pending: {e}")

        # Use XAUTOCLAIM to bulk-claim messages
        # min_idle_time=0 means claim regardless of idle time
        try:
            # XAUTOCLAIM returns (next_id, claimed_messages, deleted_ids)
            result = r.xautoclaim(
                key,
                CONSUMER_GROUP,
                worker_consumer,
                min_idle_time=0,  # Claim all regardless of idle time
                start_id="0-0",   # Start from beginning
                count=100         # Max to claim at once
            )

            # Handle different Redis client versions
            if isinstance(result, tuple):
                next_id, claimed, deleted = result if len(result) == 3 else (result[0], result[1], [])
            else:
                next_id, claimed, deleted = "0-0", [], []

            claimed_count = len(claimed)

            return {
                "ok": True,
                "message": f"Claimed {claimed_count} messages",
                "claimed_count": claimed_count,
                "to_consumer": worker_consumer,
                "next_id": next_id,
                "deleted_count": len(deleted) if deleted else 0
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"XAUTOCLAIM failed: {e}")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
def streams_health(instance: Optional[str] = None, _user: TokenPayload = Depends(require_superadmin())):
    """
    Comprehensive health check for streams.
    Alerts if queue is stuck or worker is not consuming.
    """
    if not instance:
        instance = get_instance_name()

    try:
        r = get_redis_client()
        key = stream_key(instance)
        dlq = dlq_key(instance)

        # Get stream info
        try:
            queue_depth = r.xlen(key)
            dlq_depth = r.xlen(dlq)
        except Exception:
            queue_depth = 0
            dlq_depth = 0

        # Get group info
        try:
            groups = r.xinfo_groups(key)
            group = next((g for g in groups if g.get("name") == CONSUMER_GROUP), None)

            if group:
                consumers_count = group.get("consumers", 0)
                pending = group.get("pending", 0)
                last_delivered = group.get("last-delivered-id", "0-0")
            else:
                consumers_count = 0
                pending = 0
                last_delivered = "no_group"
        except Exception:
            consumers_count = 0
            pending = 0
            last_delivered = "error"

        # Get consumer details
        try:
            consumers = r.xinfo_consumers(key, CONSUMER_GROUP)
            consumer_details = [
                {
                    "name": c.get("name"),
                    "pending": c.get("pending", 0),
                    "idle_ms": c.get("idle", 0),
                    "idle_seconds": round(c.get("idle", 0) / 1000, 1)
                }
                for c in consumers
            ]
        except Exception:
            consumer_details = []

        # Determine health status
        issues = []

        if consumers_count == 0:
            issues.append("NO_ACTIVE_CONSUMERS")

        if queue_depth > 100:
            issues.append("HIGH_QUEUE_DEPTH")

        if pending > 0 and consumers_count == 0:
            issues.append("PENDING_WITHOUT_CONSUMER")

        if dlq_depth > 10:
            issues.append("HIGH_DLQ_DEPTH")

        # Check for stuck consumers (idle > 5 minutes with pending messages)
        for consumer in consumer_details:
            if consumer["pending"] > 0 and consumer["idle_ms"] > 300000:  # 5 min
                issues.append(f"STUCK_CONSUMER_{consumer['name']}")

        status = "healthy" if not issues else "degraded" if consumers_count > 0 else "unhealthy"

        return {
            "status": status,
            "instance": instance,
            "queue_depth": queue_depth,
            "dlq_depth": dlq_depth,
            "consumers_count": consumers_count,
            "pending": pending,
            "last_delivered_id": last_delivered,
            "consumers": consumer_details,
            "issues": issues,
            "recommendations": _get_recommendations(issues)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _get_recommendations(issues: list) -> list:
    """Generate actionable recommendations based on issues"""
    recs = []

    if "NO_ACTIVE_CONSUMERS" in issues:
        recs.append("Start or restart the worker process")

    if "HIGH_QUEUE_DEPTH" in issues:
        recs.append("Scale up workers or investigate slow processing")

    if "PENDING_WITHOUT_CONSUMER" in issues:
        recs.append("Use /claim-pending-to-worker to recover stuck messages")

    if "HIGH_DLQ_DEPTH" in issues:
        recs.append("Investigate DLQ messages for recurring failures")

    if any("STUCK_CONSUMER" in issue for issue in issues):
        recs.append("Restart stuck worker or use /claim-pending-to-worker")

    return recs