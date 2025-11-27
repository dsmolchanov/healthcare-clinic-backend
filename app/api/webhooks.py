"""
Webhook endpoints for real-time status updates and WhatsApp message handling
"""
from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
from fastapi.responses import StreamingResponse
from typing import Dict, Optional, Any
import json
import asyncio
import os
import logging
from datetime import datetime
from supabase import create_client, Client
from supabase.client import ClientOptions
import uuid

# FSM imports
from ..fsm.manager import FSMManager
from ..fsm.intent_router import IntentRouter
from ..fsm.slot_manager import SlotManager
from ..fsm.state_handlers import StateHandler
from ..fsm.answer_service import AnswerService
from ..fsm.redis_client import redis_client
from ..fsm.models import FSMState, ConversationState
from ..services.appointment_booking_service import AppointmentBookingService

logger = logging.getLogger(__name__)

# Initialize Supabase client for webhooks
options = ClientOptions(
    schema='healthcare',
    auto_refresh_token=True,
    persist_session=False
)
supabase: Client = create_client(
    os.environ.get("SUPABASE_URL"),
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_ANON_KEY"),
    options=options
)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Store active SSE connections
active_connections: Dict[str, asyncio.Queue] = {}

# Initialize FSM components
fsm_manager = FSMManager()
intent_router = IntentRouter()
slot_manager = SlotManager()
answer_service = AnswerService(supabase_client=supabase)
state_handler = StateHandler(fsm_manager, intent_router, slot_manager, answer_service)
booking_service = AppointmentBookingService(supabase_client=supabase)

# Feature flag - default disabled for safe rollout
FSM_ENABLED = os.getenv('ENABLE_FSM', 'false').lower() == 'true'

logger.info(f"FSM feature flag: {'ENABLED' if FSM_ENABLED else 'DISABLED'}")


# ============================================================================
# FSM Processing Functions
# ============================================================================

