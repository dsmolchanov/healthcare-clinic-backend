"""
Application startup and shutdown lifecycle management.

Handles:
- Service warmup on startup
- Worker initialization (calendar sync, outbox processor, etc.)
- HIPAA compliance system initialization
- Graceful shutdown of all services
"""
import os
import logging
import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.database import get_healthcare_client

logger = logging.getLogger(__name__)


async def warmup_services(client: httpx.AsyncClient):
    """Warm up external services on startup with timeouts to prevent blocking."""
    warmup_tasks = []

    # Note: OpenAI/Supabase/Pinecone warmup disabled - using lazy initialization
    if warmup_tasks:
        try:
            await asyncio.wait_for(
                asyncio.gather(*warmup_tasks, return_exceptions=True),
                timeout=5.0
            )
        except asyncio.TimeoutError:
            logger.warning("Warmup timed out after 5s, continuing startup")


async def init_hipaa_systems(app: FastAPI, supabase):
    """Initialize HIPAA compliance systems."""
    try:
        from app.security.hipaa_audit_system import init_audit_system
        from app.security.phi_encryption import init_encryption_system
        from app.security.data_retention import init_retention_manager

        encryption_system = init_encryption_system()
        logger.info("✅ PHI encryption system initialized")

        audit_system = init_audit_system(supabase)
        logger.info("✅ HIPAA audit system initialized")

        retention_manager = init_retention_manager(supabase, audit_system, encryption_system)
        logger.info("✅ Data retention manager initialized")

        app.state.audit_system = audit_system
        app.state.encryption_system = encryption_system
        app.state.retention_manager = retention_manager

        logger.info("🛡️ HIPAA compliance systems ready")
    except Exception as e:
        logger.error(f"Failed to initialize HIPAA compliance systems: {str(e)}")


async def init_workers(app: FastAPI):
    """Initialize background workers."""
    # Calendar sync worker
    try:
        from app.workers.calendar_sync_worker import start_worker
        await start_worker()
        logger.info("✅ Calendar sync worker started")
    except Exception as e:
        logger.error(f"Failed to start calendar sync worker: {str(e)}")

    # Outbox processor worker
    try:
        from app.workers.outbox_processor import OutboxProcessor
        outbox_processor = OutboxProcessor()
        asyncio.create_task(outbox_processor.start())
        app.state.outbox_processor = outbox_processor
        logger.info("✅ Outbox processor worker started")
    except Exception as e:
        logger.error(f"Failed to start outbox processor: {str(e)}")

    # Failed confirmation escalation worker
    try:
        from app.workers.failed_confirmation_escalation import FailedConfirmationEscalation
        escalation_worker = FailedConfirmationEscalation()
        asyncio.create_task(escalation_worker.start())
        app.state.escalation_worker = escalation_worker
        logger.info("✅ Failed confirmation escalation worker started")
    except Exception as e:
        logger.error(f"Failed to start escalation worker: {str(e)}")

    # Message plan worker (SOTA reminders)
    try:
        from app.workers.message_plan_worker import MessagePlanWorker
        message_plan_worker = MessagePlanWorker()
        asyncio.create_task(message_plan_worker.start())
        app.state.message_plan_worker = message_plan_worker
        logger.info("✅ Message plan worker started")
    except Exception as e:
        logger.error(f"Failed to start message plan worker: {str(e)}")


async def init_billing_services():
    """Initialize billing listener and reconciliation worker."""
    # Billing listener
    try:
        from app.services.billing_listener import start_billing_listener
        if await start_billing_listener():
            logger.info("✅ Billing listener started (per-doctor sync)")
        else:
            logger.warning("⚠️ Billing listener not started (DB URL not configured)")
    except Exception as e:
        logger.error(f"Failed to start billing listener: {str(e)}")

    # Billing reconciliation worker
    try:
        from app.workers.billing_reconciliation import start_reconciliation_worker
        await start_reconciliation_worker()
        logger.info("✅ Billing reconciliation worker started (runs nightly)")
    except Exception as e:
        logger.error(f"Failed to start billing reconciliation worker: {str(e)}")


async def init_http_client(app: FastAPI):
    """Initialize shared HTTP client."""
    try:
        app.state.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=2.0, read=10.0, write=10.0, pool=10.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            http2=False  # Disable HTTP/2 to prevent SSL handshake issues
        )
        logger.info("✅ Shared HTTP client initialized (HTTP/1.1)")
        await warmup_services(app.state.http_client)
    except Exception as e:
        logger.warning(f"Failed to initialize HTTP client: {e}")
        app.state.http_client = None


