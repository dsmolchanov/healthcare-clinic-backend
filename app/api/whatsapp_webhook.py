"""
WhatsApp Webhook Handler

Receives messages from Evolution API and processes them with AI and RAG
"""

from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from typing import Dict, Any
import os
# Removed: from openai import AsyncOpenAI  # Now using LLM factory
import aiohttp
import json
import asyncio
from app.api.multilingual_message_processor import MessageRequest, MultilingualMessageProcessor

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# LLM factory will be initialized lazily per request
_llm_factory = None

async def get_llm_factory():
    """Get or create LLM factory"""
    global _llm_factory
    if _llm_factory is None:
        from app.services.llm.llm_factory import LLMFactory
        from app.db import get_supabase_client

        supabase = get_supabase_client()
        _llm_factory = LLMFactory(supabase)

    return _llm_factory

# Evolution API URL
EVOLUTION_API_URL = os.getenv("EVOLUTION_SERVER_URL", "https://evolution-api-prod.fly.dev")

# Initialize message processor with RAG
message_processor = MultilingualMessageProcessor()

@router.post("/whatsapp")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    """Receive WhatsApp messages from Evolution API and respond with AI and RAG"""
    print("[Webhook] Received request")

    try:
        # Read body BEFORE returning (to avoid stream consumed error)
        body_bytes = await request.body()
        print(f"[Webhook] Read {len(body_bytes)} bytes from request body")

        # Parse JSON immediately to validate
        try:
            data = json.loads(body_bytes.decode('utf-8'))
            print(f"[Webhook] Parsed JSON successfully")
        except json.JSONDecodeError as e:
            print(f"[Webhook] Invalid JSON: {e}")
            return {"status": "error", "message": "Invalid JSON"}

        # Use FastAPI's BackgroundTasks for proper background processing
        background_tasks.add_task(process_webhook_background, body_bytes)
        print(f"[Webhook] Added task to BackgroundTasks")

    except Exception as e:
        print(f"[Webhook] Error reading request body: {e}")
        import traceback
        traceback.print_exc()

    # Return immediately
    print("[Webhook] Returning immediate response")
    return {"status": "accepted", "message": "Processing"}


async def process_webhook_background(body_bytes: bytes):
    """Process webhook in background"""
    print(f"[Background] ==================== STARTING BACKGROUND PROCESSING ====================")
    print(f"[Background] Received {len(body_bytes)} bytes")

    try:
        # Parse JSON from bytes
        try:
            data = json.loads(body_bytes.decode('utf-8'))
            print(f"[Background] Successfully parsed JSON")
        except json.JSONDecodeError as e:
            print(f"[Background] Failed to parse JSON: {e}")
            print(f"[Background] Raw data: {body_bytes[:500]}")  # First 500 bytes for debugging
            return

        print(f"[Background] Webhook data keys: {list(data.keys())}")

        instance_name = data.get("instanceName")
        message_data = data.get("message", {})

        print(f"[Background] Instance: {instance_name}")
        print(f"[Background] Message data keys: {list(message_data.keys())}")

        # Evolution API sends messages in a nested format
        # Extract the actual message content from the nested structure
        key = message_data.get("key", {})
        from_number = key.get("remoteJid", "").replace("@s.whatsapp.net", "")
        is_from_me = key.get("fromMe", False)

        # Skip our own messages
        if is_from_me:
            return {"status": "ignored", "reason": "Own message"}

        # Extract push name (sender's name)
        push_name = message_data.get("pushName", "WhatsApp User")

        # Extract text from nested message structure
        text = ""
        nested_message = message_data.get("message", {})
        if nested_message:
            # Check various message formats Evolution API might send
            text = (
                nested_message.get("conversation") or
                nested_message.get("extendedTextMessage", {}).get("text") or
                nested_message.get("imageMessage", {}).get("caption") or
                nested_message.get("videoMessage", {}).get("caption") or
                ""
            )

        # Also check if text is directly in message_data (for compatibility)
        if not text:
            text = message_data.get("text", "")

        # Also extract from direct if not found (legacy format)
        if not from_number:
            from_number = message_data.get("from", "")

        if not text or not from_number:
            print(f"Ignoring message - no text or sender. Text: '{text}', From: '{from_number}'")
            return {"status": "ignored", "reason": "No text or sender"}

        # Extract clinic ID from instance name (format: clinic-{uuid}-{timestamp})
        # UUID format: 8-4-4-4-12 characters
        clinic_id = "3e411ecb-3411-4add-91e2-8fa897310cb0"  # Default to Shtern Dental
        if instance_name and instance_name.startswith("clinic-"):
            parts = instance_name.split("-")
            if len(parts) >= 6:  # clinic + 5 UUID parts + timestamp
                # Reconstruct full UUID (parts 1-5)
                clinic_id = "-".join(parts[1:6])  # Full UUID: 8-4-4-4-12
                print(f"Extracted clinic ID from instance name: {clinic_id}")

        print(f"Using clinic ID for RAG: {clinic_id}")

        # Process message
        print(f"[Background] Processing message from {from_number}: '{text}'")

        # Get AI response using RAG-enabled processor
        print(f"[Background] Getting AI response...")
        ai_response = await get_ai_response_with_rag(text, from_number, clinic_id, push_name)
        print(f"[Background] AI response: {ai_response[:200]}...")

        # Send response back via Evolution API
        print(f"[Background] Sending WhatsApp message...")
        await send_whatsapp_message(instance_name, from_number, ai_response)
        print(f"[Background] ✅ Successfully sent response to {from_number}")

    except Exception as e:
        print(f"[Background] ❌ Error processing message: {e}")
        import traceback
        print(f"[Background] Full error: {traceback.format_exc()}")

