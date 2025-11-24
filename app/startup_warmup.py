"""
Startup Warmup Script

Preloads critical data into Redis cache on application startup:
- All clinic doctors
- All clinic services
- All clinic FAQs

This ensures the first user request gets cached data, not DB queries.
"""

import logging
import asyncio
from typing import List, Optional, Dict, Any
from app.db.supabase_client import get_supabase_client
from app.config import get_redis_client
from app.services.clinic_data_cache import ClinicDataCache

logger = logging.getLogger(__name__)


async def warmup_clinic_data(clinic_ids: List[str] = None):
    """
    Warm up Redis cache with clinic data

    Args:
        clinic_ids: List of clinic IDs to warm up. If None, warms up all clinics.
    """
    try:
        supabase = get_supabase_client()
        redis = get_redis_client()
        cache = ClinicDataCache(redis, default_ttl=3600)  # 1 hour TTL

        # Get all clinic IDs if not provided
        if not clinic_ids:
            result = supabase.table('clinics').select('id').eq('is_active', True).execute()
            clinic_ids = [c['id'] for c in result.data] if result.data else []

        logger.info(f"🔥 Starting warmup for {len(clinic_ids)} clinic(s)...")

        total_doctors = 0
        total_services = 0
        total_faqs = 0

        for clinic_id in clinic_ids:
            try:
                import json

                # OPTIMIZATION: Use get_clinic_bundle RPC for single database call
                # This replaces 4 separate queries (clinic, doctors, services, faqs) with 1 RPC
                # NOTE: RPC is in healthcare schema
                from app.database import get_supabase
                healthcare_supabase = await get_supabase(schema='healthcare')
                result = healthcare_supabase.rpc('get_clinic_bundle', {'p_clinic_id': clinic_id}).execute()

                if not result.data:
                    logger.warning(f"No data returned for clinic {clinic_id}")
                    continue

                bundle = result.data
                clinic_info = bundle.get('clinic', {})
                doctors = bundle.get('doctors', [])
                services = bundle.get('services', [])
                faqs = bundle.get('faqs', [])

                # Cache clinic info and collections directly in Redis
                cache_key_info = f"clinic:{clinic_id}:info:v2"
                cache_key_doctors = f"clinic:{clinic_id}:doctors"
                cache_key_services = f"clinic:{clinic_id}:services"
                cache_key_faqs = f"clinic:{clinic_id}:faqs"

                redis.setex(cache_key_info, 3600, json.dumps(clinic_info))
                redis.setex(cache_key_doctors, 3600, json.dumps(doctors))
                redis.setex(cache_key_services, 3600, json.dumps(services))
                redis.setex(cache_key_faqs, 3600, json.dumps(faqs))

                total_doctors += len(doctors)
                total_services += len(services)
                total_faqs += len(faqs)

                logger.info(
                    f"✅ Warmed clinic {clinic_id[:8]}...: "
                    f"{len(doctors)} doctors, {len(services)} services, {len(faqs)} FAQs, "
                    f"clinic info ({clinic_info.get('name', 'N/A')}) [1 RPC call]"
                )

            except Exception as e:
                logger.error(f"❌ Failed to warm clinic {clinic_id}: {e}")

        logger.info(
            f"🎉 Warmup complete! Total cached: "
            f"{total_doctors} doctors, {total_services} services, {total_faqs} FAQs"
        )

        return True

    except Exception as e:
        logger.error(f"❌ Warmup failed: {e}")
        return False


async def warmup_organization_data(organization_id: str):
    """
    Warm up data for a specific organization (finds all its clinics)
    """
    try:
        supabase = get_supabase_client()

        # Get all clinics for this organization
        result = supabase.table('clinics').select('id').eq(
            'organization_id', organization_id
        ).eq('is_active', True).execute()

        clinic_ids = [c['id'] for c in result.data] if result.data else []

        if clinic_ids:
            await warmup_clinic_data(clinic_ids)
        else:
            logger.warning(f"No clinics found for organization {organization_id}")

    except Exception as e:
        logger.error(f"Failed to warm organization {organization_id}: {e}")


