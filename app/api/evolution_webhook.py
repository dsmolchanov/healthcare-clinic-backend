"""
Evolution API Webhook Handler

This webhook handles messages from Evolution API using the expected URL pattern:
/webhooks/evolution/{instance_name}
"""

from fastapi import APIRouter, Request, Path, Body, HTTPException
from typing import Any, Dict, Optional
import os
import json
import logging
import asyncio
from app.api.multilingual_message_processor import MessageRequest, MultilingualMessageProcessor
from app.security.webhook_verification import verify_webhook_signature
from app.services.message_router import MessageType  # Keep MessageType enum for logging
import aiohttp

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/evolution", tags=["webhooks"])

# Evolution API URL
EVOLUTION_API_URL = os.getenv("EVOLUTION_SERVER_URL", "https://evolution-api-prod.fly.dev")

# Initialize message processor with RAG
message_processor = MultilingualMessageProcessor()

# FSM Feature Flag
FSM_ENABLED = os.getenv('ENABLE_FSM', 'false').lower() == 'true'

logger.info(f"Evolution webhook - FSM feature flag: {'ENABLED' if FSM_ENABLED else 'DISABLED'}")

# FSM imports (only if enabled)
if FSM_ENABLED:
    from ..fsm.manager import FSMManager
    from ..fsm.intent_router import IntentRouter
    from ..fsm.slot_manager import SlotManager
    from ..fsm.state_handlers import StateHandler
    from ..fsm.answer_service import AnswerService
    from ..fsm.redis_client import redis_client
    from ..fsm.models import FSMState, ConversationState
    from supabase import create_client
    from supabase.client import ClientOptions

    # Get Supabase client for FSM components
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    options = ClientOptions(schema='healthcare', auto_refresh_token=True, persist_session=False)
    fsm_supabase = create_client(supabase_url, supabase_key, options=options)

    # Initialize FSM components
    fsm_manager = FSMManager()
    intent_router = IntentRouter()
    slot_manager = SlotManager()
    answer_service = AnswerService(fsm_supabase)
    state_handler = StateHandler(fsm_manager, intent_router, slot_manager, answer_service)

@router.post("/whatsapp/{webhook_token}")
async def whatsapp_webhook_v2(
    request: Request,
    webhook_token: str = Path(..., description="Webhook routing token"),
    body: Dict[str, Any] = Body(..., description="Webhook payload from Evolution API")
):
    """
    NEW: Token-based webhook endpoint for WhatsApp messages

    Evolution API sends to: /webhooks/evolution/whatsapp/{webhook_token}

    Benefits:
    - Zero DB queries on cache hit (Redis lookup: token → clinic_id)
    - Secure token-based routing (no instance name exposure)
    - Single indexed query on cache miss

    CRITICAL: Must return IMMEDIATELY to avoid Evolution timeout
    """
    import datetime
    timestamp = datetime.datetime.now().isoformat()

    print(f"\n{'='*80}")
    print(f"[{timestamp}] TOKEN-BASED WEBHOOK RECEIVED")
    print(f"[WhatsApp Webhook V2] Token: {webhook_token[:8]}...")
    print(f"[WhatsApp Webhook V2] Body type: {type(body)}")
    print(f"[WhatsApp Webhook V2] Body keys: {list(body.keys()) if body else 'None'}")

    # Verify webhook signature (OPTIONAL - Evolution doesn't send signatures by default)
    evolution_webhook_secret = os.getenv("EVOLUTION_WEBHOOK_SECRET", "")
    signature = request.headers.get("X-Webhook-Signature")

    # Convert body to JSON bytes for signature verification
    body_bytes = json.dumps(body, separators=(',', ':'), ensure_ascii=False).encode('utf-8')

    if evolution_webhook_secret and signature:
        # Verify signature if both are present
        if verify_webhook_signature('evolution', body=body_bytes, signature=signature):
            print(f"[WhatsApp Webhook V2] ✅ Signature verified")
        else:
            print(f"[WhatsApp Webhook V2] ⚠️  Invalid signature - continuing anyway (optional verification)")
    else:
        print(f"[WhatsApp Webhook V2] ⚠️  No signature verification (secret={bool(evolution_webhook_secret)}, signature={bool(signature)})")

    # Create background task for processing (CRITICAL: return immediately)
    asyncio.create_task(process_webhook_by_token(webhook_token, body_bytes))

    print(f"[WhatsApp Webhook V2] 🏁 Returning response immediately")

    # IMMEDIATE response (Evolution timeout is ~5 seconds)
    return {"status": "ok", "token": webhook_token[:8] + "..."}


