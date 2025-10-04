# clinics/backend/app/api/memory_health.py

from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any
import logging

from app.memory.conversation_memory import get_memory_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/memory", tags=["memory"])

@router.get("/health")
async def memory_health_check() -> Dict[str, Any]:
    """
    Check health of memory systems (mem0, Redis, Supabase)
    """

    manager = get_memory_manager()

    health = {
        "mem0": {
            "available": manager.mem0_available,
            "status": "unknown"
        },
        "supabase": {
            "available": True,  # Assume available if manager exists
            "status": "unknown"
        }
    }

    # Test mem0 connectivity
    if manager.mem0_available:
        try:
            test_memories = manager.memory.get_all(user_id="health_check_test", limit=1)
            health["mem0"]["status"] = "healthy"
            health["mem0"]["test_query_success"] = True
            health["mem0"]["sample_memory_count"] = len(test_memories)
        except Exception as e:
            health["mem0"]["status"] = "unhealthy"
            health["mem0"]["error"] = str(e)
            logger.error(f"mem0 health check failed: {e}")

    # Test Supabase connectivity
    try:
        # Simple query to test connection
        result = manager.supabase.table('conversation_sessions').select('id').limit(1).execute()
        health["supabase"]["status"] = "healthy"
        health["supabase"]["test_query_success"] = True
    except Exception as e:
        health["supabase"]["status"] = "unhealthy"
        health["supabase"]["error"] = str(e)
        logger.error(f"Supabase health check failed: {e}")

    # Overall health
    health["overall"] = "healthy" if all(
        h["status"] == "healthy" for h in health.values() if isinstance(h, dict)
    ) else "degraded"

    return health

@router.get("/stats/{phone_number}")
async def get_memory_stats(phone_number: str) -> Dict[str, Any]:
    """
    Get memory statistics for a specific user
    """

    manager = get_memory_manager()
    clean_phone = phone_number.replace("@s.whatsapp.net", "")

    stats = {
        "phone_number": clean_phone,
        "mem0_memories": 0,
        "supabase_sessions": 0,
        "supabase_messages": 0
    }

    # Count mem0 memories
    if manager.mem0_available:
        try:
            memories = manager.memory.get_all(user_id=clean_phone)
            stats["mem0_memories"] = len(memories)
            stats["mem0_available"] = True
        except Exception as e:
            stats["mem0_error"] = str(e)
            stats["mem0_available"] = False

    # Count Supabase records
    try:
        sessions = manager.supabase.table('conversation_sessions').select('id').eq(
            'user_identifier', clean_phone
        ).execute()
        stats["supabase_sessions"] = len(sessions.data)

        if sessions.data:
            session_ids = [s['id'] for s in sessions.data]
            messages = manager.supabase.table('conversation_messages').select('id').in_(
                'session_id', session_ids
            ).execute()
            stats["supabase_messages"] = len(messages.data)
    except Exception as e:
        stats["supabase_error"] = str(e)

    return stats
