"""WhatsApp webhook handler using existing core schema tables"""

from fastapi import FastAPI, Request, Form, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from twilio.twiml.messaging_response import MessagingResponse
import logging
import os
import requests
from typing import Optional
from supabase import create_client, Client
import json
from datetime import datetime
import asyncio
from pathlib import Path
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
env_path = Path(__file__).resolve().parent.parent.parent / '.env'
if env_path.exists():
    load_dotenv(env_path)
    logger.info(f"Loaded .env from {env_path}")

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Supabase client
supabase_url = os.getenv("SUPABASE_URL", "https://wojtrbcbezpfwksedjmy.supabase.co")
supabase_key = os.getenv("SUPABASE_ANON_KEY")
supabase: Client = create_client(supabase_url, supabase_key) if supabase_key else None

# Get OpenAI API key
openai_api_key = os.getenv("OPENAI_API_KEY")
if openai_api_key:
    openai_api_key = openai_api_key.strip()
    if '\n' in openai_api_key:
        openai_api_key = openai_api_key.split('\n')[0].strip()
    logger.info(f"OpenAI API key loaded (length: {len(openai_api_key)})")
else:
    logger.warning("OpenAI API key not found")

# Store sessions in memory (for demo purposes)
sessions = {}

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.utcnow()}

async def get_clinic_config(clinic_id: str) -> dict:
    """Fetch clinic's WhatsApp configuration from existing tables"""
    try:
        if supabase:
            # Try to fetch from core.organizations first
            org_result = supabase.schema("core").table("organizations").select("*").eq("id", clinic_id).execute()

            if org_result.data and len(org_result.data) > 0:
                org = org_result.data[0]
                # Return config based on organization settings
                return {
                    "clinic_id": clinic_id,
                    "ai_assistant_name": "Julia",
                    "ai_model": "gpt-4o-mini",  # Use a standard model
                    "ai_instructions": """You are Julia, a friendly and helpful dental clinic assistant.
You help patients with appointment scheduling, general dental health questions, and clinic information.
Be professional yet warm and conversational. Keep responses concise and clear.
Our clinic hours are Monday-Friday 9am-6pm, Saturday 10am-2pm.""",
                    "business_hours": {
                        "mon": "09:00-18:00",
                        "tue": "09:00-18:00",
                        "wed": "09:00-18:00",
                        "thu": "09:00-18:00",
                        "fri": "09:00-18:00",
                        "sat": "10:00-14:00",
                        "sun": "closed"
                    }
                }
    except Exception as e:
        logger.error(f"Error fetching clinic config: {e}")

    # Return default config for testing
    return {
        "clinic_id": clinic_id,
        "ai_assistant_name": "Julia",
        "ai_model": "gpt-4o-mini",
        "ai_instructions": """You are Julia, a friendly and helpful dental clinic assistant.
You help patients with appointment scheduling, general dental health questions, and clinic information.
Be professional yet warm and conversational. Keep responses concise and clear.
Our clinic hours are Monday-Friday 9am-6pm, Saturday 10am-2pm.""",
        "business_hours": {
            "mon": "09:00-18:00",
            "tue": "09:00-18:00",
            "wed": "09:00-18:00",
            "thu": "09:00-18:00",
            "fri": "09:00-18:00",
            "sat": "10:00-14:00",
            "sun": "closed"
        }
    }

async def get_or_create_session(clinic_id: str, phone: str):
    """Get or create a conversation session using core.conversation_sessions"""
    session_key = f"{clinic_id}:{phone}"

    if supabase:
        try:
            # Check for existing session
            result = supabase.schema("core").table("conversation_sessions").select("*").eq(
                "organization_id", clinic_id
            ).eq("whatsapp_user_phone", phone).eq("channel_type", "whatsapp").execute()

            if result.data and len(result.data) > 0:
                return result.data[0]

            # Create new session
            new_session = {
                "organization_id": clinic_id,
                "channel_type": "whatsapp",
                "whatsapp_user_phone": phone,
                "conversation_mode": "text",
                "room_name": f"whatsapp_{clinic_id}_{phone}_{datetime.utcnow().timestamp()}",
                "metadata": {"source": "whatsapp"},
                "started_at": datetime.utcnow().isoformat()
            }

            result = supabase.schema("core").table("conversation_sessions").insert(new_session).execute()
            if result.data:
                return result.data[0]
        except Exception as e:
            logger.error(f"Error managing session in database: {e}")

    # Fallback to in-memory session
    if session_key not in sessions:
        sessions[session_key] = {
            "id": session_key,
            "messages": [],
            "context": {},
            "started_at": datetime.utcnow()
        }
    sessions[session_key]["last_activity"] = datetime.utcnow()
    return sessions[session_key]