async def process_webhook_by_token(webhook_token: str, body_bytes: bytes):
    """
    Process webhook using token-based routing (background task)

    This is the NEW processing path with zero-DB-query cache hits.
    """
    import datetime
    timestamp = datetime.datetime.now().isoformat()

    print(f"\n{'='*80}")
    print(f"[{timestamp}] TOKEN-BASED ASYNC PROCESSING STARTED")
    print(f"[Token Async] Token: {webhook_token[:8]}...")
    print(f"[Token Async] Processing {len(body_bytes)} bytes")
    print(f"{'='*80}")

    try:
        # Parse webhook body
        data = json.loads(body_bytes)
        message_data = data.get("message", {})

        if not message_data:
            print(f"[Token Async] ⚠️  No message data, ignoring")
            return

        # Idempotency check (same as existing flow)
        message_id = message_data.get("key", {}).get("id")
        if not message_id:
            print(f"[Token Async] ⚠️  No message ID, cannot check idempotency")
            return

        # Redis SETNX for idempotency
        from app.config import get_redis_client
        redis_client = get_redis_client()
        idempotency_key = f"webhook:msg:{message_id}"

        is_first_time = redis_client.set(idempotency_key, "1", nx=True, ex=3600)

        if not is_first_time:
            print(f"[Token Async] ⏭️  Duplicate message {message_id}, skipping")
            return

        print(f"[Token Async] ✅ Idempotency check passed: {message_id}")

        # ZERO-QUERY LOOKUP via token cache
        from app.services.whatsapp_clinic_cache import get_whatsapp_clinic_cache
        cache = get_whatsapp_clinic_cache()

        clinic_info = await cache.get_or_fetch_clinic_info_by_token(webhook_token)

        if not clinic_info:
            print(f"[Token Async] ❌ No clinic found for token {webhook_token[:8]}...")
            return

        clinic_id = clinic_info['clinic_id']
        organization_id = clinic_info['organization_id']
        clinic_name = clinic_info.get('name', 'Unknown')

        print(f"[Token Async] ✅ Resolved: token → clinic {clinic_name} ({clinic_id[:8]}...)")

        # Extract message details
        from_number = message_data.get("key", {}).get("remoteJid", "").split("@")[0]
        message_text = message_data.get("message", {}).get("conversation", "")

        # Also try other message formats
        if not message_text:
            nested_message = message_data.get("message", {})
            message_text = (
                nested_message.get("extendedTextMessage", {}).get("text") or
                nested_message.get("imageMessage", {}).get("caption") or
                nested_message.get("videoMessage", {}).get("caption") or
                ""
            )

        print(f"[Token Async] From: {from_number}")
        print(f"[Token Async] Message: {message_text[:100]}...")

        # Route to FSM or multilingual processor (same as existing flow)
        if FSM_ENABLED:
            from app.api.webhooks import process_with_fsm
            fsm_result = await process_with_fsm(
                message_sid=message_id,
                conversation_id=from_number,
                message=message_text,
                context={
                    "clinic_id": clinic_id,
                    "instance_name": instance_name
                }
            )
            ai_response = fsm_result.get("response", "")
        else:
            # Multilingual processor fallback
            processor = MultilingualMessageProcessor()

            request_obj = MessageRequest(
                session_id=f"whatsapp_{from_number}_{clinic_id[:8]}",
                message=message_text,
                clinic_id=clinic_id,
                from_phone=from_number,
                channel="whatsapp",
                language=None
            )

            response_obj = await asyncio.wait_for(processor.process_message(request_obj), timeout=30.0)
            ai_response = response_obj.response

        print(f"[Token Async] ✅ AI response: {ai_response[:100]}...")

        # Send response via Evolution API (use instance_name from config)
        instance_name = clinic_info.get('instance_name')
        if instance_name:
            conversation_id = f"whatsapp_{from_number}_{clinic_id[:8]}"
            await send_whatsapp_via_evolution(
                instance_name,
                from_number,
                ai_response,
                conversation_id,
                clinic_id
            )
        else:
            print(f"[Token Async] ⚠️  No instance_name in clinic_info, cannot send response")

    except Exception as e:
        logger.error(f"[Token Async] ❌ Error processing webhook: {e}", exc_info=True)


