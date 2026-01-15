"""
Billing Listener Service
Listens to PostgreSQL NOTIFY events for doctor changes and syncs to Stripe.
"""

import asyncio
import json
import logging
import os
from typing import Optional
from urllib.parse import urlparse

import asyncpg

from app.services.billing_sync_service import execute_doctor_sync

logger = logging.getLogger(__name__)


class BillingListener:
    """
    Listens to PostgreSQL NOTIFY events on 'doctor_changed' channel
    and triggers billing sync when relevant changes occur.
    """

    def __init__(self):
        self.conn: Optional[asyncpg.Connection] = None
        self._running = False
        self._debounce_tasks: dict = {}  # org_id -> task for debouncing

    def _get_database_url(self) -> Optional[str]:
        """Get database URL from environment."""
        db_url = os.getenv('SUPABASE_DB_URL')
        if db_url:
            return db_url

        supabase_url = os.getenv('SUPABASE_URL')
        if not supabase_url:
            return None

        parsed = urlparse(supabase_url)
        project_id = parsed.hostname.split('.')[0]

        db_password = os.getenv('SUPABASE_DB_PASSWORD')
        if db_password:
            return f"postgresql://postgres:{db_password}@db.{project_id}.supabase.co:5432/postgres"

        return None

    async def connect(self):
        """Connect to PostgreSQL and set up listener."""
        db_url = self._get_database_url()
        if not db_url:
            logger.warning("Database URL not configured, billing listener disabled")
            return False

        try:
            self.conn = await asyncpg.connect(db_url)

            # Add listener for doctor_changed notifications
            await self.conn.add_listener('doctor_changed', self._notification_handler)

            logger.info("Billing listener connected and listening for doctor_changed events")
            self._running = True
            return True

        except Exception as e:
            logger.error(f"Failed to connect billing listener: {e}")
            return False

    def _notification_handler(self, connection, pid, channel, payload):
        """Handle incoming PostgreSQL notifications."""
        try:
            data = json.loads(payload)
            organization_id = data.get('organization_id')

            if not organization_id:
                logger.warning(f"Received notification without organization_id: {payload}")
                return

            logger.info(f"Received doctor_changed: op={data.get('operation')}, org={organization_id}")

            # Debounce: cancel any pending sync for this org and schedule a new one
            # This handles rapid changes (e.g., bulk doctor import)
            if organization_id in self._debounce_tasks:
                self._debounce_tasks[organization_id].cancel()

            # Schedule debounced sync (500ms delay)
            self._debounce_tasks[organization_id] = asyncio.create_task(
                self._debounced_sync(organization_id)
            )

        except json.JSONDecodeError:
            logger.error(f"Invalid JSON in notification payload: {payload}")
        except Exception as e:
            logger.error(f"Error handling notification: {e}")

    async def _debounced_sync(self, organization_id: str):
        """Execute sync after a short debounce delay."""
        try:
            # Wait 500ms before syncing (handles rapid changes)
            await asyncio.sleep(0.5)

            # Execute the sync
            result = await execute_doctor_sync(organization_id)

            if result.get("status") == "success":
                logger.info(
                    f"Synced doctor count for org {organization_id}: "
                    f"{result.get('previous_quantity')} -> {result.get('billable_count')}"
                )
            elif result.get("status") == "skipped":
                logger.debug(f"Sync skipped for org {organization_id}: {result.get('reason')}")
            elif result.get("status") == "no_change":
                logger.debug(f"No change needed for org {organization_id}")
            else:
                logger.warning(f"Sync result for org {organization_id}: {result}")

        except asyncio.CancelledError:
            # Another change came in, this sync was cancelled
            pass
        except Exception as e:
            logger.error(f"Error syncing doctor count for org {organization_id}: {e}")
        finally:
            # Clean up the task reference
            self._debounce_tasks.pop(organization_id, None)

    async def disconnect(self):
        """Disconnect from PostgreSQL."""
        self._running = False

        # Cancel any pending debounce tasks
        for task in self._debounce_tasks.values():
            task.cancel()
        self._debounce_tasks.clear()

        if self.conn:
            await self.conn.remove_listener('doctor_changed', self._notification_handler)
            await self.conn.close()
            self.conn = None
            logger.info("Billing listener disconnected")

    async def keep_alive(self):
        """Keep the connection alive with periodic pings."""
        while self._running:
            try:
                await asyncio.sleep(60)  # Ping every 60 seconds
                if self.conn:
                    await self.conn.fetchval("SELECT 1")
            except Exception as e:
                logger.error(f"Billing listener keep-alive failed: {e}")
                # Try to reconnect
                await self.disconnect()
                await asyncio.sleep(5)
                await self.connect()


# Global instance
_billing_listener: Optional[BillingListener] = None


async def start_billing_listener():
    """Start the billing listener (call from app startup)."""
    global _billing_listener
    if _billing_listener is None:
        _billing_listener = BillingListener()
        if await _billing_listener.connect():
            # Start keep-alive task
            asyncio.create_task(_billing_listener.keep_alive())
            return True
    return False


async def stop_billing_listener():
    """Stop the billing listener (call from app shutdown)."""
    global _billing_listener
    if _billing_listener:
        await _billing_listener.disconnect()
        _billing_listener = None
