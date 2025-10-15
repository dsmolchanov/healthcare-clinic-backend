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

from supabase import create_client, Client
# from app.api import quick_onboarding_router  # Disabled - using RPC version instead
from app.api import quick_onboarding_rpc
from app.api import multimodal_upload
from app.api import services_upload
from app.middleware.rate_limiter import webhook_limiter

# Load environment variables FIRST before importing modules that need them
load_dotenv()

# Import message processor at module level AFTER dotenv load
from app.api.multilingual_message_processor import handle_process_message, MessageRequest

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Log module-level initialization
logger.info("🚀 MODULE LEVEL - app/main.py loading")


async def warmup_services(client: httpx.AsyncClient):
    """Warm up external services on startup with timeouts to prevent blocking"""
    warmup_tasks = []

    # Warm OpenAI (tiny prompt) - with timeout
    if os.getenv("OPENAI_API_KEY"):
        async def warmup_openai():
            try:
                from openai import AsyncOpenAI
                openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
                await asyncio.wait_for(
                    openai_client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[{"role": "user", "content": "warmup"}],
                        max_tokens=1
                    ),
                    timeout=3.0
                )
                logger.info("✅ OpenAI warmed up")
            except Exception as e:
                logger.warning(f"OpenAI warmup failed: {e}")
        warmup_tasks.append(warmup_openai())

    # Skip Supabase warmup - it's causing SSL timeouts
    # Supabase will be lazily initialized on first real request

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


# Get Supabase credentials
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")

# Check if credentials are properly set
if not supabase_url:
    logger.error("SUPABASE_URL not set in .env file")
    raise ValueError("SUPABASE_URL is required")

if not supabase_key or supabase_key == "eyJhbG.." or not supabase_key.startswith("eyJ"):
    logger.error("Invalid or missing Supabase key. Please set SUPABASE_ANON_KEY or SUPABASE_SERVICE_ROLE_KEY in .env file")
    logger.info("You can find these keys in your Supabase project settings:")
    logger.info("1. Go to https://supabase.com/dashboard")
    logger.info("2. Select your project")
    logger.info("3. Go to Settings > API")
    logger.info("4. Copy the 'anon public' or 'service_role' key")
    raise ValueError("Valid Supabase key is required")

# Initialize Supabase client with healthcare schema
try:
    from supabase.client import ClientOptions

    # Configure client to use healthcare schema
    options = ClientOptions(
        schema='healthcare',
        auto_refresh_token=True,
        persist_session=False
    )

    supabase: Client = create_client(supabase_url, supabase_key, options=options)
    logger.info(f"Connected to Supabase: {supabase_url} (using healthcare schema)")
except Exception as e:
    logger.error(f"Failed to connect to Supabase: {e}")
    raise

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    # Startup
    logger.info("Starting Healthcare Backend...")
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

    try:
        from app.startup_warmup import warmup_mem0_vector_indices
        mem0_timeout = float(os.getenv("MEM0_WARMUP_TIMEOUT_SECONDS", "6"))
        summary = await asyncio.wait_for(
            warmup_mem0_vector_indices(throttle_ms=75),
            timeout=mem0_timeout
        )
        app.state.mem0_warmup_summary = summary
        logger.info(
            "✅ mem0 warmup scheduled %s/%s clinics (force=%s)",
            summary.get("scheduled"),
            summary.get("total"),
            summary.get("force"),
        )
    except asyncio.TimeoutError:
        logger.warning("mem0 warmup scheduling timed out after %.1fs; continuing startup", mem0_timeout)
    except Exception as e:
        logger.warning(f"Failed to warm up mem0 indices: {e}")

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

    if hasattr(app.state, 'http_client') and app.state.http_client:
        await app.state.http_client.aclose()
        logger.info("✅ HTTP client closed")
    logger.info("Shutting down HIPAA compliance systems...")
    logger.info("Healthcare Backend shutdown complete")

# Create FastAPI app
app = FastAPI(
    title="Healthcare Clinics Backend",
    version="1.0.0",
    lifespan=lifespan,
    redirect_slashes=False  # Prevent HTTP redirects from HTTPS requests
)

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

# Include knowledge management routes
from app.api import knowledge_routes
app.include_router(knowledge_routes.router)

# Include integrations routes for Evolution API and other integrations
from app.api import integrations_routes
app.include_router(integrations_routes.router)

# Include Evolution mock for testing (temporary until Evolution API is deployed)
from app.api import evolution_mock