@router.post("/{instance_name}")
async def evolution_webhook(
    request: Request,
    instance_name: str = Path(..., description="WhatsApp instance name"),
    body: Dict[str, Any] = Body(..., description="Webhook payload from Evolution API")
):
    """
    Handle incoming messages from Evolution API for specific instance.
    Evolution API sends to: /webhooks/evolution/{instance_name}

    CRITICAL: Must return IMMEDIATELY to avoid Evolution timeout
    Using FastAPI Body parameter to let framework handle body reading
    """
    import datetime
    timestamp = datetime.datetime.now().isoformat()

    print(f"\n{'='*80}")
    print(f"[{timestamp}] WEBHOOK RECEIVED")
    print(f"[Evolution Webhook] Instance: {instance_name}")
    print(f"[Evolution Webhook] Body type: {type(body)}")
    print(f"[Evolution Webhook] Body keys: {list(body.keys()) if body else 'None'}")

    # Verify webhook signature if configured (OPTIONAL - Evolution doesn't send signatures by default)
    evolution_webhook_secret = os.getenv("EVOLUTION_WEBHOOK_SECRET", "")
    signature = request.headers.get("X-Webhook-Signature")

    if evolution_webhook_secret and signature:
        # Signature verification is OPTIONAL - verify if both secret and signature present
        print(f"[Evolution Webhook] Signature header present: {signature[:20]}...")
        # Convert dict back to bytes for signature verification.
        # Match JSON.stringify output (no spaces, UTF-8) so HMAC lines up with Evolution API.
        body_bytes = json.dumps(body, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        if verify_webhook_signature('evolution', body=body_bytes, signature=signature):
            print(f"[Evolution Webhook] ✅ Signature verified")
        else:
            print(f"[Evolution Webhook] ⚠️  Invalid signature - continuing anyway (optional verification)")
    else:
        print(f"[Evolution Webhook] ⚠️  No signature verification (secret={bool(evolution_webhook_secret)}, signature={bool(signature)})")

    print(f"{'='*80}\n")

    # FastAPI has already parsed the body for us
    if body:
        # Log first 500 chars of content for debugging
        body_str = json.dumps(body, indent=2)[:500]
        print(f"[Evolution Webhook] ✅ Successfully received body")
        print(f"[Evolution Webhook] Body preview:\n{body_str}")

        # Convert dict back to bytes for background processing
        body_bytes = json.dumps(body).encode('utf-8')

        # Create a task to process the webhook asynchronously
        task = asyncio.create_task(process_webhook_async(instance_name, body_bytes))
        print(f"[Evolution Webhook] 🚀 Background task created: {task}")
        print(f"[Evolution Webhook] Task ID: {id(task)}")
    else:
        print(f"[Evolution Webhook] ⚠️ No body data to process")

    # ALWAYS return immediately - this is the critical part
    print(f"[Evolution Webhook] 🏁 Returning response immediately to Evolution API")
    print(f"[Evolution Webhook] Response: {{'status': 'ok', 'instance': '{instance_name}'}}")
    return {"status": "ok", "instance": instance_name}


async def process_webhook_async(instance_name: str, body_bytes: bytes):
    """Process webhook in background after returning response"""
    import datetime
    start_time = datetime.datetime.now()

    print(f"\n{'='*80}")
    print(f"[{start_time.isoformat()}] ASYNC PROCESSING STARTED")
    print(f"[Async Process] Instance: {instance_name}")
    print(f"[Async Process] Processing {len(body_bytes)} bytes")
    print(f"[Async Process] Task running in background")
    print(f"{'='*80}\n")

    try:
        # Process the message
        print(f"[Async Process] Calling process_evolution_message...")
        await process_evolution_message(instance_name, body_bytes)

        end_time = datetime.datetime.now()
        duration = (end_time - start_time).total_seconds()
        print(f"\n[Async Process] ✅ COMPLETED in {duration:.2f} seconds")

    except Exception as e:
        end_time = datetime.datetime.now()
        duration = (end_time - start_time).total_seconds()
        print(f"\n[Async Process] ❌ FAILED after {duration:.2f} seconds")
        print(f"[Async Process] Error: {e}")
        import traceback
        print(f"[Async Process] Full traceback:\n{traceback.format_exc()}")


async def process_evolution_message(instance_name: str, body_bytes: bytes):
    """Process Evolution API webhook message in background"""
    import datetime
    process_start = datetime.datetime.now()

    print(f"\n{'='*80}")
    print(f"[{process_start.isoformat()}] MESSAGE PROCESSING STARTED")
    print(f"[Background] Instance: {instance_name}")
    print(f"[Background] Processing {len(body_bytes)} bytes")
    print(f"{'='*80}\n")

    try:
        # Parse JSON from bytes
        print(f"[Background] Step 1: Parsing JSON from bytes...")
        try:
            data = json.loads(body_bytes.decode('utf-8'))
            print(f"[Background] ✅ Successfully parsed JSON")
            print(f"[Background] JSON keys: {list(data.keys())}")
            print(f"[Background] Full JSON data:\n{json.dumps(data, indent=2)[:1000]}")
        except json.JSONDecodeError as e:
            print(f"[Background] ❌ Failed to parse JSON: {e}")
            print(f"[Background] Raw data that failed to parse: {body_bytes[:500]}")
            return

        # IDEMPOTENCY CHECK: Reject duplicate messages using Redis SETNX
        # Extract message ID for deduplication
        message_data = data.get("message", {})
        key = message_data.get("key", {})
        message_id = key.get("id")

        if message_id:
            from app.config import get_redis_client
            redis_client = get_redis_client()
            idempotency_key = f"webhook:msg:{message_id}"
            idempotency_ttl = 3600  # 1 hour - prevent processing same message twice within this window

            # Try to set the key (SETNX - set if not exists)
            # Returns 1 if key was set (first time seeing this message)
            # Returns 0 if key already exists (duplicate message)
            idempotency_start = datetime.datetime.now()
            is_first_time = redis_client.set(idempotency_key, "1", nx=True, ex=idempotency_ttl)
            idempotency_duration = (datetime.datetime.now() - idempotency_start).total_seconds() * 1000

            if not is_first_time:
                print(f"[Background] ⏭️ DUPLICATE MESSAGE DETECTED (idempotency check: {idempotency_duration:.2f}ms)")
                print(f"[Background] Message ID: {message_id}")
                print(f"[Background] Skipping processing - message already handled")
                return
            else:
                print(f"[Background] ✅ Idempotency check passed ({idempotency_duration:.2f}ms)")
                print(f"[Background] Message ID: {message_id} (first time processing)")
        else:
            print(f"[Background] ⚠️ No message ID found - skipping idempotency check")

        # Check if this is a CONNECTION_UPDATE event (not a message)
        event_type = data.get("event")
        if event_type == "CONNECTION_UPDATE":
            print(f"[Background] 📡 CONNECTION_UPDATE event received")
            connection_data = data.get("data", {})
            state = connection_data.get("state")
            phone = connection_data.get("phone")
            connected = connection_data.get("connected", False)

            print(f"[Background] Connection state: {state}")
            print(f"[Background] Phone number: {phone}")
            print(f"[Background] Connected: {connected}")

            # Update integration status in database
            if state == "open" and connected:
                print(f"[Background] ✅ WhatsApp connected! Updating integration status...")
                from app.main import supabase

                try:
                    # Update integration status to connected
                    result = supabase.schema("healthcare").table("integrations").update({
                        "status": "connected",
                        "config": {
                            "instance_name": instance_name,
                            "phone_number": phone,
                            "connected_at": datetime.datetime.utcnow().isoformat()
                        },
                        "enabled": True
                    }).eq("config->>instance_name", instance_name).execute()

                    print(f"[Background] ✅ Integration status updated to connected")
                    print(f"[Background] Updated records: {len(result.data) if result.data else 0}")
                except Exception as db_error:
                    print(f"[Background] ⚠️  Failed to update integration status: {db_error}")

            # CONNECTION_UPDATE events don't have messages, so return early
            return

        # Evolution API sends both instanceName in body AND in URL path
        # The URL path is more reliable
        print(f"[Background] Step 2: Extracting message data...")
        actual_instance = instance_name  # Use the path parameter

        print(f"[Background] Using instance from URL path: {actual_instance}")
        print(f"[Background] Message data keys: {list(message_data.keys())}")
        if message_data:
            print(f"[Background] Message data content:\n{json.dumps(message_data, indent=2)[:500]}")

        # Extract message details (key and message_data already extracted above for idempotency)
        print(f"[Background] Step 3: Extracting sender details...")
        from_number = key.get("remoteJid", "").replace("@s.whatsapp.net", "")
        is_from_me = key.get("fromMe", False)

        print(f"[Background] Key data: {key}")
        print(f"[Background] From number: {from_number}")
        print(f"[Background] Is from me: {is_from_me}")

        # Skip our own messages
        if is_from_me:
            print(f"[Background] ⏭️ Ignoring own message")
            return

        # Extract push name (sender's name)
        push_name = message_data.get("pushName", "WhatsApp User")

        # Extract text from various message formats
        text = ""
        nested_message = message_data.get("message", {})
        if nested_message:
            text = (
                nested_message.get("conversation") or
                nested_message.get("extendedTextMessage", {}).get("text") or
                nested_message.get("imageMessage", {}).get("caption") or
                nested_message.get("videoMessage", {}).get("caption") or
                ""
            )

        # Also check if text is directly in message_data
        if not text:
            text = message_data.get("text", "")

        # Also extract from direct if not found
        if not from_number:
            from_number = message_data.get("from", "")

        if not text or not from_number:
            print(f"[Background] ⚠️ Ignoring message - missing required data")
            print(f"[Background] Text present: {bool(text)} (length: {len(text) if text else 0})")
            print(f"[Background] From number present: {bool(from_number)}")
            print(f"[Background] Text content: '{text}'")
            print(f"[Background] From: '{from_number}'")
            return

        # OPTIMIZATION: Use prewarm cache to resolve instance → clinic (zero DB queries!)
        from app.services.whatsapp_clinic_cache import get_whatsapp_clinic_cache

        cache = get_whatsapp_clinic_cache()
        clinic_info = await cache.get_or_fetch_clinic_info(actual_instance)

        if not clinic_info:
            print(f"[Background] ❌ Could not resolve clinic for instance: {actual_instance}")
            print(f"[Background] Cache miss and DB lookup failed - rejecting message")
            return  # Do NOT process message if we can't resolve clinic

        # Extract clinic info from cache
        clinic_id = clinic_info.get('clinic_id')
        actual_organization_id = clinic_info.get('organization_id')
        clinic_name = clinic_info.get('name', 'Clinic')

        print(f"[Background] ✅ Resolved from cache (ZERO DB queries!)")
        print(f"[Background] Organization ID: {actual_organization_id}")
        print(f"[Background] Clinic ID: {clinic_id}")
        print(f"[Background] Clinic name: {clinic_name}")

        print(f"\n[Background] Step 4: Valid message received!")
        print(f"[Background] From: {from_number} ({push_name})")
        print(f"[Background] Message text: '{text}'")
        print(f"[Background] Organization ID: {actual_organization_id}")
        print(f"[Background] Clinic ID: {clinic_id}")

        # MULTI-AGENT: Load orchestrator agent for organization
        from app.services.agent_service import get_agent_service
        from app.services.orchestrator_factory import get_orchestrator_factory

        agent_service = get_agent_service()
        orchestrator_agent = await agent_service.get_agent_for_organization(
            organization_id=actual_organization_id,
            agent_type="receptionist"  # Main orchestrator
        )

        if not orchestrator_agent:
            print(f"[Background] ⚠️ No orchestrator agent found for organization {actual_organization_id}")
            print(f"[Background] Falling back to legacy processing")
            # Continue with legacy flow
        else:
            print(f"[Background] ✅ Loaded orchestrator: {orchestrator_agent.name} (type={orchestrator_agent.type})")

        # MULTI-AGENT: Send quick ack in parallel (non-blocking)
        # DISABLED: Quick ack temporarily disabled
        quick_ack_task = None
        quick_ack_config = {}
        if orchestrator_agent:
            quick_ack_config = orchestrator_agent.quick_ack_config

        if False and quick_ack_config.get("enabled"):  # DISABLED
            from app.services.whatsapp_queue.evolution_client import send_typing_indicator, send_quick_ack

            async def send_quick_ack_delayed():
                """Send quick ack after delay, can be cancelled if response comes first"""
                try:
                    print(f"[Background] 🚀 Multi-Agent: Starting quick ack task...")

                    # Stage 1: Show typing indicator immediately
                    typing_success = await send_typing_indicator(actual_instance, from_number)
                    if typing_success:
                        print(f"[Background] ✅ Typing indicator sent")
                    else:
                        print(f"[Background] ⚠️ Typing indicator failed (non-critical)")

                    # Stage 2: Wait configured delay, then send quick ack
                    quick_ack_delay = quick_ack_config.get("delay_ms", 500) / 1000.0
                    print(f"[Background] ⏰ Waiting {quick_ack_delay}s before sending quick ack...")
                    await asyncio.sleep(quick_ack_delay)

                    # If we got here, the delay expired - send quick ack
                    # Detect language for quick ack
                    detected_language = detect_language_simple(text)
                    print(f"[Background] Detected language: {detected_language}")

                    # Get language-specific quick ack message from agent config
                    quick_ack_message = orchestrator_agent.get_quick_ack_message(detected_language)
                    if quick_ack_message:
                        ack_success = await send_quick_ack(actual_instance, from_number, quick_ack_message)
                        if ack_success:
                            print(f"[Background] ✅ Quick ack sent: '{quick_ack_message}' (lang={detected_language})")
                            return True  # Mark that quick ack was sent
                        else:
                            print(f"[Background] ⚠️ Quick ack failed (non-critical)")
                            return False
                    else:
                        print(f"[Background] ⚠️ No quick ack message configured for language: {detected_language}")
                        return False
                except asyncio.CancelledError:
                    print(f"[Background] ⚠️ Quick ack cancelled - actual response arrived first (< {quick_ack_config.get('delay_ms', 500)}ms)")
                    raise

            # Start quick ack task in background (non-blocking)
            quick_ack_task = asyncio.create_task(send_quick_ack_delayed())
            print(f"[Background] 🎯 Quick ack task started in parallel")

        # Determine message type (text vs voice note)
        message_type = MessageType.TEXT
        nested_message = message_data.get("message", {})
        if nested_message and nested_message.get("audioMessage"):
            message_type = MessageType.VOICE_NOTE
            print(f"[Background] Detected VOICE NOTE message")
        else:
            print(f"[Background] Detected TEXT message")

        # Create session ID from phone number
        session_id = f"whatsapp_{from_number}_{actual_instance}"

        # Route message through FSM or multilingual processor
        print(f"\n[Background] Step 5: Processing message...")
        print(f"[Background] Message type: {message_type.value}")
        print(f"[Background] Session ID: {session_id}")

        ai_start = datetime.datetime.now()

        # Use local variable to track if we should use FSM for this message
        use_fsm = FSM_ENABLED

        # Check if FSM is enabled
        if use_fsm:
            print(f"[Background] 🔄 Routing to FSM processing")
            try:
                # Process through FSM
                from app.api.webhooks import process_with_fsm

                fsm_result = await process_with_fsm(
                    message_sid=key.get("id"),
                    conversation_id=from_number,
                    message=text,
                    context={
                        "clinic_id": clinic_id,
                        "instance_name": actual_instance,
                        "user_name": push_name,
                        "session_id": session_id
                    }
                )

                ai_response = fsm_result.get("response", "")
                routing_path = "fsm"
                latency_ms = fsm_result.get("processing_time_ms", 0)

                print(f"[Background] ✅ Message processed successfully via FSM")

            except Exception as fsm_error:
                print(f"[Background] ❌ FSM processing failed: {fsm_error}")
                print(f"[Background] 🔄 Falling back to multilingual processor")
                # Fallback to multilingual processor on FSM error
                use_fsm = False  # Disable for this message only

        if not use_fsm:
            print(f"[Background] 🔄 Processing with multilingual processor (with memory support)...")
            try:
                # Use the NEW message processor with RouterService and FastPathService
                # This includes memory retrieval and fast-path routing
                request_obj = MessageRequest(
                    from_phone=from_number,
                    to_phone=actual_instance,  # Use instance as "to" identifier
                    body=text,
                    message_sid=key.get("id"),
                    clinic_id=clinic_id,
                    clinic_name=clinic_name or "Clinic",
                    channel="whatsapp",
                    profile_name=push_name,
                    metadata={
                        "instance_name": actual_instance,
                        "user_name": push_name,
                        "whatsapp_message_id": key.get("id"),
                        "session_id": session_id
                    }
                )

                # Process with timeout
                message_response = await asyncio.wait_for(
                    message_processor.process_message(request_obj),
                    timeout=30.0  # 30 second max for AI processing
                )

                # Extract response from MessageResponse object
                ai_response = message_response.message
                routing_path = message_response.metadata.get("routing_path", "multilingual_processor")
                latency_ms = message_response.metadata.get("processing_time_ms", 0)

                print(f"[Background] ✅ Message processed successfully via {routing_path}")

            except asyncio.TimeoutError:
                print(f"[Background] ⏰ Processing timed out after 30s - using fallback response")
                # Use fallback response on timeout
                ai_response = "Thank you for your message. We're processing your request and will respond shortly."
                routing_path = "timeout_fallback"
                latency_ms = 30000
                # Continue to send this fallback message
            except Exception as routing_error:
                print(f"[Background] ❌ Processing error: {routing_error}")
                import traceback
                print(f"[Background] Processing traceback:\n{traceback.format_exc()}")
                # Use error fallback
                ai_response = "We received your message. Please try again or contact us directly."
                routing_path = "error_fallback"
                latency_ms = 0

        ai_end = datetime.datetime.now()
        ai_duration = (ai_end - ai_start).total_seconds()

        print(f"[Background] ✅ Response received via {routing_path} in {ai_duration:.2f}s ({latency_ms:.2f}ms)")
        print(f"[Background] AI response length: {len(ai_response)} chars")
        print(f"[Background] AI response: {ai_response[:300]}...")

        # Log performance metrics
        if latency_ms > 500 and message_type == MessageType.TEXT:
            print(f"[Background] ⚠️ Text response exceeded 500ms target: {latency_ms:.2f}ms")
        elif latency_ms <= 500 and message_type == MessageType.TEXT:
            print(f"[Background] ✅ Text response met <500ms target: {latency_ms:.2f}ms")

        # Check if quick ack was sent or should be cancelled
        quick_ack_was_sent = False
        quick_ack_delay_s = quick_ack_config.get("delay_ms", 500) / 1000.0 if quick_ack_task else 0
        if quick_ack_task:
            if quick_ack_task.done():
                # Quick ack task completed - check if it was sent
                try:
                    quick_ack_was_sent = quick_ack_task.result()
                    if quick_ack_was_sent:
                        print(f"[Background] ℹ️ Quick ack was already sent (response took {ai_duration:.2f}s > {quick_ack_delay_s:.2f}s delay)")
                except asyncio.CancelledError:
                    print(f"[Background] ℹ️ Quick ack was cancelled earlier")
            else:
                # Quick ack still waiting - cancel it since actual response is ready
                if ai_duration < quick_ack_delay_s:
                    print(f"[Background] 🚫 Cancelled quick ack - actual response ready in {ai_duration:.2f}s (< {quick_ack_delay_s:.2f}s delay)")
                else:
                    print(f"[Background] ⚠️ Quick ack task still running after {ai_duration:.2f}s (> {quick_ack_delay_s:.2f}s) - cancelling")
                quick_ack_task.cancel()
                try:
                    await quick_ack_task
                except asyncio.CancelledError:
                    pass  # Expected

        # Send response back via Evolution API
        print(f"\n[Background] Step 6: Sending WhatsApp response...")
        send_start = datetime.datetime.now()
        send_result = await send_whatsapp_via_evolution(
            actual_instance,
            from_number,
            ai_response,
            session_id,
            clinic_id
        )
        send_end = datetime.datetime.now()
        send_duration = (send_end - send_start).total_seconds()

        if send_result:
            print(f"[Background] ✅ Successfully sent response in {send_duration:.2f}s")
        else:
            print(f"[Background] ❌ Failed to send response after {send_duration:.2f}s")

        process_end = datetime.datetime.now()
        total_duration = (process_end - process_start).total_seconds()
        print(f"\n[Background] 🏁 Total processing time: {total_duration:.2f} seconds")
        print(f"[Background] Breakdown: AI={ai_duration:.2f}s, Send={send_duration:.2f}s")

    except Exception as e:
        process_end = datetime.datetime.now()
        total_duration = (process_end - process_start).total_seconds()
        print(f"\n[Background] ❌ Error after {total_duration:.2f} seconds")
        print(f"[Background] Error type: {type(e).__name__}")
        print(f"[Background] Error message: {e}")
        import traceback
        print(f"[Background] Full traceback:\n{traceback.format_exc()}")


async def get_ai_response_with_rag(user_message: str, from_number: str, clinic_id: str, user_name: str) -> str:
    """Generate AI response using RAG-enabled multilingual processor"""
    import datetime
    rag_start = datetime.datetime.now()

    print(f"\n{'='*80}")
    print(f"[{rag_start.isoformat()}] AI RESPONSE GENERATION")
    print(f"[RAG] User message: '{user_message}'")
    print(f"[RAG] From: {from_number} ({user_name})")
    print(f"[RAG] Clinic ID: {clinic_id}")
    print(f"{'='*80}\n")

    try:
        # Create message request for RAG processor
        print(f"[RAG] Step 1: Creating MessageRequest object...")
        message_sid = f"whatsapp_{from_number}_{os.urandom(8).hex()}"

        message_request = MessageRequest(
            from_phone=from_number,
            to_phone="+14155238886",  # WhatsApp Business number
            body=user_message,
            message_sid=message_sid,
            clinic_id=clinic_id,
            clinic_name="Shtern Dental Clinic",
            channel="whatsapp",
            profile_name=user_name,
            metadata={}
        )

        print(f"[RAG] Message SID: {message_sid}")
        print(f"[RAG] Request object created successfully")

        # Process with RAG
        print(f"\n[RAG] Step 2: Processing with MultilingualMessageProcessor...")
        print(f"[RAG] Calling message_processor.process_message()...")

        process_start = datetime.datetime.now()
        response = await message_processor.process_message(message_request)
        process_end = datetime.datetime.now()
        process_duration = (process_end - process_start).total_seconds()

        print(f"[RAG] ✅ Processing completed in {process_duration:.2f}s")
        print(f"[RAG] Response type: {type(response)}")
        print(f"[RAG] Response message length: {len(response.message)} chars")
        print(f"[RAG] Response preview: {response.message[:300]}...")

        rag_end = datetime.datetime.now()
        total_duration = (rag_end - rag_start).total_seconds()
        print(f"\n[RAG] Total AI generation time: {total_duration:.2f}s")

        return response.message

    except Exception as e:
        rag_end = datetime.datetime.now()
        total_duration = (rag_end - rag_start).total_seconds()

        print(f"\n[RAG] ❌ Error after {total_duration:.2f}s")
        print(f"[RAG] Error type: {type(e).__name__}")
        print(f"[RAG] Error message: {e}")

        import traceback
        print(f"[RAG] Full traceback:\n{traceback.format_exc()}")

        # Fallback to basic response
        fallback = "I apologize, but I'm having trouble processing your message. Please try again or call our clinic directly at +1-234-567-8900."
        print(f"[RAG] Returning fallback response: {fallback}")
        return fallback


async def send_whatsapp_via_evolution(
    instance_name: str,
    to_number: str,
    text: str,
    conversation_id: str,
    clinic_id: str
) -> bool:
    """
    Write WhatsApp message to outbox for async processing by OutboxProcessor worker.

    This function writes the message to the database outbox table with a timeout cap.
    The OutboxProcessor worker will pick it up and deliver it asynchronously.

    Args:
        instance_name: WhatsApp instance name
        to_number: Recipient phone number
        text: Message text content
        conversation_id: Conversation identifier for replay capability
        clinic_id: UUID of the clinic

    Returns:
        True if message was successfully written to outbox, False on immediate error
    """
    from app.services.outbox_service import write_to_outbox
    import uuid

    message_id = str(uuid.uuid4())

    print(f"[SendMessage] Writing message {message_id} to outbox for {to_number}")
    print(f"[SendMessage] Instance: {instance_name}")
    print(f"[SendMessage] Text length: {len(text)} chars")
    print(f"[SendMessage] Text preview: {text[:100]}...")

    async def _write_to_outbox():
        try:
            # Write to outbox table (returns immediately)
            success = await write_to_outbox(
                instance_name=instance_name,
                to_number=to_number,
                message_text=text,
                conversation_id=conversation_id,
                clinic_id=clinic_id,
                message_id=message_id
            )

            if success:
                print(f"[SendMessage] ✅ Message written to outbox (id: {message_id})")
                return True
            else:
                print(f"[SendMessage] ❌ Failed to write message to outbox")
                return False

        except Exception as e:
            print(f"[SendMessage] ❌ Outbox write error: {e}")
            import traceback
            print(f"[SendMessage] Traceback: {traceback.format_exc()[:300]}")
            return False

    try:
        # Cap write operation at 1s - don't block on slow database
        return await asyncio.wait_for(_write_to_outbox(), timeout=1.0)
    except asyncio.TimeoutError:
        print(f"[SendMessage] ⚠️ Outbox write timed out (>1s), continuing")
        # Return True optimistically - the write may still complete
        return True


# Module-level org→clinic cache (10 min TTL)
_org_to_clinic_cache: Dict[str, tuple] = {}  # org_id → (clinic_id, timestamp)
_ORG_CLINIC_CACHE_TTL = 600  # 10 minutes

async def get_clinic_for_org_cached(organization_id: str) -> Optional[str]:
    """
    Get clinic_id for organization with caching and timeout protection.
    Returns None if lookup fails or times out.
    """
    from time import time

    # Check cache first
    cached = _org_to_clinic_cache.get(organization_id)
    if cached:
        clinic_id, timestamp = cached
        if time() - timestamp < _ORG_CLINIC_CACHE_TTL:
            logger.debug(f"✅ Org→clinic cache hit: {organization_id} → {clinic_id}")
            return clinic_id

    # Fetch with timeout protection
    try:
        from app.db.supabase_client import get_supabase_client
        supabase = get_supabase_client()

        result = await asyncio.wait_for(
            supabase.table('clinics')
                .select('id, organization_id')
                .eq('organization_id', organization_id)
                .limit(1)
                .execute(),
            timeout=0.6  # 600ms cap
        )

        if result.data and len(result.data) > 0:
            clinic_id = result.data[0]['id']
            # Cache the result
            _org_to_clinic_cache[organization_id] = (clinic_id, time())
            logger.info(f"Mapped org {organization_id} → clinic {clinic_id}")
            return clinic_id

        logger.warning(f"No clinic found for organization {organization_id}")
        return None

    except asyncio.TimeoutError:
        logger.warning(f"Org→clinic lookup timed out (>600ms) for {organization_id}")
        return None
    except Exception as e:
        logger.warning(f"Org→clinic lookup error: {e}")
        return None

def extract_org_id_from_instance(instance_name: str) -> str:
    """
    Extract organization ID from instance name

    Expected formats:
    - "clinic-{uuid}"
    - "org-{uuid}"
    - "{uuid}"
    """
    import re
    from app.db.supabase_client import get_supabase_client

    # Try to extract UUID from instance name
    patterns = [
        r"clinic-([0-9a-f-]{36})",
        r"org-([0-9a-f-]{36})",
        r"^([0-9a-f-]{36})$"
    ]

    for pattern in patterns:
        match = re.search(pattern, instance_name)
        if match:
            return match.group(1)

    # Fallback: query database for instance name mapping
    try:
        supabase = get_supabase_client()

        # First try core.whatsapp_business_configs
        try:
            result = supabase.rpc(
                "get_whatsapp_config_by_agent_id",
                {"agent_identifier": instance_name}
            ).execute()

            if result.data and len(result.data) > 0:
                logger.info(f"Found org ID in whatsapp_business_configs: {result.data[0]['organization_id']}")
                return result.data[0]["organization_id"]
        except Exception as e:
            logger.debug(f"whatsapp_business_configs lookup failed: {e}")

        # Then try healthcare.integrations
        try:
            from supabase.client import ClientOptions
            from supabase import create_client
            import os

            healthcare_options = ClientOptions(schema='healthcare')
            healthcare_supabase = create_client(
                os.getenv("SUPABASE_URL"),
                os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
                healthcare_options
            )

            result = healthcare_supabase.from_('integrations').select('organization_id, config').eq('type', 'whatsapp').execute()

            # Check if any integration has this instance name in config
            for integration in result.data:
                config_instance = integration.get('config', {}).get('instance_name')
                if config_instance == instance_name:
                    org_id = integration['organization_id']
                    logger.info(f"Found org ID in healthcare.integrations: {org_id}")
                    return org_id
        except Exception as e:
            logger.debug(f"healthcare.integrations lookup failed: {e}")

    except Exception as e:
        logger.error(f"Failed to lookup org ID for instance {instance_name}: {e}")

    logger.warning(f"Could not find organization ID for instance: {instance_name}")
    return None


def detect_language_simple(text: str) -> str:
    """
    Simple language detection based on character set

    Returns:
        Language code: 'ru', 'he', or 'en'
    """
    # Count Cyrillic characters
    cyrillic_count = sum(1 for c in text if '\u0400' <= c <= '\u04FF')
    # Count Hebrew characters
    hebrew_count = sum(1 for c in text if '\u0590' <= c <= '\u05FF')

    total_chars = len(text)
    if total_chars == 0:
        return "en"

    # If more than 30% Cyrillic, it's Russian
    if cyrillic_count / total_chars > 0.3:
        return "ru"

    # If more than 30% Hebrew, it's Hebrew
    if hebrew_count / total_chars > 0.3:
        return "he"

    # Default to English
    return "en"


@router.get("/test")
async def test_evolution_webhook():
    """Test endpoint to verify Evolution webhook is working"""
    return {
        "status": "ok",
        "webhook": "Evolution webhook is active",
        "pattern": "/webhooks/evolution/{instance_name}"
    }