async def get_ai_response_with_rag(user_message: str, from_number: str, clinic_id: str, user_name: str) -> str:
    """Generate AI response using RAG-enabled multilingual processor"""
    try:
        # Create message request for RAG processor
        message_request = MessageRequest(
            from_phone=from_number,
            to_phone="+14155238886",  # WhatsApp Business number
            body=user_message,
            message_sid=f"whatsapp_{from_number}_{os.urandom(8).hex()}",
            clinic_id=clinic_id,
            clinic_name="Shtern Dental Clinic",
            channel="whatsapp",
            profile_name=user_name,
            metadata={}
        )

        # Process with RAG
        response = await message_processor.process_message(message_request)
        return response.message

    except Exception as e:
        print(f"Error with RAG processor, falling back to basic AI: {e}")
        # Fallback to basic AI response without RAG
        return await get_ai_response(user_message, from_number)

async def get_ai_response(user_message: str, from_number: str) -> str:
    """Generate AI response for WhatsApp message with multilingual support"""
    try:
        # System prompt for clinic assistant
        system_prompt = """You are a helpful AI assistant for Shtern Dental Clinic, a modern dental practice in New York.

        IMPORTANT: Respond in the same language as the user's message. If they write in Russian, respond in Russian. If in English, respond in English.

        Services and Pricing (USD):
        - Simple filling: $150-$300
        - Root canal: $800-$1,500
        - Dental cleaning: $100-$200
        - Teeth whitening: $400-$600
        - Crown: $1,000-$2,000
        - Extraction: $150-$400
        - Initial consultation: $75

        Clinic Information:
        - Location: 123 Main Street, New York, NY 10001
        - Phone: +1-234-567-8900
        - Office hours: Monday-Friday 9AM-6PM, Saturday 10AM-2PM
        - Emergency service available 24/7
        - We accept most insurance plans

        You help patients with:
        - Scheduling appointments (direct them to call the number)
        - Providing pricing information (give ranges as above)
        - Answering questions about procedures
        - Emergency dental advice

        Be concise, friendly, and professional. Always offer to help schedule an appointment."""

        # Get AI response using LLM factory
        factory = await get_llm_factory()
        response = await factory.generate(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            max_tokens=500,
            temperature=0.7
        )

        return response.content
    except Exception as e:
        print(f"Error getting AI response: {e}")
        return "I apologize, but I'm having trouble processing your message. Please try again or call our clinic directly at +1-234-567-8900."

async def send_whatsapp_message(instance_name: str, to_number: str, text: str) -> None:
    """Send WhatsApp message via Evolution API"""
    print(f"[SendMessage] ==================== SENDING WHATSAPP MESSAGE ====================")
    print(f"[SendMessage] Instance: {instance_name}")
    print(f"[SendMessage] To: {to_number}")
    print(f"[SendMessage] Text: {text[:100]}...")

    try:
        # Remove @s.whatsapp.net if present, we'll add it back
        clean_number = to_number.replace("@s.whatsapp.net", "")
        print(f"[SendMessage] Clean number: {clean_number}")

        async with aiohttp.ClientSession() as session:
            url = f"{EVOLUTION_API_URL}/message/sendText/{instance_name}"
            payload = {
                "number": clean_number,
                "text": text
            }

            print(f"[SendMessage] URL: {url}")
            print(f"[SendMessage] Payload: {json.dumps(payload, indent=2)}")

            async with session.post(url, json=payload) as response:
                result_text = await response.text()
                print(f"[SendMessage] Response status: {response.status}")
                print(f"[SendMessage] Response body: {result_text}")

                if response.status != 200 and response.status != 201:
                    raise Exception(f"Failed to send message: HTTP {response.status} - {result_text}")

                print(f"[SendMessage] ✅ Message sent successfully!")
    except Exception as e:
        print(f"[SendMessage] ❌ Error sending WhatsApp message: {e}")
        raise

@router.get("/whatsapp/test")
async def test_webhook():
    """Test endpoint to verify webhook is working"""
    return {"status": "ok", "webhook": "WhatsApp webhook is active"}
