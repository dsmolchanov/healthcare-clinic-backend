"""
Healthcare Clinics Backend - Main Application

FACADE MODULE: This module creates the FastAPI app and registers routes.
Heavy initialization is delegated to submodules:
- startup.py: Lifespan manager and worker initialization
- app_factory.py: FastAPI app creation with middleware
- routers_registry.py: All router registrations
"""
import os
import logging
from typing import Dict, Any

from fastapi import Request, HTTPException, Response
from dotenv import load_dotenv

# Apply runtime patches before additional imports
import app.patches.openai_httpx_fix  # noqa: E402,F401

# Load environment variables FIRST
load_dotenv()

# Validate environment configuration
from app.startup_validation import validate_environment
if not validate_environment():
    import logging as _log
    _log.getLogger(__name__).warning(
        "Environment validation failed - some features may not work. "
        "See logs above for details."
    )

# Configure centralized logging
from app.utils.logging_config import configure_logging
configure_logging()
logger = logging.getLogger(__name__)

logger.info("🚀 MODULE LEVEL - app/main.py loading")

# Import Supabase client
from app.database import get_healthcare_client
supabase = get_healthcare_client()
logger.info("Connected to Supabase (using healthcare schema)")

# Create FastAPI app using factory
from app.app_factory import create_app
app = create_app()

# Register all routers
from app.routers_registry import register_all_routers
register_all_routers(app)


# ============================================================================
# Health Check Endpoints
# ============================================================================

@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "status": "healthy",
        "service": "Healthcare Clinics Backend",
        "version": "1.0.0"
    }


@app.api_route(
    "/apps/voice-api/{deprecated_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False
)
async def deprecated_voice_api_routes(deprecated_path: str):
    """Return a clear error for legacy routes."""
    logger.warning(f"Legacy /apps/voice-api/{deprecated_path} route called.")
    raise HTTPException(
        status_code=410,
        detail="The /apps/voice-api/* endpoints have been retired. Please use the /api/* routes instead.",
    )


@app.get("/health")
async def health_check():
    """Instant health check endpoint with memory monitoring."""
    from datetime import datetime
    import psutil

    process = psutil.Process(os.getpid())
    memory_info = process.memory_info()
    memory_mb = memory_info.rss / (1024 * 1024)
    memory_percent = process.memory_percent()

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


@app.get("/health/detailed")
async def detailed_health_check():
    """Detailed health check with database connection test."""
    try:
        result = supabase.table("clinics").select("id").limit(1).execute()
        return {
            "status": "healthy",
            "database": "connected",
            "clinics_accessible": True,
            "message": "Backend server is running and connected to database"
        }
    except Exception as e:
        return {
            "status": "degraded",
            "database": "error",
            "message": "Server is running but database connection failed",
            "error": str(e)
        }


@app.get("/debug/memory")
async def memory_stats():
    """Return memory usage stats (restricted in production)."""
    import psutil

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


@app.get("/api/debug/env")
async def debug_env():
    """Debug endpoint to check environment variables."""
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
            env_status[key] = f"Set ({value[:10]}...)"
        else:
            env_status[key] = "Not set"

    return {
        "environment": os.environ.get('FLY_APP_NAME', 'local'),
        "env_vars": env_status,
        "total_env_vars": len(os.environ),
        "fly_machine_id": os.environ.get('FLY_MACHINE_ID', 'not_on_fly')
    }


@app.post("/admin/clear-cache/doctors/{clinic_id}")
async def clear_doctor_cache(clinic_id: str):
    """Clear Redis cache for doctor info."""
    from app.config import get_redis_client
    redis_client = get_redis_client()
    if redis_client:
        key = f"clinic_doctors:{clinic_id}"
        result = redis_client.delete(key)
        return {"success": True, "deleted": result, "key": key}
    return {"success": False, "error": "No Redis client"}


# ============================================================================
# WhatsApp Webhook Endpoints
# ============================================================================

@app.get("/webhooks/whatsapp")
async def verify_webhook(request: Request):
    """Verify WhatsApp webhook (Meta webhook verification)."""
    params = request.query_params
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


# ============================================================================
# Widget Chat Endpoints
# ============================================================================

