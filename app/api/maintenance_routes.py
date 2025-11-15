"""
Maintenance API Routes

Endpoints for system maintenance tasks like cleaning up orphaned instances
"""

from fastapi import APIRouter, HTTPException
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/maintenance", tags=["maintenance"])


@router.post("/cleanup-orphaned-instances")
async def cleanup_orphaned_instances():
    """
    Manually trigger orphaned instance cleanup

    This endpoint:
    1. Finds Evolution instances not in database (orphaned Evolution instances)
    2. Finds database records not in Evolution (orphaned DB records)
    3. Cleans up both types of orphans

    Returns:
        Summary of cleanup results
    """
    try:
        from ..services.whatsapp_queue.orphan_cleanup import run_cleanup_job

        logger.info("Manual cleanup triggered via API")
        result = await run_cleanup_job()

        return {
            "success": True,
            "cleanup_summary": result
        }

    except Exception as e:
        logger.error(f"Error running cleanup job: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/orphaned-instances/check")
async def check_orphaned_instances():
    """
    Check for orphaned instances without cleaning them up

    Returns:
        List of orphaned instances found
    """
    try:
        from ..main import supabase
        from ..evolution_api import EvolutionAPIClient
        from ..services.whatsapp_queue.orphan_cleanup import OrphanedInstanceCleanup

        async with EvolutionAPIClient() as evolution_client:
            cleanup_service = OrphanedInstanceCleanup(supabase, evolution_client)

            # Find orphans without cleaning up
            orphaned_evolution = await cleanup_service.find_orphaned_evolution_instances()
            orphaned_db = await cleanup_service.find_orphaned_db_records()

            return {
                "success": True,
                "orphaned_evolution_instances": orphaned_evolution,
                "orphaned_db_records": [
                    {
                        "id": record["id"],
                        "instance_name": record["instance_name"],
                        "organization_id": record["organization_id"],
                        "status": record["status"]
                    }
                    for record in orphaned_db
                ]
            }

    except Exception as e:
        logger.error(f"Error checking orphaned instances: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/health-check")
async def run_health_check():
    """
    Run health check on all WhatsApp instances

    This endpoint:
    1. Checks connection status of all active instances
    2. Updates database with current status
    3. Detects and logs disconnections

    Returns:
        Summary of health check results
    """
    try:
        from ..services.whatsapp_queue.health_monitor import run_health_monitor

        logger.info("Manual health check triggered via API")
        result = await run_health_monitor()

        return {
            "success": True,
            "health_summary": result
        }

    except Exception as e:
        logger.error(f"Error running health check: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health-check/status")
async def get_instance_health_status():
    """
    Get current health status of all instances without running a check

    Returns:
        Current status from database
    """
    try:
        from ..main import supabase

        result = supabase.schema("healthcare").table("integrations").select(
            "id, organization_id, config, status, enabled, connected_at, disconnect_reason, updated_at"
        ).eq("type", "whatsapp").eq("provider", "evolution").execute()

        instances = []
        healthy_count = 0
        unhealthy_count = 0

        for record in result.data:
            instance_name = record.get("config", {}).get("instance_name", "unknown")
            status = record.get("status", "unknown")
            is_healthy = (status == "connected")

            if is_healthy:
                healthy_count += 1
            else:
                unhealthy_count += 1

            instances.append({
                "instance_name": instance_name,
                "organization_id": record.get("organization_id"),
                "status": status,
                "enabled": record.get("enabled", False),
                "healthy": is_healthy,
                "connected_at": record.get("connected_at"),
                "disconnect_reason": record.get("disconnect_reason"),
                "last_updated": record.get("updated_at")
            })

        return {
            "success": True,
            "summary": {
                "total": len(instances),
                "healthy": healthy_count,
                "unhealthy": unhealthy_count
            },
            "instances": instances
        }

    except Exception as e:
        logger.error(f"Error getting health status: {e}")
        raise HTTPException(status_code=500, detail=str(e))
