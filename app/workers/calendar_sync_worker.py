"""
Background worker for syncing unsynced appointments to external calendars

Optimized architecture (SOTA):
- Batch appointments by clinic
- Authenticate once per clinic (not per appointment)
- Process appointments in parallel within each clinic batch
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.services.external_calendar_service import ExternalCalendarService
from app.db.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

# Concurrency limits for parallel processing
MAX_CONCURRENT_APPOINTMENTS = 5  # Process up to 5 appointments in parallel per clinic


class CalendarSyncWorker:
    """
    Background worker to sync unsynced appointments to external calendars
    Runs periodically to catch any appointments that failed to sync
    """

    def __init__(self, interval_minutes: int = 60):
        """
        Initialize calendar sync worker

        Args:
            interval_minutes: How often to run sync (default 60 minutes)
                             Reduced from 15 since webhooks handle real-time sync
        """
        self.supabase = get_supabase_client()
        self.calendar_service = ExternalCalendarService()
        self.scheduler = AsyncIOScheduler()
        self.interval_minutes = interval_minutes
        self.is_running = False

        logger.info(f"Initialized CalendarSyncWorker with {interval_minutes} minute interval")

    def start(self):
        """Start the scheduled sync worker"""
        if not self.is_running:
            # Schedule periodic sync
            self.scheduler.add_job(
                self.sync_all_unsynced_appointments,
                trigger=IntervalTrigger(minutes=self.interval_minutes),
                id='calendar_sync_worker',
                name='Calendar Sync Worker',
                misfire_grace_time=120,  # Allow 2 minutes grace period
                coalesce=True,  # Combine missed runs
                max_instances=1  # Only one instance at a time
            )

            # Run once on startup (with 60 second delay to allow health checks to pass)
            self.scheduler.add_job(
                self.sync_all_unsynced_appointments,
                trigger='date',
                run_date=datetime.now() + timedelta(seconds=60),
                id='calendar_sync_startup',
                name='Calendar Sync Worker (Startup)'
            )

            self.scheduler.start()
            self.is_running = True
            logger.info(f"Calendar sync worker started (runs every {self.interval_minutes} minutes)")

    def stop(self):
        """Stop the scheduled sync worker"""
        if self.is_running:
            self.scheduler.shutdown()
            self.is_running = False
            logger.info("Calendar sync worker stopped")

    async def sync_all_unsynced_appointments(self) -> Dict[str, Any]:
        """
        Find and sync all unsynced appointments across all clinics

        Returns:
            Dictionary with sync statistics
        """
        try:
            logger.info("Starting calendar sync worker run")
            start_time = datetime.now()

            stats = {
                "total_appointments": 0,
                "synced_count": 0,
                "failed_count": 0,
                "skipped_count": 0,
                "clinics_processed": 0,
                "start_time": start_time.isoformat()
            }

            # Get all clinics with calendar integration enabled
            integrations = self.supabase.from_('calendar_integrations').select(
                'clinic_id, organization_id, provider'
            ).eq('sync_enabled', True).execute()

            if not integrations.data:
                logger.info("No clinics with calendar integration enabled")
                return stats

            # Process each clinic
            for integration in integrations.data:
                clinic_id = integration['clinic_id']

                try:
                    clinic_stats = await self._sync_clinic_appointments(
                        clinic_id,
                        integration['organization_id']
                    )

                    stats['total_appointments'] += clinic_stats['total']
                    stats['synced_count'] += clinic_stats['synced']
                    stats['failed_count'] += clinic_stats['failed']
                    stats['skipped_count'] += clinic_stats['skipped']
                    stats['clinics_processed'] += 1

                except Exception as e:
                    logger.error(f"Error syncing clinic {clinic_id}: {e}", exc_info=True)
                    stats['failed_count'] += 1

            duration = (datetime.now() - start_time).total_seconds()
            stats['duration_seconds'] = duration

            logger.info(
                f"Calendar sync worker completed: "
                f"{stats['synced_count']}/{stats['total_appointments']} synced, "
                f"{stats['failed_count']} failed, "
                f"{stats['clinics_processed']} clinics processed "
                f"in {duration:.2f}s"
            )

            return stats

        except Exception as e:
            logger.error(f"Error in calendar sync worker: {e}", exc_info=True)
            return {"error": str(e)}

    async def _sync_single_appointment(
        self,
        appointment: Dict[str, Any],
        semaphore: asyncio.Semaphore
    ) -> Dict[str, str]:
        """
        Sync a single appointment with semaphore-controlled concurrency.

        Args:
            appointment: Appointment data dict
            semaphore: Asyncio semaphore for concurrency control

        Returns:
            Dict with result status: {'status': 'synced'|'failed'|'skipped', 'id': appointment_id}
        """
        async with semaphore:
            appointment_id = appointment['id']
            try:
                # Check if appointment is in valid status
                if appointment['status'] in ['cancelled', 'no_show']:
                    return {'status': 'skipped', 'id': appointment_id}

                # Sync to calendar
                sync_result = await self.calendar_service.sync_appointment_to_calendar(
                    appointment_id
                )

                if sync_result.get('success'):
                    logger.debug(f"Synced appointment {appointment_id}")
                    return {'status': 'synced', 'id': appointment_id}
                else:
                    logger.warning(
                        f"Failed to sync appointment {appointment_id}: "
                        f"{sync_result.get('error')}"
                    )
                    return {'status': 'failed', 'id': appointment_id, 'error': sync_result.get('error')}

            except Exception as e:
                logger.error(f"Error syncing appointment {appointment_id}: {e}")
                return {'status': 'failed', 'id': appointment_id, 'error': str(e)}

    async def _sync_clinic_appointments(
        self,
        clinic_id: str,
        organization_id: str
    ) -> Dict[str, int]:
        """
        Sync unsynced appointments for a specific clinic.

        OPTIMIZED: Uses parallel processing with semaphore-controlled concurrency.
        The ExternalCalendarService caches credentials per-clinic, so we only
        authenticate once regardless of how many appointments we sync.

        Args:
            clinic_id: Clinic UUID
            organization_id: Organization UUID

        Returns:
            Dictionary with sync counts
        """
        stats = {"total": 0, "synced": 0, "failed": 0, "skipped": 0}

        # Get unsynced appointments using RPC
        result = self.supabase.rpc('get_unsynced_appointments', {
            'p_clinic_id': clinic_id
        }).execute()

        if not result.data:
            return stats

        appointments = result.data
        stats['total'] = len(appointments)
        logger.info(f"Found {stats['total']} unsynced appointments for clinic {clinic_id}")

        # Pre-warm credential cache for this clinic (single auth call)
        # The calendar service caches credentials, so subsequent calls will reuse them
        try:
            # Trigger credential fetch/refresh once before parallel processing
            await self.calendar_service._get_calendar_integration(clinic_id, 'google')
            logger.debug(f"Pre-warmed credential cache for clinic {clinic_id}")
        except Exception as e:
            logger.warning(f"Failed to pre-warm credentials for clinic {clinic_id}: {e}")
            # Continue anyway - individual syncs will handle credential errors

        # Process appointments in parallel with controlled concurrency
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_APPOINTMENTS)

        tasks = [
            self._sync_single_appointment(appointment, semaphore)
            for appointment in appointments
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Aggregate results
        for result in results:
            if isinstance(result, Exception):
                stats['failed'] += 1
                logger.error(f"Unexpected error during sync: {result}")
            elif isinstance(result, dict):
                status = result.get('status', 'failed')
                if status == 'synced':
                    stats['synced'] += 1
                elif status == 'skipped':
                    stats['skipped'] += 1
                else:
                    stats['failed'] += 1

        return stats


# Global worker instance
_worker_instance = None


def get_worker_instance() -> CalendarSyncWorker:
    """Get or create global worker instance"""
    global _worker_instance
    if _worker_instance is None:
        _worker_instance = CalendarSyncWorker()
    return _worker_instance


async def start_worker():
    """Start the calendar sync worker"""
    worker = get_worker_instance()
    worker.start()
    logger.info("Calendar sync worker started")


async def stop_worker():
    """Stop the calendar sync worker"""
    worker = get_worker_instance()
    worker.stop()
    logger.info("Calendar sync worker stopped")