async def get_ai_response(message: str, phone_number: str, clinic_config: dict) -> tuple[str, int]:
    """Get response from OpenAI using standard chat completions API"""
    try:
        if not openai_api_key:
            return ("I'm currently unavailable. Please call our clinic directly.", 0)

        # Get session
        session = await get_or_create_session(
            clinic_config["clinic_id"],
            phone_number
        )

        # Use standard OpenAI chat completions API
        headers = {
            "Authorization": f"Bearer {openai_api_key}",
            "Content-Type": "application/json"
        }

        messages = [
            {"role": "system", "content": clinic_config.get("ai_instructions", "You are a helpful assistant")},
            {"role": "user", "content": message}
        ]

        data = {
            "model": clinic_config.get("ai_model", "gpt-4o-mini"),
            "messages": messages,
            "max_tokens": 150,
            "temperature": 0.7
        }

        logger.info(f"Calling OpenAI API with model: {data['model']}")

        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=data,
            timeout=30
        )

        if response.status_code == 200:
            response_data = response.json()
            ai_text = response_data['choices'][0]['message']['content'].strip()
            tokens_used = response_data.get('usage', {}).get('total_tokens', 0)

            # Store message in database if available
            if supabase and isinstance(session, dict) and 'id' in session:
                try:
                    # Store inbound message
                    supabase.schema("core").table("whatsapp_messages").insert({
                        "session_id": session['id'],
                        "direction": "inbound",
                        "message_type": "text",
                        "content": message,
                        "organization_id": clinic_config["clinic_id"]
                    }).execute()

                    # Store outbound response
                    supabase.schema("core").table("whatsapp_messages").insert({
                        "session_id": session['id'],
                        "direction": "outbound",
                        "message_type": "text",
                        "content": ai_text,
                        "organization_id": clinic_config["clinic_id"]
                    }).execute()
                except Exception as e:
                    logger.error(f"Failed to store messages: {e}")

            return (ai_text, tokens_used)
        else:
            logger.error(f"OpenAI API error: {response.status_code} - {response.text}")
            return ("I apologize, but I'm having trouble processing your request right now.", 0)

    except Exception as e:
        logger.error(f"Error getting AI response: {e}")
        return ("I apologize, but I'm experiencing technical difficulties.", 0)

@app.post("/webhooks/twilio/whatsapp/{clinic_id}")
async def handle_whatsapp_webhook(
    clinic_id: str,
    request: Request,
    Body: Optional[str] = Form(default=None),
    From: Optional[str] = Form(default=None),
    To: Optional[str] = Form(default=None),
    MessageSid: Optional[str] = Form(default=None),
    AccountSid: Optional[str] = Form(default=None),
    NumMedia: Optional[str] = Form(default=None),
    MediaUrl0: Optional[str] = Form(default=None),
    MediaContentType0: Optional[str] = Form(default=None)
):
    """Handle incoming WhatsApp messages from Twilio"""
    logger.info(f"Webhook called for clinic: {clinic_id}")

    # Log raw request details for debugging
    logger.info(f"Request method: {request.method}")
    logger.info(f"Request headers: {dict(request.headers)}")

    # If form parsing failed, try to get raw body
    if Body is None and From is None:
        try:
            body_bytes = await request.body()
            body_str = body_bytes.decode('utf-8')
            logger.info(f"Raw body: {body_str}")

            # Parse manually if needed
            import urllib.parse
            params = urllib.parse.parse_qs(body_str)
            Body = params.get('Body', [None])[0]
            From = params.get('From', [None])[0]
            logger.info(f"Manually parsed: Body={Body}, From={From}")
        except Exception as e:
            logger.error(f"Error parsing raw body: {e}")

    logger.info(f"Message: Body={Body}, From={From}")

    try:
        # Get clinic configuration
        clinic_config = await get_clinic_config(clinic_id)

        # Extract phone number
        phone_number = From.replace("whatsapp:", "") if From else "unknown"

        # Handle the message
        if Body:
            # Get AI response
            response_text, tokens_used = await get_ai_response(Body, phone_number, clinic_config)

            logger.info(f"Sending response for clinic {clinic_id}: {response_text[:100]}...")

            # Create TwiML response
            resp = MessagingResponse()
            resp.message(response_text)

            return Response(content=str(resp), media_type="application/xml")
        else:
            # Handle media messages
            resp = MessagingResponse()
            if NumMedia and int(NumMedia) > 0:
                resp.message("I received your media file. Please describe what you need help with.")
            else:
                resp.message("I didn't receive any message. How can I help you today?")

            return Response(content=str(resp), media_type="application/xml")

    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        resp = MessagingResponse()
        resp.message("I apologize, but I'm having trouble processing your message. Please try again or call our clinic directly.")
        return Response(content=str(resp), media_type="application/xml")

@app.post("/test/whatsapp")
async def test_endpoint(message: str = Form("Test message")):
    """Test endpoint for debugging"""
    clinic_config = await get_clinic_config("test_clinic")
    response, tokens = await get_ai_response(message, "+1234567890", clinic_config)
    return {
        "message": message,
        "response": response,
        "tokens": tokens,
        "clinic_config": clinic_config
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
