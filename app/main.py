"""
Healthcare Clinics Backend - Main Application
Handles WhatsApp webhooks and appointment scheduling
"""

import os
import logging
import asyncio
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from dotenv import load_dotenv
import hashlib
import hmac
from typing import Dict, Any
import httpx

# Apply runtime patches before additional imports that rely on OpenAI client setup.
import app.patches.openai_httpx_fix  # noqa: E402,F401

# Supabase client - use canonical database module
from app.database import get_healthcare_client
# from app.api import quick_onboarding_router  # Disabled - using RPC version instead
from app.api import quick_onboarding_rpc
from app.api import multimodal_upload
from app.api import services_upload
from app.middleware.rate_limiter import webhook_limiter

# Load environment variables FIRST before importing modules that need them
load_dotenv()

# Validate environment configuration (Phase 1 security - fail fast on bad config)
# Note: Currently logs warning but doesn't exit to avoid breaking existing deployments
# TODO: Enable validate_or_exit() after all secrets are properly configured in Fly.io
from app.startup_validation import validate_environment
if not validate_environment():
    import logging as _log
    _log.getLogger(__name__).warning(
        "Environment validation failed - some features may not work. "
        "See logs above for details."
    )

# Import message processor at module level AFTER dotenv load
# NOTE: Using pipeline_message_processor (multilingual_message_processor is deprecated and removed)
from app.api.pipeline_message_processor import handle_process_message
from app.schemas.messages import MessageRequest

# Configure centralized logging (container-aware: no timestamps in Docker/Fly.io)
from app.utils.logging_config import configure_logging
configure_logging()
logger = logging.getLogger(__name__)

# Log module-level initialization
logger.info("🚀 MODULE LEVEL - app/main.py loading")


async def warmup_services(client: httpx.AsyncClient):
    """Warm up external services on startup with timeouts to prevent blocking"""
    warmup_tasks = []

    # OpenAI warmup removed - llm_factory handles lazy initialization with adapter caching
    # Supabase warmup skipped - it's causing SSL timeouts, will be lazily initialized
    # Pinecone removed - no longer needed (Phase 3)

    # Run all warmups concurrently with 5s total timeout
    if warmup_tasks:
        try:
            await asyncio.wait_for(
                asyncio.gather(*warmup_tasks, return_exceptions=True),
                timeout=5.0
            )
        except asyncio.TimeoutError:
            logger.warning("Warmup timed out after 5s, continuing startup")


# Initialize Supabase client using canonical database module
# Credentials are validated by startup_validation.py
supabase = get_healthcare_client()
logger.info("Connected to Supabase (using healthcare schema via database.py)")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    # Startup
    logger.info("Starting Healthcare Backend...")

    # Initialize Arize Cloud observability (Gemini + LangGraph tracing)
    # Uses multi-exporter mode to work alongside Langfuse without conflicts
    try:
        from app.observability.arize_tracer import init_arize
        arize_provider = init_arize()
        if arize_provider:
            app.state.arize_tracer = arize_provider
    except Exception as e:
        logger.warning(f"Failed to initialize Arize observability: {e}")
    logger.info(f"Connected to Supabase: {os.getenv('SUPABASE_URL')}")

    # Initialize HIPAA compliance systems
    try:
        from app.security.hipaa_audit_system import init_audit_system
        from app.security.phi_encryption import init_encryption_system
        from app.security.data_retention import init_retention_manager

        # Initialize encryption system
        encryption_system = init_encryption_system()
        logger.info("✅ PHI encryption system initialized")

        # Initialize audit system
        audit_system = init_audit_system(supabase)
        logger.info("✅ HIPAA audit system initialized")

        # Initialize retention manager
        retention_manager = init_retention_manager(supabase, audit_system, encryption_system)
        logger.info("✅ Data retention manager initialized")

        # Store systems in app state for access by endpoints
        app.state.audit_system = audit_system
        app.state.encryption_system = encryption_system
        app.state.retention_manager = retention_manager

        logger.info("🛡️ HIPAA compliance systems ready")

    except Exception as e:
        logger.error(f"Failed to initialize HIPAA compliance systems: {str(e)}")
        # Don't fail startup, but log the issue

    # Note: WhatsApp Queue Worker now runs as separate process (run_worker.py)
    # See fly.toml [processes] section for configuration
    logger.info("📝 WhatsApp worker runs as separate process - not started here")

    # Start calendar sync worker
    try:
        from app.workers.calendar_sync_worker import start_worker
        await start_worker()
        logger.info("✅ Calendar sync worker started")
    except Exception as e:
        logger.error(f"Failed to start calendar sync worker: {str(e)}")
        # Don't fail startup, but log the issue

    # Start outbox processor worker for async message delivery
    try:
        from app.workers.outbox_processor import OutboxProcessor
        outbox_processor = OutboxProcessor()
        asyncio.create_task(outbox_processor.start())
        app.state.outbox_processor = outbox_processor
        logger.info("✅ Outbox processor worker started")
    except Exception as e:
        logger.error(f"Failed to start outbox processor: {str(e)}")
        # Don't fail startup, but log the issue

    # Start failed confirmation escalation worker
    try:
        from app.workers.failed_confirmation_escalation import FailedConfirmationEscalation
        escalation_worker = FailedConfirmationEscalation()
        asyncio.create_task(escalation_worker.start())
        app.state.escalation_worker = escalation_worker
        logger.info("✅ Failed confirmation escalation worker started")
    except Exception as e:
        logger.error(f"Failed to start escalation worker: {str(e)}")
        # Don't fail startup, but log the issue

    # FSM system removed in Phase 1.3 cleanup - all message processing
    # now goes through PipelineMessageProcessor -> LangGraph orchestrator

    # Initialize shared HTTP client without HTTP/2 (causes SSL issues)
    try:
        app.state.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=2.0, read=10.0, write=10.0, pool=10.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            http2=False  # Disable HTTP/2 to prevent SSL handshake issues
        )
        logger.info("✅ Shared HTTP client initialized (HTTP/1.1)")

        # Warm up external services
        await warmup_services(app.state.http_client)
    except Exception as e:
        logger.warning(f"Failed to initialize HTTP client: {e}")
        app.state.http_client = None

    # Warm up Redis cache with clinic data
    try:
        from app.startup_warmup import warmup_clinic_data
        await warmup_clinic_data()
        logger.info("✅ Redis cache warmed up with clinic data")
    except Exception as e:
        logger.warning(f"Failed to warm up Redis cache: {e}")
        # Don't fail startup, caching will happen on first request

    # Warm up TierRegistry cache (Phase 2 - model tier abstraction)
    try:
        from app.services.llm.tier_registry import warmup_tier_registry
        await warmup_tier_registry()
        logger.info("✅ TierRegistry warmed up with model mappings")
    except Exception as e:
        logger.warning(f"Failed to warm up TierRegistry: {e}")
        # Don't fail startup, will use code defaults on first request

    # OPTIMIZATION: Warm up WhatsApp→Clinic mapping cache (eliminates DB queries on hot path)
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
        # Don't fail startup, will do DB lookups on first request

    yield

    # Shutdown
    logger.info("Shutting down services...")

    # Stop calendar sync worker
    try:
        from app.workers.calendar_sync_worker import stop_worker
        await stop_worker()
        logger.info("✅ Calendar sync worker stopped")
    except Exception as e:
        logger.error(f"Error stopping calendar sync worker: {str(e)}")

    # Stop outbox processor worker
    try:
        if hasattr(app.state, 'outbox_processor'):
            await app.state.outbox_processor.stop()
            logger.info("✅ Outbox processor worker stopped")
    except Exception as e:
        logger.error(f"Error stopping outbox processor: {str(e)}")

    # Stop escalation worker
    try:
        if hasattr(app.state, 'escalation_worker'):
            await app.state.escalation_worker.stop()
            logger.info("✅ Escalation worker stopped")
    except Exception as e:
        logger.error(f"Error stopping escalation worker: {str(e)}")

    if hasattr(app.state, 'http_client') and app.state.http_client:
        await app.state.http_client.aclose()
        logger.info("✅ HTTP client closed")

    # Flush Langfuse events to ensure they're sent before shutdown
    try:
        from app.observability import flush_langfuse
        await flush_langfuse()
    except Exception as e:
        logger.warning(f"Error flushing Langfuse: {e}")

    logger.info("Shutting down HIPAA compliance systems...")
    logger.info("Healthcare Backend shutdown complete")