async def warmup_caches():
    """Warm up Redis and other caches."""
    # Redis cache with clinic data
    try:
        from app.startup_warmup import warmup_clinic_data
        await warmup_clinic_data()
        logger.info("✅ Redis cache warmed up with clinic data")
    except Exception as e:
        logger.warning(f"Failed to warm up Redis cache: {e}")

    # TierRegistry cache
    try:
        from app.services.llm.tier_registry import warmup_tier_registry
        await warmup_tier_registry()
        logger.info("✅ TierRegistry warmed up with model mappings")
    except Exception as e:
        logger.warning(f"Failed to warm up TierRegistry: {e}")

    # WhatsApp→Clinic mapping cache
    try:
        from app.startup_warmup import warmup_whatsapp_instance_cache
        whatsapp_stats = await warmup_whatsapp_instance_cache()
        logger.info(
            "✅ WhatsApp cache warmed: %s/%s instances cached",
            whatsapp_stats.get("cached", 0),
            whatsapp_stats.get("total", 0)
        )
    except Exception as e:
        logger.warning(f"Failed to warm up WhatsApp cache: {e}")


async def stop_workers(app: FastAPI):
    """Stop all background workers gracefully."""
    # Calendar sync worker
    try:
        from app.workers.calendar_sync_worker import stop_worker
        await stop_worker()
        logger.info("✅ Calendar sync worker stopped")
    except Exception as e:
        logger.error(f"Error stopping calendar sync worker: {str(e)}")

    # Outbox processor
    try:
        if hasattr(app.state, 'outbox_processor'):
            await app.state.outbox_processor.stop()
            logger.info("✅ Outbox processor worker stopped")
    except Exception as e:
        logger.error(f"Error stopping outbox processor: {str(e)}")

    # Escalation worker
    try:
        if hasattr(app.state, 'escalation_worker'):
            await app.state.escalation_worker.stop()
            logger.info("✅ Escalation worker stopped")
    except Exception as e:
        logger.error(f"Error stopping escalation worker: {str(e)}")

    # Message plan worker
    try:
        if hasattr(app.state, 'message_plan_worker'):
            await app.state.message_plan_worker.stop()
            logger.info("✅ Message plan worker stopped")
    except Exception as e:
        logger.error(f"Error stopping message plan worker: {str(e)}")

    # Billing listener
    try:
        from app.services.billing_listener import stop_billing_listener
        await stop_billing_listener()
        logger.info("✅ Billing listener stopped")
    except Exception as e:
        logger.error(f"Error stopping billing listener: {str(e)}")

    # Billing reconciliation worker
    try:
        from app.workers.billing_reconciliation import stop_reconciliation_worker
        await stop_reconciliation_worker()
        logger.info("✅ Billing reconciliation worker stopped")
    except Exception as e:
        logger.error(f"Error stopping billing reconciliation worker: {str(e)}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle manager."""
    # === STARTUP ===
    logger.info("Starting Healthcare Backend...")

    supabase = get_healthcare_client()

    # Initialize Arize Cloud observability
    try:
        from app.observability.arize_tracer import init_arize
        arize_provider = init_arize()
        if arize_provider:
            app.state.arize_tracer = arize_provider
    except Exception as e:
        logger.warning(f"Failed to initialize Arize observability: {e}")

    logger.info(f"Connected to Supabase: {os.getenv('SUPABASE_URL')}")

    # Initialize HIPAA systems
    await init_hipaa_systems(app, supabase)

    # Note: WhatsApp Queue Worker runs as separate process
    logger.info("📝 WhatsApp worker runs as separate process - not started here")

    # Initialize background workers
    await init_workers(app)

    # Initialize billing services
    await init_billing_services()

    # Initialize HTTP client
    await init_http_client(app)

    # Warm up caches
    await warmup_caches()

    yield

    # === SHUTDOWN ===
    logger.info("Shutting down services...")

    await stop_workers(app)

    # Close HTTP client
    if hasattr(app.state, 'http_client') and app.state.http_client:
        await app.state.http_client.aclose()
        logger.info("✅ HTTP client closed")

    # Flush Langfuse events
    try:
        from app.observability import flush_langfuse
        await flush_langfuse()
    except Exception as e:
        logger.warning(f"Error flushing Langfuse: {e}")

    logger.info("Shutting down HIPAA compliance systems...")
    logger.info("Healthcare Backend shutdown complete")
