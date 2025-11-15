"""
Orphaned Instance Cleanup Service

This service periodically checks for and removes orphaned Evolution instances:
1. Instances in Evolution but not in database (orphaned Evolution instances)
2. Instances in database but not in Evolution (orphaned DB records)

PREVENTION STRATEGY 3: Periodic cleanup of orphaned instances
"""

import asyncio
import logging
from typing import List, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class OrphanedInstanceCleanup:
    """Service to clean up orphaned WhatsApp instances"""

    def __init__(self, supabase_client, evolution_client):
        """
        Initialize cleanup service

        Args:
            supabase_client: Supabase client instance
            evolution_client: EvolutionAPIClient instance
        """
        self.supabase = supabase_client
        self.evolution = evolution_client

    async def find_orphaned_evolution_instances(self) -> List[str]:
        """
        Find Evolution instances that exist in Evolution but not in database

        Returns:
            List of orphaned instance names in Evolution
        """
        try:
            # Get all Evolution instances
            all_evolution_instances = await self.evolution.fetch_all_instances()
            evolution_instance_names = {
                inst.get("instance", {}).get("instanceName")
                for inst in all_evolution_instances
                if inst.get("instance", {}).get("instanceName")
            }

            logger.info(f"Found {len(evolution_instance_names)} instances in Evolution")

            # Get all database instances
            db_result = self.supabase.schema("healthcare").table("integrations").select(
                "config"
            ).eq("type", "whatsapp").eq("provider", "evolution").execute()

            db_instance_names = {
                record.get("config", {}).get("instance_name")
                for record in db_result.data
                if record.get("config", {}).get("instance_name")
            }

            logger.info(f"Found {len(db_instance_names)} instances in database")

            # Find orphans: in Evolution but not in DB
            orphaned = evolution_instance_names - db_instance_names

            if orphaned:
                logger.warning(f"Found {len(orphaned)} orphaned Evolution instances: {orphaned}")
            else:
                logger.info("No orphaned Evolution instances found")

            return list(orphaned)

        except Exception as e:
            logger.error(f"Error finding orphaned Evolution instances: {e}")
            return []

    async def find_orphaned_db_records(self) -> List[Dict[str, Any]]:
        """
        Find database records that exist in DB but not in Evolution

        Returns:
            List of orphaned database records (dicts with id and instance_name)
        """
        try:
            # Get all database instances
            db_result = self.supabase.schema("healthcare").table("integrations").select(
                "id, organization_id, config, status"
            ).eq("type", "whatsapp").eq("provider", "evolution").execute()

            db_instances = [
                {
                    "id": record["id"],
                    "organization_id": record.get("organization_id"),
                    "instance_name": record.get("config", {}).get("instance_name"),
                    "status": record.get("status")
                }
                for record in db_result.data
                if record.get("config", {}).get("instance_name")
            ]

            logger.info(f"Checking {len(db_instances)} database records")

            # Check each DB instance against Evolution
            orphaned = []
            for db_instance in db_instances:
                instance_name = db_instance["instance_name"]
                try:
                    status = await self.evolution.get_instance_status(instance_name)

                    if not status.get("exists"):
                        logger.warning(f"Found orphaned DB record: {instance_name} (not in Evolution)")
                        orphaned.append(db_instance)
                except Exception as check_error:
                    logger.error(f"Error checking instance {instance_name}: {check_error}")

            if orphaned:
                logger.warning(f"Found {len(orphaned)} orphaned database records")
            else:
                logger.info("No orphaned database records found")

            return orphaned

        except Exception as e:
            logger.error(f"Error finding orphaned DB records: {e}")
            return []

    async def cleanup_orphaned_evolution_instances(self, orphaned_instances: List[str]) -> Dict[str, int]:
        """
        Delete orphaned instances from Evolution

        Args:
            orphaned_instances: List of instance names to delete

        Returns:
            dict with 'deleted' and 'failed' counts
        """
        results = {"deleted": 0, "failed": 0}

        for instance_name in orphaned_instances:
            try:
                logger.info(f"Deleting orphaned Evolution instance: {instance_name}")
                result = await self.evolution.delete_instance(instance_name)

                if result.get("success"):
                    results["deleted"] += 1
                    logger.info(f"✅ Deleted orphaned instance: {instance_name}")
                else:
                    results["failed"] += 1
                    logger.error(f"❌ Failed to delete {instance_name}: {result.get('error')}")

            except Exception as e:
                results["failed"] += 1
                logger.error(f"❌ Error deleting {instance_name}: {e}")

        return results

    async def cleanup_orphaned_db_records(self, orphaned_records: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Delete orphaned database records

        Args:
            orphaned_records: List of DB records to delete

        Returns:
            dict with 'deleted' and 'failed' counts
        """
        results = {"deleted": 0, "failed": 0}

        for record in orphaned_records:
            try:
                record_id = record["id"]
                instance_name = record["instance_name"]

                logger.info(f"Deleting orphaned DB record: {instance_name} (id: {record_id})")

                self.supabase.schema("healthcare").table("integrations").delete().eq(
                    "id", record_id
                ).execute()

                results["deleted"] += 1
                logger.info(f"✅ Deleted orphaned DB record: {instance_name}")

                # Invalidate cache
                try:
                    from ..whatsapp_clinic_cache import get_whatsapp_clinic_cache
                    cache = get_whatsapp_clinic_cache()
                    await cache.invalidate_instance(instance_name)
                except Exception as cache_error:
                    logger.warning(f"Failed to invalidate cache for {instance_name}: {cache_error}")

            except Exception as e:
                results["failed"] += 1
                logger.error(f"❌ Error deleting DB record {record.get('id')}: {e}")

        return results

    async def run_cleanup(self) -> Dict[str, Any]:
        """
        Run full cleanup process

        Returns:
            Summary of cleanup results
        """
        start_time = datetime.utcnow()
        logger.info("=" * 80)
        logger.info(f"Starting orphaned instance cleanup at {start_time.isoformat()}")
        logger.info("=" * 80)

        summary = {
            "started_at": start_time.isoformat(),
            "orphaned_evolution_instances": {
                "found": 0,
                "deleted": 0,
                "failed": 0
            },
            "orphaned_db_records": {
                "found": 0,
                "deleted": 0,
                "failed": 0
            }
        }

        try:
            # Find and clean up orphaned Evolution instances
            orphaned_evolution = await self.find_orphaned_evolution_instances()
            summary["orphaned_evolution_instances"]["found"] = len(orphaned_evolution)

            if orphaned_evolution:
                evolution_results = await self.cleanup_orphaned_evolution_instances(orphaned_evolution)
                summary["orphaned_evolution_instances"]["deleted"] = evolution_results["deleted"]
                summary["orphaned_evolution_instances"]["failed"] = evolution_results["failed"]

            # Find and clean up orphaned DB records
            orphaned_db = await self.find_orphaned_db_records()
            summary["orphaned_db_records"]["found"] = len(orphaned_db)

            if orphaned_db:
                db_results = await self.cleanup_orphaned_db_records(orphaned_db)
                summary["orphaned_db_records"]["deleted"] = db_results["deleted"]
                summary["orphaned_db_records"]["failed"] = db_results["failed"]

        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            summary["error"] = str(e)

        end_time = datetime.utcnow()
        duration = (end_time - start_time).total_seconds()

        summary["completed_at"] = end_time.isoformat()
        summary["duration_seconds"] = duration

        logger.info("=" * 80)
        logger.info(f"Cleanup completed in {duration:.2f}s")
        logger.info(f"Evolution instances: {summary['orphaned_evolution_instances']}")
        logger.info(f"DB records: {summary['orphaned_db_records']}")
        logger.info("=" * 80)

        return summary


async def run_cleanup_job():
    """
    Standalone function to run cleanup job

    This can be called from a cron job or background worker
    """
    from ...main import supabase
    from ...evolution_api import EvolutionAPIClient

    logger.info("Initializing cleanup job...")

    async with EvolutionAPIClient() as evolution_client:
        cleanup_service = OrphanedInstanceCleanup(supabase, evolution_client)
        return await cleanup_service.run_cleanup()


if __name__ == "__main__":
    # Can be run directly for testing
    asyncio.run(run_cleanup_job())