# Create FastAPI app with enhanced OpenAPI configuration (Phase 5)
app = FastAPI(
    title="Healthcare Clinics Backend",
    description="""
Voice agent platform for healthcare clinics.

## Features
- WhatsApp appointment booking
- Multi-channel message processing
- Calendar integration
- HIPAA-compliant audit logging

## Authentication
Protected endpoints require Bearer token authentication.
Use the Authorize button above to set your JWT token.
""",
    version="1.0.0",
    lifespan=lifespan,
    redirect_slashes=False,  # Prevent HTTP redirects from HTTPS requests
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


# Custom OpenAPI schema with security definitions (Phase 5)
def custom_openapi():
    """Generate custom OpenAPI schema with security schemes."""
    if app.openapi_schema:
        return app.openapi_schema

    from fastapi.openapi.utils import get_openapi

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    # Add security schemes
    openapi_schema["components"]["securitySchemes"] = {
        "bearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Enter your JWT token"
        }
    }

    # Add tags for better organization
    openapi_schema["tags"] = [
        {"name": "health", "description": "Health check endpoints"},
        {"name": "messages", "description": "Message processing endpoints"},
        {"name": "webhooks", "description": "Webhook handlers for external services"},
        {"name": "appointments", "description": "Appointment management"},
        {"name": "integrations", "description": "External service integrations"},
    ]

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi

# Configure CORS - explicitly allow production frontend
origins = [
    "https://plaintalk.io",
    "https://www.plaintalk.io",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:5174",
    "*"  # Allow all origins as fallback
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Add rate limiting middleware for webhook endpoints
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Apply rate limiting to webhook endpoints."""
    # Only apply to webhook endpoints
    if request.url.path.startswith("/webhooks/"):
        return await webhook_limiter(request, call_next)
    return await call_next(request)

# Add HIPAA audit middleware for automatic PHI access logging
from app.security.audit_middleware import HIPAAAuditMiddleware
app.add_middleware(HIPAAAuditMiddleware)

# Include routers
# app.include_router(quick_onboarding_router)  # Disabled - using RPC version instead
app.include_router(quick_onboarding_rpc.router)

# Include webhooks router for real-time updates
from app.api import webhooks
app.include_router(webhooks.router)

# Include demo router for testing
from app.api import calendar_demo
app.include_router(calendar_demo.router)

# Knowledge routes removed - Pinecone RAG deprecated, using tools-based knowledge only

# Include integrations routes for Evolution API and other integrations
from app.api import integrations_routes
app.include_router(integrations_routes.router)

# Include maintenance routes for system cleanup and health checks
from app.api import maintenance_routes
app.include_router(maintenance_routes.router)

# Include Evolution mock for testing (temporary until Evolution API is deployed)
from app.api import evolution_mock

# Include price list parser API for medical services
from app.api import price_list_api
app.include_router(price_list_api.router)
app.include_router(evolution_mock.router)

# Include WhatsApp webhook handler
from app.api import whatsapp_webhook
app.include_router(whatsapp_webhook.router)

# Simple WhatsApp webhook removed (Phase 3 - LLM Factory Unification)

# Include Evolution webhook with proper URL pattern
from app.api import evolution_webhook
app.include_router(evolution_webhook.router)

# Include Rule Engine routes
from app.api import rule_authoring_api
app.include_router(rule_authoring_api.router)

# Include chat-based scheduling rule creation API
from app.api import scheduling_rule_chat_api
app.include_router(scheduling_rule_chat_api.router)

# Include Scheduling API for intelligent scheduling system
from app.api import scheduling_routes
app.include_router(scheduling_routes.router)

# Include Resources API for dashboard data
from app.api import resources_api
app.include_router(resources_api.router)

# Include Multimodal Bulk Upload API
app.include_router(multimodal_upload.router)

# Include Services Upload API
app.include_router(services_upload.router)

# Include Medical Director API for enhanced rule engine specialty assignment
from app.api import medical_director
app.include_router(medical_director.router)

# Include Calendar Webhooks for external calendar sync
from app.webhooks import calendar_webhooks
app.include_router(calendar_webhooks.router)

# Include Appointment Sync Webhook for automatic calendar sync
from app.webhooks import appointment_sync_webhook
app.include_router(appointment_sync_webhook.router)

# Include Unified Appointments API (Phase 2: Direct Replacement)
from app.api import appointments_api
app.include_router(appointments_api.router)

# Include WebSocket API for Real-Time Updates (Phase 3)
from app.api import websocket_api
app.include_router(websocket_api.router)

# Include Smart Scheduling API for AI-Powered Optimization (Phase 4)
from app.api import smart_scheduling_api
app.include_router(smart_scheduling_api.router)

# Include HIPAA Compliance API for Security and Audit Management (Phase 5)
from app.api import hipaa_compliance_api
app.include_router(hipaa_compliance_api.router)

# Include Metrics Endpoint for Prometheus scraping
from app.api import metrics_endpoint
app.include_router(metrics_endpoint.router)

# Include Healthcare API for direct doctor specialties endpoints
from app.api import healthcare_api
app.include_router(healthcare_api.router)

# Include LangGraph service for dual-lane routing
try:
    from app.api import langgraph_service
    app.include_router(langgraph_service.router)
    print("✅ LangGraph service routes loaded for dual-lane routing")
except ImportError as e:
    print(f"❌ LangGraph service module not available: {e}")

# Include Admin Streams API for queue management
from app.api import admin_streams
from app.api import agents_api
from app.api import calendar_sync
from app.api import memory_health

app.include_router(admin_streams.router)
app.include_router(agents_api.router)
app.include_router(calendar_sync.router)

# Calendar Management (Multi-Doctor)
from app.api import calendar_management
app.include_router(calendar_management.router)
app.include_router(memory_health.router)

# Billing & Subscription Management (Stripe Integration)
from app.api import billing_routes
app.include_router(billing_routes.router)
app.include_router(billing_routes.webhooks_router)

# Prompt Template Management (Phase 2B-2)
from app.api import prompt_routes
app.include_router(prompt_routes.router)

# Model Tier Mappings API
from app.api import tier_mappings_api
app.include_router(tier_mappings_api.router)

# ============================================================================
# Health Check
# ============================================================================

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "status": "healthy",
        "service": "Healthcare Clinics Backend",
        "version": "1.0.0"
    }


@app.api_route("/apps/voice-api/{deprecated_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"], include_in_schema=False)
async def deprecated_voice_api_routes(deprecated_path: str):
    """Return a clear error for legacy /apps/voice-api/* routes."""
    logger.warning(
        "Legacy /apps/voice-api/%s route called. Inform the client to migrate to /api/*.",
        deprecated_path,
    )
    raise HTTPException(
        status_code=410,
        detail="The /apps/voice-api/* endpoints have been retired. Please use the /api/* routes instead.",
    )

@app.get("/health")
async def health_check():
    """Instant health check endpoint with memory monitoring"""
    from datetime import datetime
    import psutil
    import os

    # Get memory usage
    process = psutil.Process(os.getpid())
    memory_info = process.memory_info()
    memory_mb = memory_info.rss / (1024 * 1024)  # Convert to MB
    memory_percent = process.memory_percent()

    # Determine status based on memory usage
    # Warning at 70%, degraded at 85%
    if memory_percent > 85:
        status = "degraded"
    elif memory_percent > 70:
        status = "warning"
    else:
        status = "healthy"

    return {
        "status": status,
        "service": "Healthcare Clinics Backend",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat(),
        "memory": {
            "used_mb": round(memory_mb, 1),
            "percent": round(memory_percent, 1),
        }
    }

@app.post("/admin/clear-cache/doctors/{clinic_id}")
async def clear_doctor_cache(clinic_id: str):
    """Clear Redis cache for doctor info (temporary fix for bad cached data)"""
    from app.config import get_redis_client
    redis_client = get_redis_client()
    if redis_client:
        key = f"clinic_doctors:{clinic_id}"
        result = redis_client.delete(key)
        return {"success": True, "deleted": result, "key": key}
    return {"success": False, "error": "No Redis client"}

@app.get("/debug/memory")
async def memory_stats():
    """
    Return current memory usage stats for LangGraph optimization monitoring.
    Restricted to staging/dev environments for security.
    """
    import psutil
    import os

    # Security: Don't expose memory internals in production healthcare API
    fly_app_name = os.environ.get('FLY_APP_NAME', 'local')
    if fly_app_name == 'healthcare-clinic-backend':
        raise HTTPException(status_code=404, detail="Not found")

    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()
    return {
        "rss_mb": mem_info.rss / 1024 / 1024,
        "vms_mb": mem_info.vms / 1024 / 1024,
        "percent": process.memory_percent(),
        "rss_vs_vms_delta_mb": (mem_info.vms - mem_info.rss) / 1024 / 1024,
        "environment": fly_app_name,
    }

@app.get("/test/queue")
async def test_queue():
    """Test endpoint to verify queue functionality"""
    import sys
    sys.path.insert(0, '/app')

    from app.services.whatsapp_queue import enqueue_message, get_queue_depth
    from app.config import get_redis_client

    results = {
        "test_suite": "WhatsApp Queue Phase 1-2",
        "tests": []
    }

    instance = "test-instance-remote"

    # Test 1: Redis Connection
    try:
        redis_client = get_redis_client()
        redis_client.ping()
        results["tests"].append({
            "name": "Redis Connection",
            "status": "passed",
            "message": "Redis connection successful"
        })
    except Exception as e:
        results["tests"].append({
            "name": "Redis Connection",
            "status": "failed",
            "error": str(e)
        })
        return results

    # Test 2: Enqueue Message
    try:
        success = await enqueue_message(
            instance=instance,
            to_number="+15555551234",
            text="API test message from Fly.io",
            message_id="api-test-001"
        )
        results["tests"].append({
            "name": "Enqueue Message",
            "status": "passed" if success else "failed",
            "message": f"Message enqueued: {success}"
        })
    except Exception as e:
        results["tests"].append({
            "name": "Enqueue Message",
            "status": "failed",
            "error": str(e)
        })

    # Test 3: Queue Depth
    try:
        depth = await get_queue_depth(instance)
        results["tests"].append({
            "name": "Queue Depth Check",
            "status": "passed",
            "message": f"Queue depth: {depth}"
        })
    except Exception as e:
        results["tests"].append({
            "name": "Queue Depth Check",
            "status": "failed",
            "error": str(e)
        })

    # Test 4: Idempotency
    try:
        depth_before = await get_queue_depth(instance)
        success2 = await enqueue_message(
            instance=instance,
            to_number="+15555551234",
            text="API test message from Fly.io",
            message_id="api-test-001"  # Same ID
        )
        depth_after = await get_queue_depth(instance)

        idempotent = (depth_before == depth_after)
        results["tests"].append({
            "name": "Idempotency Test",
            "status": "passed" if idempotent else "warning",
            "message": f"Depth unchanged: {depth_before} → {depth_after}",
            "idempotent": idempotent
        })
    except Exception as e:
        results["tests"].append({
            "name": "Idempotency Test",
            "status": "failed",
            "error": str(e)
        })

    # Test 5: Configuration
    try:
        from app.services.whatsapp_queue.config import (
            EVOLUTION_API_URL,
            CONSUMER_GROUP,
            MAX_DELIVERIES,
            TOKENS_PER_SECOND
        )
        results["tests"].append({
            "name": "Configuration Check",
            "status": "passed",
            "config": {
                "evolution_api": EVOLUTION_API_URL,
                "consumer_group": CONSUMER_GROUP,
                "max_deliveries": MAX_DELIVERIES,
                "rate_limit": TOKENS_PER_SECOND
            }
        })
    except Exception as e:
        results["tests"].append({
            "name": "Configuration Check",
            "status": "failed",
            "error": str(e)
        })

    # Summary
    passed = sum(1 for t in results["tests"] if t["status"] == "passed")
    failed = sum(1 for t in results["tests"] if t["status"] == "failed")

    results["summary"] = {
        "total": len(results["tests"]),
        "passed": passed,
        "failed": failed,
        "success_rate": f"{(passed/len(results['tests'])*100):.1f}%",
        "overall_status": "✅ PASSED" if failed == 0 else "❌ FAILED"
    }

    return results

@app.get("/worker/debug-raw-read")
async def debug_raw_read():
    """Debug: Read messages directly without consumer group"""
    from app.services.whatsapp_queue.queue import stream_key
    from app.services.whatsapp_queue.config import CONSUMER_GROUP
    from app.config import get_redis_client

    instance_name = os.getenv("INSTANCE_NAME", "clinic-3e411ecb-3411-4add-91e2-8fa897310cb0-1757905315621")

    try:
        redis_client = get_redis_client()
        stream = stream_key(instance_name)

        # Try XREAD (no consumer group)
        try:
            xread_result = redis_client.xread({stream: '0-0'}, count=10)
            xread_messages = []
            for stream_name, entries in xread_result:
                for msg_id, fields in entries:
                    xread_messages.append({"id": msg_id, "fields": fields})
        except Exception as e:
            xread_messages = {"error": str(e)}

        # Try XREADGROUP with different parameters
        try:
            # First, check if group exists
            groups = redis_client.xinfo_groups(stream)
            group_exists = any(g.get("name") == CONSUMER_GROUP for g in groups)

            if not group_exists:
                redis_client.xgroup_create(stream, CONSUMER_GROUP, id='0', mkstream=True)

            # Try reading with "0" (pending)
            xreadgroup_0 = redis_client.xreadgroup(
                groupname=CONSUMER_GROUP,
                consumername="debug-consumer",
                streams={stream: "0"},
                count=10
            )

            # Try reading with ">" (new messages)
            xreadgroup_new = redis_client.xreadgroup(
                groupname=CONSUMER_GROUP,
                consumername="debug-consumer-2",
                streams={stream: ">"},
                count=10
            )

            # Try reading from beginning explicitly
            xreadgroup_from_start = redis_client.xreadgroup(
                groupname=CONSUMER_GROUP,
                consumername="debug-consumer-3",
                streams={stream: "0-0"},
                count=10
            )

            debug_results = {
                "xreadgroup_pending_0": [
                    {"stream": s, "count": len(e)}
                    for s, e in xreadgroup_0
                ],
                "xreadgroup_new": [
                    {"stream": s, "count": len(e)}
                    for s, e in xreadgroup_new
                ],
                "xreadgroup_from_0-0": [
                    {"stream": s, "count": len(e)}
                    for s, e in xreadgroup_from_start
                ]
            }

        except Exception as e:
            debug_results = {"error": str(e)}

        return {
            "instance": instance_name,
            "stream_key": stream,
            "xread_messages": xread_messages,
            "xreadgroup_debug": debug_results
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }

@app.post("/worker/claim-pending")
async def claim_pending_messages():
    """
    Claim all pending messages from idle consumers to the active worker.
    This fixes the issue when messages are stuck with dead/debug consumers.
    """
    from app.services.whatsapp_queue.queue import stream_key
    from app.services.whatsapp_queue.config import CONSUMER_GROUP
    from app.config import get_redis_client

    instance_name = os.getenv("INSTANCE_NAME", "clinic-3e411ecb-3411-4add-91e2-8fa897310cb0-1757905315621")

    try:
        redis_client = get_redis_client()
        stream = stream_key(instance_name)

        # Get the actual worker consumer name
        if not hasattr(app.state, 'whatsapp_worker'):
            return {
                "status": "error",
                "error": "Worker not running on this machine"
            }

        worker_consumer = app.state.whatsapp_worker.consumer_name

        # Get pending messages summary
        pending_summary = redis_client.xpending(stream, CONSUMER_GROUP)
        if pending_summary.get("pending", 0) == 0:
            return {
                "status": "success",
                "message": "No pending messages to claim",
                "worker_consumer": worker_consumer
            }

        # Get detailed pending messages
        pending_details = redis_client.xpending_range(
            stream, CONSUMER_GROUP,
            '-', '+',  # Get all pending
            count=100
        )

        claimed_messages = []
        for msg_info in pending_details:
            msg_id = msg_info['message_id']
            old_consumer = msg_info['consumer']

            # Claim this message for our worker
            # XCLAIM will transfer ownership
            try:
                result = redis_client.xclaim(
                    stream, CONSUMER_GROUP, worker_consumer,
                    min_idle_time=0,  # Claim regardless of idle time
                    message_ids=[msg_id]
                )
                claimed_messages.append({
                    "message_id": msg_id,
                    "from_consumer": old_consumer,
                    "to_consumer": worker_consumer,
                    "claimed": len(result) > 0
                })
            except Exception as e:
                claimed_messages.append({
                    "message_id": msg_id,
                    "error": str(e)
                })

        return {
            "status": "success",
            "message": f"Claimed {len(claimed_messages)} messages for worker",
            "worker_consumer": worker_consumer,
            "claimed_messages": claimed_messages
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "instance": instance_name
        }

@app.post("/worker/fix-consumer-group")
async def fix_consumer_group():
    """
    Fix consumer group to read all messages from beginning.
    This resets the last-delivered-id so the worker can consume existing messages.
    """
    from app.services.whatsapp_queue.queue import stream_key
    from app.services.whatsapp_queue.config import CONSUMER_GROUP
    from app.config import get_redis_client

    instance_name = os.getenv("INSTANCE_NAME", "clinic-3e411ecb-3411-4add-91e2-8fa897310cb0-1757905315621")

    try:
        redis_client = get_redis_client()
        stream = stream_key(instance_name)

        # Get current state
        try:
            groups_before = redis_client.xinfo_groups(stream)
            current_group = next((g for g in groups_before if g.get("name") == CONSUMER_GROUP), None)
            before_state = {
                "consumers": current_group.get("consumers", 0) if current_group else 0,
                "pending": current_group.get("pending", 0) if current_group else 0,
                "last_delivered": current_group.get("last-delivered-id", "none") if current_group else "none"
            }
        except Exception:
            before_state = {"error": "Could not read current state"}

        # Reset last-delivered-id to '0' (read all messages from beginning)
        try:
            redis_client.xgroup_setid(stream, CONSUMER_GROUP, id='0')
        except Exception as e:
            return {
                "status": "error",
                "error": f"Failed to reset group: {str(e)}",
                "before": before_state
            }

        # Get new state
        try:
            groups_after = redis_client.xinfo_groups(stream)
            current_group = next((g for g in groups_after if g.get("name") == CONSUMER_GROUP), None)
            after_state = {
                "consumers": current_group.get("consumers", 0) if current_group else 0,
                "pending": current_group.get("pending", 0) if current_group else 0,
                "last_delivered": current_group.get("last-delivered-id", "none") if current_group else "none"
            }
        except Exception:
            after_state = {"error": "Could not read new state"}

        return {
            "status": "success",
            "message": "Consumer group reset to read from beginning",
            "instance": instance_name,
            "consumer_group": CONSUMER_GROUP,
            "before": before_state,
            "after": after_state
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "instance": instance_name
        }

@app.get("/worker/status")
async def worker_status():
    """Get WhatsApp queue worker status and queue metrics"""
    from app.services.whatsapp_queue.queue import stream_key, dlq_key
    from app.services.whatsapp_queue.config import CONSUMER_GROUP
    from app.config import get_redis_client

    # Get queue metrics (works regardless of worker state)
    instance_name = os.getenv("INSTANCE_NAME", "clinic-3e411ecb-3411-4add-91e2-8fa897310cb0-1757905315621")

    try:
        redis_client = get_redis_client()
        stream = stream_key(instance_name)
        dlq = dlq_key(instance_name)

        queue_depth = redis_client.xlen(stream)
        dlq_depth = redis_client.xlen(dlq)

        result = {
            "instance": instance_name,
            "queue_depth": queue_depth,
            "dlq_depth": dlq_depth,
        }

        # Add detailed debug info
        try:
            # Check consumer group
            groups = redis_client.xinfo_groups(stream)
            result["consumer_groups"] = [
                {
                    "name": g.get("name"),
                    "consumers": g.get("consumers", 0),
                    "pending": g.get("pending", 0),
                    "last_delivered": g.get("last-delivered-id", "none")
                }
                for g in groups
            ]

            # Check consumers
            try:
                consumers = redis_client.xinfo_consumers(stream, CONSUMER_GROUP)
                result["consumers"] = [
                    {
                        "name": c.get("name"),
                        "pending": c.get("pending", 0),
                        "idle_ms": c.get("idle", 0)
                    }
                    for c in consumers
                ]
            except Exception:
                result["consumers"] = []

            # Check pending messages
            try:
                pending_summary = redis_client.xpending(stream, CONSUMER_GROUP)
                result["pending_summary"] = {
                    "count": pending_summary.get("pending", 0),
                    "min_id": pending_summary.get("min"),
                    "max_id": pending_summary.get("max"),
                }
            except Exception as e:
                result["pending_summary"] = {"error": str(e)}

            # Get all messages in stream
            try:
                all_msgs = redis_client.xrange(stream, '-', '+', count=5)
                result["sample_messages"] = [
                    {"id": msg_id, "fields": fields}
                    for msg_id, fields in all_msgs
                ]
            except Exception:
                result["sample_messages"] = []

        except Exception as e:
            result["debug_error"] = str(e)

        # Add worker stats if available
        if hasattr(app.state, 'whatsapp_worker'):
            worker = app.state.whatsapp_worker
            worker_stats = worker.get_stats()
            result.update(worker_stats)
            result["status"] = "healthy" if queue_depth < 100 and worker.running else "degraded"
        else:
            result["status"] = "no_worker"
            result["message"] = "Worker not initialized on this machine"

        return result
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "instance": instance_name
        }

@app.get("/api/debug/env")
async def debug_env():
    """Debug endpoint to check environment variables (temporary)"""
    import os

    # Check for important API keys (only show first 10 chars for security)
    env_status = {}

    keys_to_check = [
        'OPENAI_API_KEY',
        'SUPABASE_URL',
        'SUPABASE_ANON_KEY',
        'SUPABASE_SERVICE_ROLE_KEY'
    ]

    for key in keys_to_check:
        value = os.environ.get(key)
        if value:
            # Only show first 10 characters for security
            env_status[key] = f"Set ({value[:10]}...)"
        else:
            env_status[key] = "Not set"

    return {
        "environment": os.environ.get('FLY_APP_NAME', 'local'),
        "env_vars": env_status,
        "total_env_vars": len(os.environ),
        "fly_machine_id": os.environ.get('FLY_MACHINE_ID', 'not_on_fly')
    }

@app.get("/health/detailed")
async def detailed_health_check():
    """Detailed health check with database connection test"""
    try:
        # Test database connection with a simple query
        result = supabase.table("clinics").select("id").limit(1).execute()

        return {
            "status": "healthy",
            "database": "connected",
            "clinics_accessible": True,
            "message": "Backend server is running and connected to database"
        }
    except Exception as e:
        # If we can't access the database, report partial health
        return {
            "status": "degraded",
            "database": "error",
            "message": "Server is running but database connection failed",
            "error": str(e)
        }

# ============================================================================
# WhatsApp Webhook Endpoints
# ============================================================================

@app.get("/webhooks/whatsapp")
async def verify_webhook(request: Request):
    """Verify WhatsApp webhook (Meta webhook verification)"""
    params = request.query_params

    # Meta sends these parameters for verification
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    logger.info(f"Webhook verification: mode={mode}, token={token}")

    if mode == "subscribe" and token == os.getenv("META_VERIFY_TOKEN"):
        logger.info("Webhook verified successfully")
        return Response(content=challenge, media_type="text/plain")
    else:
        logger.error("Webhook verification failed")
        raise HTTPException(status_code=403, detail="Verification failed")

@app.post("/webhooks/twilio/whatsapp")
async def whatsapp_webhook(request: Request):
    """Handle incoming WhatsApp messages via Twilio"""
    try:
        # Twilio sends form data, not JSON
        form_data = await request.form()
        body = dict(form_data)
        logger.info(f"Received Twilio WhatsApp webhook: {body}")

        # Extract message details
        if "entry" in body:
            for entry in body["entry"]:
                for change in entry.get("changes", []):
                    value = change.get("value", {})

                    # Handle incoming messages
                    if "messages" in value:
                        for message in value["messages"]:
                            await process_whatsapp_message(message, value.get("metadata", {}))

                    # Handle status updates
                    if "statuses" in value:
                        for status in value["statuses"]:
                            logger.info(f"Message status update: {status}")

        return {"status": "success"}

    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        return {"status": "error", "message": str(e)}

async def process_whatsapp_message(message: Dict[str, Any], metadata: Dict[str, Any]):
    """Process individual WhatsApp message"""
    try:
        from_number = message.get("from")
        message_type = message.get("type")
        message_id = message.get("id")

        logger.info(f"Processing message from {from_number}: type={message_type}")

        # Log access for audit
        audit_result = supabase.rpc("log_access", {
            "p_table_name": "whatsapp_messages",
            "p_record_id": None,
            "p_operation": "RECEIVE",
            "p_reason": f"WhatsApp message from {from_number}"
        }).execute()

        # Check if patient exists
        patient_result = supabase.table("patients").select("*").eq("phone", from_number).execute()

        if not patient_result.data:
            # New patient - create record
            logger.info(f"Creating new patient for {from_number}")
            patient = supabase.table("patients").insert({
                "clinic_id": "11111111-1111-1111-1111-111111111111",  # Default clinic
                "first_name": "New",
                "last_name": "Patient",
                "phone": from_number,
                "date_of_birth": "1990-01-01",  # Placeholder - should be collected later
                "email": f"whatsapp_{from_number.replace('+', '')}@placeholder.com"
            }).execute()
            patient_id = patient.data[0]["id"] if patient.data else None
        else:
            patient_id = patient_result.data[0]["id"]

        # Create or update WhatsApp session
        session_result = supabase.table("whatsapp_sessions").upsert({
            "clinic_id": "11111111-1111-1111-1111-111111111111",
            "patient_phone": from_number,
            "patient_id": patient_id,
            "status": "active",
            "last_message_at": "now()"
        }, on_conflict="patient_phone").execute()

        # Process based on message type
        response_text = ""

        if message_type == "text":
            text = message.get("text", {}).get("body", "")
            response_text = await process_text_message(text, patient_id)

        elif message_type == "audio":
            # Handle voice notes
            audio_id = message.get("audio", {}).get("id")
            response_text = "I received your voice message. Let me process that for you..."

        else:
            response_text = "I can help you with appointment booking and clinic information. How can I assist you today?"

        # Send response
        if response_text:
            await send_whatsapp_message(from_number, response_text)

    except Exception as e:
        logger.error(f"Error processing message: {e}")
        await send_whatsapp_message(
            from_number,
            "I apologize, but I'm having trouble processing your message. Please try again or call us directly."
        )

async def process_text_message(text: str, patient_id: str) -> str:
    """Process text message and generate response"""
    text_lower = text.lower()

    # Simple intent detection (replace with LLM in production)
    if any(word in text_lower for word in ["appointment", "book", "schedule", "availability"]):
        return await handle_appointment_request(text, patient_id)

    elif any(word in text_lower for word in ["cancel", "reschedule", "change"]):
        return await handle_appointment_change(text, patient_id)

    elif any(word in text_lower for word in ["hours", "open", "location", "address"]):
        return get_clinic_info()

    else:
        return """I can help you with:
📅 Booking appointments
🔄 Rescheduling or canceling
📍 Clinic information
💬 General questions

What would you like to do today?"""

async def handle_appointment_request(text: str, patient_id: str) -> str:
    """Handle appointment booking request"""
    # Get available slots (simplified)
    return """I can help you book an appointment!

Available times this week:
📅 Tomorrow (Thu) - 10:00 AM, 2:00 PM
📅 Friday - 9:00 AM, 11:00 AM, 3:00 PM

Please reply with your preferred date and time, or tell me what type of appointment you need."""

async def handle_appointment_change(text: str, patient_id: str) -> str:
    """Handle appointment changes"""
    # Check for existing appointments
    appointments = supabase.table("appointments")\
        .select("*")\
        .eq("patient_id", patient_id)\
        .eq("status", "scheduled")\
        .execute()

    if appointments.data:
        appt = appointments.data[0]
        return f"""I found your upcoming appointment on {appt['appointment_date']} at {appt['start_time']}.

Would you like to:
1️⃣ Cancel this appointment
2️⃣ Reschedule to a different time

Please reply with 1 or 2."""
    else:
        return "I don't see any upcoming appointments for you. Would you like to book a new appointment?"

def get_clinic_info() -> str:
    """Get clinic information"""
    return """📍 Bright Smile Dental Clinic
123 Main St, New York, NY 10001

📞 Phone: +1-212-555-0100

🕐 Hours:
Mon-Thu: 9:00 AM - 6:00 PM
Friday: 9:00 AM - 5:00 PM
Saturday: 10:00 AM - 2:00 PM
Sunday: Closed

🌐 Website: brightsmile.com"""

async def send_whatsapp_message(to_number: str, text: str):
    """Send WhatsApp message using Meta API"""
    import httpx

    url = f"https://graph.facebook.com/v17.0/{os.getenv('WHATSAPP_PHONE_ID')}/messages"
    headers = {
        "Authorization": f"Bearer {os.getenv('WHATSAPP_ACCESS_TOKEN')}",
        "Content-Type": "application/json"
    }

    data = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": text}
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=data, headers=headers)
            logger.info(f"WhatsApp message sent: {response.status_code}")
    except Exception as e:
        logger.error(f"Failed to send WhatsApp message: {e}")

# ============================================================================
# Legacy Appointment Management Endpoints - REPLACED by Unified API
# ============================================================================
# The following endpoints have been replaced by the unified appointments API
# at /api/appointments/* which includes calendar coordination
#
# Old endpoints:
# - GET /api/appointments/available -> GET /api/appointments/available (enhanced)
# - POST /api/appointments/book -> POST /api/appointments/book (with calendar sync)
#
# NEW unified endpoints provide:
# - Calendar coordination with ask-hold-reserve pattern
# - External calendar integration (Google, Outlook)
# - Comprehensive appointment operations (book, cancel, reschedule)
# - Real-time availability across multiple sources
# - HIPAA compliant audit logging
#
# Legacy endpoints commented out for direct replacement strategy

# ============================================================================
# Test Endpoints
# ============================================================================

@app.post("/webhooks/whatsapp/test")
async def test_whatsapp():
    """Test endpoint to simulate WhatsApp message"""
    test_message = {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": "+1234567890",
                        "type": "text",
                        "text": {"body": "I need to book an appointment"},
                        "id": "test_message_id"
                    }],
                    "metadata": {"phone_number_id": os.getenv("WHATSAPP_PHONE_ID")}
                }
            }]
        }]
    }

    # Create a mock request with the test message
    class MockRequest:
        async def json(self):
            return test_message

    mock_request = MockRequest()
    return await whatsapp_webhook(mock_request)

# ============================================================================
# Simple Widget Endpoint (Fast Response for Testing)
# ============================================================================

@app.get("/api/widget-chat")
async def widget_chat_get(body: str = "", session_id: str = "", clinic_id: str = ""):
    """GET endpoint for widget chat with LangGraph AI - accepts message as query param"""
    logger.info(f"📱 Widget message received: {body}")

    if not body:
        return {
            "message": "Please send a message!",
            "status": "success",
            "backend": "healthcare-clinic-backend.fly.dev"
        }

    if not clinic_id:
        raise HTTPException(
            status_code=400,
            detail="clinic_id required - please complete organization setup"
        )

    # Use session ID or generate one
    if not session_id:
        import time
        session_id = f"widget_{int(time.time())}"

    try:
        # Import LangGraph service
        from app.api.langgraph_service import process_message, MessageRequest

        # Get clinic information from database using provided clinic_id
        clinic_name = "Unknown Clinic"

        # Get clinic details from Supabase
        try:
            clinic_info = supabase.table("clinics").select("*").eq("id", clinic_id).single().execute()
            clinic_data = clinic_info.data if clinic_info.data else {}

            # Get services/prices from healthcare schema
            services = supabase.schema('healthcare').table("services").select("*").eq("clinic_id", clinic_id).execute()
            services_list = services.data if services.data else []

            # Build clinic context
            services_text = "\n".join([f"- {s.get('name', 'Service')}: ${s.get('price', 'N/A')}" for s in services_list[:10]])

            clinic_context = f"""
You are a virtual assistant for {clinic_name}.

Clinic Information:
- Name: {clinic_data.get('name', clinic_name)}
- Phone: {clinic_data.get('phone', 'N/A')}
- Address: {clinic_data.get('address', 'N/A')}
- Hours: {clinic_data.get('business_hours', 'Please call for hours')}

Services and Prices:
{services_text if services_text else 'Please inquire about our services'}

You can help patients with:
- Booking appointments
- Answering questions about services and prices
- Providing clinic information
- General inquiries

Be helpful, professional, and specific about our services.
"""
        except Exception as e:
            logger.error(f"Error loading clinic data: {e}")
            clinic_context = f"You are a virtual assistant for {clinic_name}. Help patients with appointments and inquiries."

        # Create request for LangGraph with clinic context
        request = MessageRequest(
            session_id=session_id,
            text=body,
            metadata={
                "channel": "widget",
                "backend": "healthcare-clinic-backend",
                "clinic_id": clinic_id,
                "clinic_name": clinic_name,
                "clinic_context": clinic_context,
                "system_prompt_override": clinic_context,
                "enable_appointment_tools": True,
                "enable_knowledge_base": True
            },
            use_healthcare=True,
            enable_rag=True,
            enable_memory=True
        )

        # Process with LangGraph
        response = await process_message(request)

        return {
            "message": response.response,
            "status": "success",
            "backend": "healthcare-clinic-backend.fly.dev",
            "latency_ms": response.latency_ms,
            "intent": response.intent,
            "routing_path": response.routing_path
        }
    except Exception as e:
        logger.error(f"LangGraph processing error: {e}")
        # Fallback to simple response
        return {
            "message": f"I received your message: '{body}'. AI processing temporarily unavailable. How can I help you?",
            "status": "success",
            "backend": "healthcare-clinic-backend.fly.dev",
            "error": str(e)
        }

@app.post("/api/widget-chat")
async def widget_chat(request: Request):
    """Fast endpoint for widget testing - returns immediate response"""
    try:
        data = await request.json()
        user_message = data.get('body', '')

        logger.info(f"📱 Widget message received: {user_message}")

        # Simple echo response for testing
        response_text = f"I received your message: '{user_message}'. The healthcare-clinic-backend is working! 🎉"

        return {
            "message": response_text,
            "status": "success",
            "backend": "healthcare-clinic-backend.fly.dev"
        }
    except Exception as e:
        logger.error(f"Widget chat error: {e}")
        return {
            "message": "Sorry, there was an error processing your message.",
            "status": "error"
        }

# ============================================================================
# Message Processing Endpoint (WhatsApp/SMS/Web)
# ============================================================================

@app.post("/api/process-message")
async def process_message(request: Request):
    """Process incoming messages from API server with AI and RAG"""
    import time
    import traceback
    import psutil

    start_time = time.time()

    def log_checkpoint(name: str):
        elapsed = time.time() - start_time
        process = psutil.Process()
        memory_mb = process.memory_info().rss / 1024 / 1024
        logger.info(f"⏱️ CHECKPOINT [{name}] - Elapsed: {elapsed:.2f}s | Memory: {memory_mb:.1f}MB")
        return elapsed

    try:
        log_checkpoint("START - Request received")

        # Parse request body
        data = await request.json()
        log_checkpoint("PARSE - Request body parsed")

        logger.info(f"Processing message from: {data.get('from_phone')}")

        # Check environment variables BEFORE import
        required_vars = ['OPENAI_API_KEY', 'SUPABASE_URL', 'SUPABASE_ANON_KEY']
        missing_vars = [var for var in required_vars if not os.environ.get(var)]
        if missing_vars:
            logger.error(f"❌ MISSING ENV VARS: {missing_vars}")
        else:
            logger.info(f"✅ ENV VARS: All required variables present")
        log_checkpoint("ENV_CHECK - Environment variables checked")

        # Module already imported at top level - no import needed here
        # Create message request
        message_request = MessageRequest(**data)
        log_checkpoint("MODEL - Pydantic model created")

        # Process message with AI (tools) - with timeout
        logger.info("🤖 AI START - Calling handle_process_message")
        try:
            import asyncio
            response = await asyncio.wait_for(
                handle_process_message(message_request),
                timeout=25.0  # 25 second timeout
            )
            log_checkpoint("AI - Message processed")
        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            logger.error(f"⏰ TIMEOUT - Message processing exceeded 25s (at {elapsed:.2f}s)")
            return {
                "message": "Lo siento, el procesamiento tomó demasiado tiempo. Por favor, intente de nuevo.",
                "session_id": "timeout",
                "status": "timeout",
                "metadata": {
                    "error": "Processing timeout after 25 seconds",
                    "elapsed_seconds": elapsed
                }
            }

        logger.info(f"Generated response with RAG: {response.message[:100]}...")
        logger.info(f"Knowledge used: {response.metadata.get('knowledge_used', 0)} items")

        total_time = log_checkpoint("COMPLETE - Request completed")
        logger.info(f"✅ TOTAL TIME: {total_time:.2f}s")

        return response.dict()
    except Exception as e:
        elapsed = time.time() - start_time
        error_details = traceback.format_exc()
        logger.error(f"❌ ERROR at {elapsed:.2f}s: {e}")
        logger.error(f"Full traceback:\n{error_details}")
        logger.error(f"Error type: {type(e).__name__}")
        return {
            "message": "Lo siento, hubo un error procesando su mensaje. Por favor, intente de nuevo.",
            "session_id": "error",
            "status": "error",
            "metadata": {
                "error": str(e),
                "error_type": type(e).__name__,
                "elapsed_seconds": elapsed
            }
        }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=True
    )