# Include price list parser API for medical services
from app.api import price_list_api
app.include_router(price_list_api.router)
app.include_router(evolution_mock.router)

# Include WhatsApp webhook handler
from app.api import whatsapp_webhook
app.include_router(whatsapp_webhook.router)

# Include simple WhatsApp webhook for testing
from app.api import whatsapp_webhook_simple
app.include_router(whatsapp_webhook_simple.router)

# Include Evolution webhook with proper URL pattern
from app.api import evolution_webhook
app.include_router(evolution_webhook.router)

# Include Rule Engine routes
from app.api import rules_routes
app.include_router(rules_routes.router)

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
    """Instant health check endpoint - NO external calls to prevent blocking"""
    from datetime import datetime

    # Return immediately with no external I/O - prevents health check timeouts
    return {
        "status": "healthy",
        "service": "Healthcare Clinics Backend",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat()
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

@app.get("/debug/mem0")
async def debug_mem0():
    """Diagnostic endpoint for mem0 status"""
    from app.memory.conversation_memory import get_memory_manager, ConversationMemoryManager
    import os
    import traceback

    try:
        mem_manager = get_memory_manager()

        status = {
            "mem0_available": mem_manager.mem0_available,
            "memory_instance": str(type(mem_manager.memory)) if mem_manager.memory else None,
            "openai_api_key_set": bool(os.environ.get('OPENAI_API_KEY')),
            "qdrant_path": os.environ.get('QDRANT_PATH', '/app/qdrant_data'),
        }

        # Try to check if path exists
        qdrant_path = os.environ.get('QDRANT_PATH', '/app/qdrant_data')
        status["qdrant_path_exists"] = os.path.exists(qdrant_path)

        if os.path.exists(qdrant_path):
            status["qdrant_path_writable"] = os.access(qdrant_path, os.W_OK)
            try:
                status["qdrant_path_contents"] = os.listdir(qdrant_path)[:10]
            except:
                status["qdrant_path_contents"] = "ERROR: Cannot list"

        # Try to initialize a fresh instance to see the actual error
        if not mem_manager.mem0_available:
            try:
                # Try to initialize mem0 directly to capture the real error
                from mem0 import Memory

                mem0_config = {
                    "llm": {
                        "provider": "openai",
                        "config": {
                            "model": "gpt-4o-mini",
                            "temperature": 0.1
                        }
                    },
                    "embedder": {
                        "provider": "openai",
                        "config": {
                            "model": "text-embedding-3-small"
                        }
                    },
                    "vector_store": {
                        "provider": "qdrant",
                        "config": {
                            "collection_name": "whatsapp_memories",
                            # Use subdirectory to avoid mem0 trying to delete the volume mount point
                            "path": os.path.join(os.environ.get('QDRANT_PATH', '/app/qdrant_data'), 'storage'),
                            "embedding_model_dims": 1536
                        }
                    },
                    "version": "v1.1"
                }

                test_memory = Memory.from_config(mem0_config)
                status["test_init_error"] = "SUCCESS: mem0 initialized directly!"
                status["test_memory_type"] = str(type(test_memory))
            except Exception as init_e:
                status["test_init_error"] = str(init_e)
                status["test_init_traceback"] = traceback.format_exc()

        return status
    except Exception as e:
        return {"error": str(e), "type": str(type(e)), "traceback": traceback.format_exc()}

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
        'PINECONE_API_KEY',
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
async def widget_chat_get(body: str = "", session_id: str = ""):
    """GET endpoint for widget chat with LangGraph AI - accepts message as query param"""
    logger.info(f"📱 Widget message received: {body}")

    if not body:
        return {
            "message": "Please send a message!",
            "status": "success",
            "backend": "clinic-webhooks.fly.dev"
        }

    # Use session ID or generate one
    if not session_id:
        import time
        session_id = f"widget_{int(time.time())}"

    try:
        # Import LangGraph service
        from app.api.langgraph_service import process_message, MessageRequest

        # Get clinic information from database
        clinic_id = "e0c84f56-235d-49f2-9a44-37c1be579afc"  # Correct clinic ID
        clinic_name = "Shtern Dental Clinic"

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
                "backend": "clinic-webhooks",
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
            "backend": "clinic-webhooks.fly.dev",
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
            "backend": "clinic-webhooks.fly.dev",
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

        # Process message with AI (tools + mem0) - with timeout
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