@app.get("/api/widget-chat")
async def widget_chat_get(body: str = "", session_id: str = "", clinic_id: str = ""):
    """GET endpoint for widget chat with LangGraph AI."""
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

    if not session_id:
        import time
        session_id = f"widget_{int(time.time())}"

    try:
        from app.api.langgraph_service import process_message, MessageRequest

        # Get clinic context
        clinic_context = await _build_clinic_context(clinic_id)

        request = MessageRequest(
            session_id=session_id,
            text=body,
            metadata={
                "channel": "widget",
                "backend": "healthcare-clinic-backend",
                "clinic_id": clinic_id,
                "system_prompt_override": clinic_context,
                "enable_appointment_tools": True,
                "enable_knowledge_base": True
            },
            use_healthcare=True,
            enable_rag=True,
            enable_memory=True
        )

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
        return {
            "message": f"I received your message: '{body}'. AI processing temporarily unavailable.",
            "status": "success",
            "backend": "healthcare-clinic-backend.fly.dev",
            "error": str(e)
        }


@app.post("/api/widget-chat")
async def widget_chat(request: Request):
    """POST endpoint for widget chat."""
    try:
        data = await request.json()
        user_message = data.get('body', '')
        logger.info(f"📱 Widget message received: {user_message}")

        return {
            "message": f"I received your message: '{user_message}'. The healthcare-clinic-backend is working! 🎉",
            "status": "success",
            "backend": "healthcare-clinic-backend.fly.dev"
        }
    except Exception as e:
        logger.error(f"Widget chat error: {e}")
        return {
            "message": "Sorry, there was an error processing your message.",
            "status": "error"
        }


async def _build_clinic_context(clinic_id: str) -> str:
    """Build clinic context for AI prompts."""
    try:
        clinic_info = supabase.table("clinics").select("*").eq("id", clinic_id).single().execute()
        clinic_data = clinic_info.data if clinic_info.data else {}
        clinic_name = clinic_data.get('name', 'Unknown Clinic')

        services = supabase.schema('healthcare').table("services").select("*").eq("clinic_id", clinic_id).execute()
        services_list = services.data if services.data else []
        services_text = "\n".join([f"- {s.get('name', 'Service')}: ${s.get('price', 'N/A')}" for s in services_list[:10]])

        return f"""
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
        return "You are a virtual assistant. Help patients with appointments and inquiries."


# ============================================================================
# Message Processing Endpoint
# ============================================================================

@app.post("/api/process-message")
async def process_message(request: Request):
    """Process incoming messages from API server with AI and RAG."""
    import time
    import traceback
    import psutil
    import asyncio

    from app.api.pipeline_message_processor import handle_process_message
    from app.schemas.messages import MessageRequest

    start_time = time.time()

    def log_checkpoint(name: str):
        elapsed = time.time() - start_time
        process = psutil.Process()
        memory_mb = process.memory_info().rss / 1024 / 1024
        logger.info(f"⏱️ CHECKPOINT [{name}] - Elapsed: {elapsed:.2f}s | Memory: {memory_mb:.1f}MB")
        return elapsed

    try:
        log_checkpoint("START - Request received")
        data = await request.json()
        log_checkpoint("PARSE - Request body parsed")

        logger.info(f"Processing message from: {data.get('from_phone')}")

        message_request = MessageRequest(**data)
        log_checkpoint("MODEL - Pydantic model created")

        logger.info("🤖 AI START - Calling handle_process_message")
        try:
            response = await asyncio.wait_for(
                handle_process_message(message_request),
                timeout=25.0
            )
            log_checkpoint("AI - Message processed")
        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            logger.error(f"⏰ TIMEOUT - Processing exceeded 25s (at {elapsed:.2f}s)")
            return {
                "message": "Lo siento, el procesamiento tomó demasiado tiempo. Por favor, intente de nuevo.",
                "session_id": "timeout",
                "status": "timeout",
                "metadata": {"error": "Processing timeout", "elapsed_seconds": elapsed}
            }

        total_time = log_checkpoint("COMPLETE")
        logger.info(f"✅ TOTAL TIME: {total_time:.2f}s")

        return response.dict()
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"❌ ERROR at {elapsed:.2f}s: {e}")
        logger.error(f"Full traceback:\n{traceback.format_exc()}")
        return {
            "message": "Lo siento, hubo un error procesando su mensaje.",
            "session_id": "error",
            "status": "error",
            "metadata": {"error": str(e), "elapsed_seconds": elapsed}
        }


# ============================================================================
# Entry Point
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=True
    )