async def process_with_fsm(
    message_sid: str,
    conversation_id: str,
    message: str,
    context: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Process message through FSM pipeline.

    8-step processing flow:
    1. Check idempotency (prevent duplicate processing)
    2. Load FSM state
    3. Detect intent
    4. Call appropriate state handler
    5. Handle BOOKING state (execute booking)
    6. Save new state (with CAS retry)
    7. Cache response for idempotency
    8. Return response

    Args:
        message_sid: Unique message identifier (e.g., Twilio MessageSid)
        conversation_id: Unique conversation identifier (e.g., phone number)
        message: User message text
        context: Full webhook payload for additional context

    Returns:
        Response dictionary with message and state info
    """
    try:
        # Step 1: Check idempotency
        logger.info(f"Processing message {message_sid} for conversation {conversation_id}")
        cached_response = await fsm_manager.check_idempotency(message_sid)
        if cached_response:
            logger.info(f"Returning cached response for message {message_sid}")
            return {"response": cached_response, "cached": True}

        # Step 2: Load FSM state
        # Check if clinic_id is provided in context, otherwise lookup
        clinic_id = context.get("clinic_id") or await get_clinic_id_from_number(conversation_id)
        state = await fsm_manager.load_state(conversation_id, clinic_id)
        logger.info(f"Loaded state: {state.current_state}, version: {state.version}")

        # Step 3: Detect intent (with context)
        intent = intent_router.detect_intent(
            message,
            state.current_state,
            last_prompt=state.last_prompt
        )
        logger.info(
            f"Detected intent: {intent.label}, "
            f"topic: {intent.topic}, "
            f"entities: {intent.entities}"
        )

        # Step 4: Handle based on current state
        if state.current_state == ConversationState.GREETING:
            new_state, response = await state_handler.handle_greeting(
                state, message, intent
            )
        elif state.current_state == ConversationState.COLLECTING_SLOTS:
            new_state, response = await state_handler.handle_collecting_slots(
                state, message, intent
            )
        elif state.current_state == ConversationState.AWAITING_CONFIRMATION:
            new_state, response = await state_handler.handle_awaiting_confirmation(
                state, message, intent
            )
        elif state.current_state == ConversationState.DISAMBIGUATING:
            new_state, response = await state_handler.handle_disambiguating(
                state, message, intent
            )
        elif state.current_state == ConversationState.AWAITING_CLARIFICATION:
            new_state, response = await state_handler.handle_awaiting_clarification(
                state, message, intent
            )
        elif state.current_state == ConversationState.BOOKING:
            # BOOKING state should not receive user input - immediate execution
            new_state = state
            response = "Оформляю запись..."
        elif state.current_state in [ConversationState.COMPLETED, ConversationState.FAILED]:
            # Terminal states - start new conversation
            logger.info(f"Terminal state {state.current_state}, starting new conversation")
            new_state = await fsm_manager.load_state(conversation_id, clinic_id)
            new_state, response = await state_handler.handle_greeting(
                new_state, message, intent
            )
        else:
            # Fallback
            logger.warning(f"Unexpected state: {state.current_state}")
            new_state = state
            response = "Извините, произошла ошибка. Попробуйте ещё раз."

        # Step 5: Handle BOOKING state (execute actual booking)
        if new_state.current_state == ConversationState.BOOKING:
            logger.info("Executing booking...")
            booking_result = await execute_booking(new_state)

            if booking_result['success']:
                new_state = await fsm_manager.transition_state(
                    new_state,
                    ConversationState.COMPLETED
                )
                response = f"✅ Запись создана! Номер: {booking_result['booking_id']}"
                logger.info(f"Booking successful: {booking_result['booking_id']}")
            else:
                new_state = await fsm_manager.transition_state(
                    new_state,
                    ConversationState.FAILED
                )
                response = f"❌ Ошибка бронирования: {booking_result['error']}"
                logger.error(f"Booking failed: {booking_result['error']}")

        # Step 6: Save new state (with CAS retry)
        save_success = await fsm_manager.save_state(new_state)
        if not save_success:
            # CAS conflict - state was modified by another request
            logger.error(f"CAS conflict saving state for conversation {conversation_id}")
            response = "Произошла ошибка. Пожалуйста, повторите запрос."
        else:
            logger.info(f"State saved successfully: {new_state.current_state}, version: {new_state.version}")

        # Step 7: Cache response for idempotency
        await fsm_manager.cache_response(message_sid, response)

        # Step 8: Return response
        state_value = new_state.current_state.value if hasattr(new_state.current_state, 'value') else str(new_state.current_state)
        return {
            "response": response,
            "state": state_value,
            "cached": False
        }

    except Exception as e:
        logger.exception(f"Error processing message with FSM: {e}")
        return {
            "response": "Произошла ошибка. Пожалуйста, попробуйте позже.",
            "error": str(e)
        }


async def execute_booking(state: FSMState) -> Dict[str, Any]:
    """
    Execute actual booking in database.

    Extracts slot values from FSM state and calls the appointment booking service.

    Args:
        state: FSM state with confirmed slots

    Returns:
        Dictionary with success status and booking_id or error message
    """
    try:
        # Extract confirmed slots
        doctor = state.get_slot_value("doctor")
        date = state.get_slot_value("date")
        time_slot = state.get_slot_value("time")
        patient_name = state.get_slot_value("patient_name")

        if not all([doctor, date, time_slot]):
            logger.error("Missing required slots for booking")
            return {
                "success": False,
                "error": "Отсутствуют обязательные данные для записи"
            }

        logger.info(f"Executing booking: doctor={doctor}, date={date}, time={time_slot}")

        # Prepare appointment details
        appointment_details = {
            "doctor_id": doctor,  # Assuming doctor slot contains doctor_id
            "date": date,
            "time": time_slot,
            "patient_name": patient_name or "WhatsApp User",
            "service_type": state.get_slot_value("service") or "Консультация"
        }

        # Call booking service
        result = await booking_service.book_appointment(
            patient_phone=state.conversation_id,
            clinic_id=state.clinic_id,
            appointment_details=appointment_details,
            idempotency_key=f"{state.conversation_id}_{state.version}"
        )

        if result.get('success'):
            return {
                "success": True,
                "booking_id": result.get('appointment_id', 'N/A')
            }
        else:
            return {
                "success": False,
                "error": result.get('reason', 'Неизвестная ошибка')
            }

    except Exception as e:
        logger.exception(f"Error executing booking: {e}")
        return {
            "success": False,
            "error": str(e)
        }


async def get_clinic_id_from_number(phone_number: str) -> str:
    """
    Look up clinic ID from healthcare.integrations table by phone number.

    Args:
        phone_number: WhatsApp business phone number (normalized format)

    Returns:
        clinic_id if found

    Raises:
        ValueError: If no integration found for phone number
    """
    # Normalize phone number (remove spaces, dashes, parentheses)
    normalized_phone = ''.join(filter(str.isdigit, phone_number))

    # Try with + prefix, without prefix, and original format
    for phone_variant in [f"+{normalized_phone}", normalized_phone, phone_number]:
        try:
            result = supabase.table('integrations').select(
                'clinic_id, organization_id, config, display_name'
            ).eq('type', 'whatsapp').eq('enabled', True).eq(
                'phone_number', phone_variant
            ).limit(1).execute()

            if result.data and len(result.data) > 0:
                clinic_id = result.data[0]['clinic_id']
                logger.info(
                    f"Found clinic for phone {phone_number}: "
                    f"clinic_id={clinic_id}, "
                    f"organization_id={result.data[0].get('organization_id')}"
                )
                return clinic_id
        except Exception as e:
            logger.warning(f"Error querying integrations for {phone_variant}: {e}")
            continue

    # Not found - this is an error condition
    logger.error(f"No integration found for phone number: {phone_number}")
    raise ValueError(
        f"No WhatsApp integration configured for phone number {phone_number}. "
        f"Please configure in the integrations settings."
    )


async def handle_message_legacy(body: Dict[str, Any]) -> Dict[str, str]:
    """
    Legacy message handling (preserved for rollback).

    This is the existing webhook logic before FSM integration.
    Kept for safe rollback if FSM_ENABLED=false.

    Args:
        body: Webhook payload

    Returns:
        Response dictionary
    """
    # TODO: Implement legacy handling if there was existing logic
    # For now, return a simple acknowledgment
    logger.info("Processing with legacy handler")

    return {
        "response": "Спасибо за сообщение. Наш администратор скоро с вами свяжется.",
        "legacy": True
    }


# ============================================================================
# WhatsApp Webhook Endpoints
# ============================================================================

@router.post("/whatsapp")
async def whatsapp_webhook(request: Request):
    """
    Twilio WhatsApp webhook handler.

    Processes incoming WhatsApp messages via Twilio.
    Routes to FSM processing if FSM_ENABLED=true, otherwise uses legacy handler.
    """
    try:
        body = await request.json()
        logger.info(f"Received WhatsApp webhook: {body}")

        # Extract webhook data (Twilio format)
        message_sid = body.get('MessageSid')
        from_number = body.get('From')  # conversation_id
        message_text = body.get('Body', '')

        if not message_sid or not from_number:
            logger.error("Missing MessageSid or From in webhook payload")
            raise HTTPException(status_code=400, detail="Invalid webhook payload")

        # Route based on feature flag
        if FSM_ENABLED:
            logger.info("Routing to FSM processing")
            result = await process_with_fsm(
                message_sid=message_sid,
                conversation_id=from_number,
                message=message_text,
                context=body
            )
        else:
            logger.info("Routing to legacy processing")
            result = await handle_message_legacy(body)

        return result

    except Exception as e:
        logger.exception(f"Error in WhatsApp webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/evolution")
async def evolution_webhook(request: Request):
    """
    Evolution API WhatsApp webhook handler.

    Processes incoming WhatsApp messages via Evolution API.
    Routes to FSM processing if FSM_ENABLED=true, otherwise uses legacy handler.
    """
    try:
        body = await request.json()
        logger.info(f"Received Evolution webhook: {body}")

        # Extract webhook data (Evolution format)
        message_sid = body.get('key', {}).get('id')
        from_number = body.get('key', {}).get('remoteJid')
        message_text = body.get('message', {}).get('conversation', '')

        # Alternative Evolution format for text messages
        if not message_text:
            message_text = body.get('message', {}).get('extendedTextMessage', {}).get('text', '')

        if not message_sid or not from_number:
            logger.error("Missing key.id or key.remoteJid in webhook payload")
            raise HTTPException(status_code=400, detail="Invalid webhook payload")

        # Route based on feature flag
        if FSM_ENABLED:
            logger.info("Routing to FSM processing")
            result = await process_with_fsm(
                message_sid=message_sid,
                conversation_id=from_number,
                message=message_text,
                context=body
            )
        else:
            logger.info("Routing to legacy processing")
            result = await handle_message_legacy(body)

        return result

    except Exception as e:
        logger.exception(f"Error in Evolution webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Integration Status Endpoints (existing SSE endpoints)
# ============================================================================

@router.get("/integration-status/{integration_id}")
async def integration_status_stream(integration_id: str, request: Request):
    """
    Server-Sent Events endpoint for real-time integration status updates
    """
    async def event_generator():
        # Create a unique connection ID
        connection_id = str(uuid.uuid4())
        queue = asyncio.Queue()
        active_connections[connection_id] = queue
        
        try:
            # Send initial status
            try:
                # Get current integration status
                result = supabase.table('integrations').select('*').eq('id', integration_id).single().execute()
                if result.data:
                    yield f"data: {json.dumps({'type': 'status', 'integration': result.data})}\n\n"
            except:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Integration not found'})}\n\n"
            
            # Keep connection alive and send updates
            while True:
                try:
                    # Wait for updates with timeout
                    message = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(message)}\n\n"
                    
                    # If status is connected, send final update and close
                    if message.get('status') == 'active' or message.get('connected'):
                        yield f"data: {json.dumps({'type': 'complete', 'message': 'Integration connected successfully'})}\n\n"
                        break
                        
                except asyncio.TimeoutError:
                    # Send heartbeat to keep connection alive
                    yield f": heartbeat\n\n"
                    
                except asyncio.CancelledError:
                    break
                    
        finally:
            # Clean up connection
            if connection_id in active_connections:
                del active_connections[connection_id]
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "https://nemo.menu",
            "X-Accel-Buffering": "no"  # Disable nginx buffering
        }
    )

@router.post("/integration-status/update")
async def update_integration_status(data: dict):
    """
    Internal endpoint to push status updates to connected clients
    """
    integration_id = data.get('integration_id')
    status_update = {
        'type': 'update',
        'integration_id': integration_id,
        'status': data.get('status'),
        'connected': data.get('connected'),
        'timestamp': datetime.utcnow().isoformat()
    }
    
    # Send update to all active connections
    for queue in active_connections.values():
        await queue.put(status_update)
    
    return {"success": True, "connections_notified": len(active_connections)}

@router.post("/calendar-connected/{clinic_id}")
async def calendar_connected_webhook(clinic_id: str, background_tasks: BackgroundTasks):
    """
    Webhook called when calendar OAuth is completed successfully
    """
    try:
        # Get all integrations for this clinic
        # Try both possible column names for compatibility
        try:
            result = supabase.table('integrations').select('*').eq(
                'organization_id', clinic_id
            ).eq('type', 'google_calendar').execute()
        except:
            # Fallback to integration_type if type doesn't exist
            result = supabase.table('integrations').select('*').eq(
                'organization_id', clinic_id
            ).eq('integration_type', 'google_calendar').execute()
        
        if result.data:
            for integration in result.data:
                # Update integration status to active
                supabase.table('integrations').update({
                    'status': 'active',
                    'webhook_verified': True,
                    'updated_at': datetime.utcnow().isoformat()
                }).eq('id', integration['id']).execute()
                
                # Notify connected clients
                status_update = {
                    'type': 'calendar_connected',
                    'integration_id': integration['id'],
                    'status': 'active',
                    'connected': True,
                    'clinic_id': clinic_id
                }
                
                for queue in active_connections.values():
                    await queue.put(status_update)
        
        return {"success": True, "message": "Calendar connected successfully"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))