async def warmup_mem0_vector_indices(
    clinic_ids: Optional[List[str]] = None,
    *,
    force: bool = False,
    throttle_ms: int = 50
) -> Dict[str, Any]:
    """Enqueue mem0 warmups for active clinics to avoid cold starts."""

    summary: Dict[str, Any] = {
        "scheduled": 0,
        "total": 0,
        "available": False,
        "results": {},
        "force": force,
    }

    try:
        from app.memory.conversation_memory import get_memory_manager
        mem_manager = get_memory_manager()
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning(f"mem0 warmup unavailable (manager init failed): {exc}")
        summary["error"] = str(exc)
        return summary

    # Attempt to initialize mem0 once before scheduling jobs
    try:
        mem_manager._ensure_mem0_initialized()
    except Exception as exc:  # pragma: no cover - safety against unexpected failure
        logger.warning(f"mem0 initialization during warmup failed: {exc}")

    if clinic_ids is None:
        try:
            supabase = get_supabase_client()
            result = supabase.table('clinics').select('id').eq('is_active', True).execute()
            clinic_ids = [row['id'] for row in (result.data or []) if row.get('id')]
        except Exception as exc:
            logger.warning(f"Unable to fetch clinics for mem0 warmup: {exc}")
            summary["error"] = f"clinic_lookup_failed: {exc}"
            clinic_ids = []

    clinic_ids = clinic_ids or []
    summary["total"] = len(clinic_ids)

    if not mem_manager.mem0_available or not mem_manager.memory:
        logger.info("Skipping mem0 warmup scheduler: mem0 not available")
        summary["available"] = False
        return summary

    if not clinic_ids:
        logger.info("No clinics available for mem0 warmup")
        summary["available"] = mem_manager.mem0_available
        return summary

    results = await mem_manager.warmup_multiple_clinics(
        clinic_ids,
        force=force,
        throttle_ms=throttle_ms
    )

    scheduled = sum(1 for ok in results.values() if ok)

    summary.update({
        "scheduled": scheduled,
        "available": mem_manager.mem0_available,
        "results": results,
    })

    logger.info(
        "mem0 warmup scheduled for %s/%s clinics (force=%s)",
        scheduled,
        len(clinic_ids),
        force,
    )

    return summary


async def warmup_whatsapp_instance_cache() -> Dict[str, Any]:
    """
    Warm up WhatsApp instance → clinic mapping cache

    This eliminates DB queries on every incoming message by preloading
    all WhatsApp instance configurations into Redis.

    Returns:
        Dict with warmup statistics
    """
    try:
        from app.services.whatsapp_clinic_cache import get_whatsapp_clinic_cache

        cache = get_whatsapp_clinic_cache()
        stats = await cache.warmup_all_instances()

        logger.info(
            f"🎉 WhatsApp cache warmup: {stats.get('cached', 0)}/{stats.get('total', 0)} instances"
        )

        return stats
    except Exception as e:
        logger.error(f"❌ WhatsApp cache warmup failed: {e}")
        return {"error": str(e), "cached": 0, "total": 0}


def warmup_all_clinics_sync():
    """
    Synchronous wrapper for warmup - can be called from FastAPI startup
    """
    try:
        asyncio.run(warmup_clinic_data())
    except Exception as e:
        logger.error(f"Warmup error: {e}")


def warmup_all_sync():
    """
    Comprehensive warmup: clinics data + WhatsApp cache + mem0
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # Warmup clinic data
        logger.info("🔥 Starting comprehensive warmup...")
        loop.run_until_complete(warmup_clinic_data())

        # Warmup WhatsApp cache
        loop.run_until_complete(warmup_whatsapp_instance_cache())

        # Warmup mem0 (if available)
        loop.run_until_complete(warmup_mem0_vector_indices())

        logger.info("✅ All warmup tasks completed")
        loop.close()
    except Exception as e:
        logger.error(f"Warmup error: {e}")
