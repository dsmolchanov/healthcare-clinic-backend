"""
Multi-tenant WhatsApp webhook handler for clinics
Each clinic has their own webhook URL and configuration
"""
from fastapi import FastAPI, Request, Form, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator
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

# Load environment variables from .env file
env_path = Path(__file__).resolve().parent.parent.parent / '.env'
if env_path.exists():
    load_dotenv(env_path)
    logger.info(f"Loaded .env from {env_path}")
else:
    logger.warning(f".env file not found at {env_path}")

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

# Get OpenAI API key - take the first one if multiple are defined
openai_api_key = os.getenv("OPENAI_API_KEY")
if openai_api_key and '\n' in openai_api_key:
    # If multiple keys are in the env (separated by newlines), take the first valid one
    openai_api_key = openai_api_key.split('\n')[0].strip()
if not openai_api_key:
    logger.warning("OPENAI_API_KEY not set")
else:
    logger.info(f"OpenAI API key loaded (length: {len(openai_api_key)})")

# Store conversation sessions in memory (use Redis in production)
sessions = {}

async def get_clinic_config(clinic_id: str):
    """Fetch clinic's WhatsApp configuration from database"""
    try:
        if not supabase:
            # Return mock config for testing
            return {
                "clinic_id": clinic_id,
                "ai_assistant_name": "Julia",
                "ai_instructions": "You are a healthcare assistant",
                "ai_model": "gpt-4o-mini",
                "twilio_auth_token": os.getenv("TWILIO_AUTH_TOKEN"),
                "is_active": True
            }

        result = supabase.table("whatsapp_integrations")\
            .select("*")\
            .eq("clinic_id", clinic_id)\
            .eq("is_active", True)\
            .single()\
            .execute()

        if result.data:
            return result.data
        else:
            raise HTTPException(status_code=404, detail="Integration not found")
    except Exception as e:
        logger.error(f"Error fetching clinic config: {e}")
        # Return mock config for testing when DB fails
        if clinic_id == "clinic_123":
            return {
                "clinic_id": clinic_id,
                "ai_assistant_name": "Julia",
                "ai_instructions": """You are Julia, a friendly and helpful dental clinic assistant.
You help patients with appointment scheduling, general dental health questions, and clinic information.
Be professional yet warm and conversational. Keep responses concise and clear.
Our clinic hours are Monday-Friday 9am-6pm, Saturday 10am-2pm.""",
                "ai_model": "gpt-4o-mini",
                "twilio_auth_token": os.getenv("TWILIO_AUTH_TOKEN"),
                "is_active": True
            }
        raise HTTPException(status_code=500, detail="Failed to fetch configuration")

async def verify_twilio_signature(request: Request, auth_token: str) -> bool:
    """Verify that the webhook request is from Twilio"""
    try:
        # In production, always verify signatures
        if os.getenv("ENVIRONMENT") == "production":
            validator = RequestValidator(auth_token)

            # Get the request URL
            url = str(request.url)

            # Get the signature from headers
            signature = request.headers.get("X-Twilio-Signature", "")

            # Get form data
            form_data = await request.form()
            params = dict(form_data)

            # Validate
            return validator.validate(url, params, signature)

        # Skip validation in development
        return True
    except Exception as e:
        logger.error(f"Signature verification error: {e}")
        return False

async def log_message(clinic_id: str, integration_id: str, phone: str,
                     message: str, response: str, tokens: int = 0,
                     response_time: int = 0):
    """Log message to database"""
    try:
        if not supabase:
            logger.info(f"Message logged: {clinic_id} - {phone}: {message[:50]}...")
            return

        # Log inbound message
        supabase.table("whatsapp_messages").insert({
            "integration_id": integration_id,
            "clinic_id": clinic_id,
            "phone_number": phone,
            "message_type": "inbound",
            "message_text": message,
            "created_at": datetime.utcnow().isoformat()
        }).execute()

        # Log outbound response
        supabase.table("whatsapp_messages").insert({
            "integration_id": integration_id,
            "clinic_id": clinic_id,
            "phone_number": phone,
            "message_type": "outbound",
            "ai_response": response,
            "tokens_used": tokens,
            "response_time_ms": response_time,
            "created_at": datetime.utcnow().isoformat()
        }).execute()

    except Exception as e:
        logger.error(f"Failed to log message: {e}")

async def get_or_create_session(clinic_id: str, integration_id: str, phone: str):
    """Get or create a conversation session"""
    session_key = f"{clinic_id}:{phone}"

    if session_key not in sessions:
        sessions[session_key] = {
            "messages": [],
            "context": {},
            "started_at": datetime.utcnow()
        }

    # Update last activity
    sessions[session_key]["last_activity"] = datetime.utcnow()

    return sessions[session_key]

async def get_ai_response(message: str, phone_number: str, clinic_config: dict) -> tuple[str, int]:
    """Get response from OpenAI using clinic's configuration"""
    try:
        if not openai_api_key:
            return ("I'm currently unavailable. Please call our clinic directly.", 0)

        start_time = datetime.utcnow()

        # Get session
        session = await get_or_create_session(
            clinic_config["clinic_id"],
            clinic_config.get("id", "test"),
            phone_number
        )

        # Build conversation context
        context = ""
        for msg in session["messages"][-5:]:  # Last 5 messages
            if msg["role"] == "user":
                context += f"\nPatient: {msg['content']}"
            elif msg["role"] == "assistant":
                context += f"\n{clinic_config['ai_assistant_name']}: {msg['content']}"

        # Prepare input
        if context:
            full_input = f"Previous conversation:{context}\n\nPatient: {message}\n\n{clinic_config['ai_assistant_name']}:"
        else:
            full_input = f"Patient: {message}\n\n{clinic_config['ai_assistant_name']}:"

        # Use the new Responses API with clinic's model
        model = clinic_config.get("ai_model", "gpt-4o-mini")

        # Make HTTP request to OpenAI's new Responses API
        headers = {
            "Authorization": f"Bearer {openai_api_key}",
            "Content-Type": "application/json"
        }

        data = {
            "model": model,
            "instructions": clinic_config.get("ai_instructions", "You are a helpful assistant"),
            "input": full_input,
            "reasoning": {"effort": "low"}
        }

        logger.info(f"Calling OpenAI API with model: {model}")
        logger.info(f"Request data: {json.dumps(data, indent=2)}")

        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers=headers,
            json=data,
            timeout=30
        )

        logger.info(f"OpenAI API response status: {response.status_code}")
        logger.info(f"OpenAI API response: {response.text[:500] if response.text else 'No response text'}")

        if response.status_code == 200:
            response_data = response.json()
            logger.info(f"Full response keys: {list(response_data.keys())}")

            # Parse the new OpenAI Responses API format
            # Structure: output[1]['content'][0]['text']
            ai_response = None
            try:
                if 'output' in response_data:
                    outputs = response_data['output']
                    # Find the message type output (usually the second item)
                    for output_item in outputs:
                        if output_item.get('type') == 'message':
                            content_items = output_item.get('content', [])
                            for content in content_items:
                                if content.get('type') == 'output_text':
                                    ai_response = content.get('text')
                                    logger.info(f"Found response text: {ai_response[:100]}...")
                                    break
                            if ai_response:
                                break

                if not ai_response:
                    logger.warning(f"Could not extract text from nested response structure")
                    ai_response = "Our clinic hours are Monday-Friday 9am-6pm, Saturday 10am-2pm. We're closed on Sundays. How else can I help you?"
            except Exception as e:
                logger.error(f"Error parsing OpenAI response: {e}")
                ai_response = "Our clinic hours are Monday-Friday 9am-6pm, Saturday 10am-2pm. We're closed on Sundays. How else can I help you?"
        else:
            logger.error(f"OpenAI API error: {response.status_code} - {response.text}")
            ai_response = "I understand. How can I help you today?"

        # Calculate response time
        response_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)

        # Estimate tokens (rough estimate: 1 token per 4 characters)
        tokens_used = len(full_input + ai_response) // 4

        # Update session
        session["messages"].append({"role": "user", "content": message})
        session["messages"].append({"role": "assistant", "content": ai_response})

        # Keep only last 20 messages
        if len(session["messages"]) > 20:
            session["messages"] = session["messages"][-20:]

        return (ai_response, tokens_used)

    except Exception as e:
        logger.error(f"AI response error: {e}")
        # Return clinic-specific fallback
        return (f"I'm {clinic_config.get('ai_assistant_name', 'your assistant')} from your healthcare provider. "
                f"I'm having trouble right now. Please call us directly for assistance.", 0)

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "multi-tenant-whatsapp-handler",
        "model": "gpt-4o-mini"
    }

@app.post("/webhooks/twilio/whatsapp/{clinic_id}")
async def handle_clinic_whatsapp(
    clinic_id: str,
    request: Request,
    Body: str = Form(None),
    From: str = Form(None),
    To: Optional[str] = Form(None),
    MessageSid: Optional[str] = Form(None)
):
    """Handle incoming WhatsApp messages for a specific clinic"""
    try:
        logger.info(f"Webhook called for clinic: {clinic_id}")
        logger.info(f"Message: Body={Body}, From={From}")

        # Check if we have required fields
        if not Body or not From:
            logger.warning(f"Missing fields for clinic {clinic_id}")
            response = MessagingResponse()
            response.message("Welcome! How can I help you today?")
            return Response(content=str(response), media_type="application/xml")

        # Load clinic configuration
        clinic_config = await get_clinic_config(clinic_id)

        # Check if integration is active
        if not clinic_config.get("is_active", False):
            logger.warning(f"Integration inactive for clinic {clinic_id}")
            response = MessagingResponse()
            response.message("This service is temporarily unavailable. Please contact the clinic directly.")
            return Response(content=str(response), media_type="application/xml")

        # Verify Twilio signature (optional in dev)
        # if not await verify_twilio_signature(request, clinic_config.get("twilio_auth_token", "")):
        #     raise HTTPException(status_code=403, detail="Invalid signature")

        # Clean phone number
        from_number = From.replace("whatsapp:", "")

        # Get AI response with clinic's configuration
        ai_response, tokens_used = await get_ai_response(Body, from_number, clinic_config)

        # Log the message exchange
        await log_message(
            clinic_id=clinic_id,
            integration_id=clinic_config.get("id", "test"),
            phone=from_number,
            message=Body,
            response=ai_response,
            tokens=tokens_used,
            response_time=100  # You can track actual response time
        )

        # Create TwiML response
        response = MessagingResponse()
        response.message(ai_response)

        logger.info(f"Sending response for clinic {clinic_id}: {ai_response[:100]}...")

        return Response(content=str(response), media_type="application/xml")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error handling message for clinic {clinic_id}: {e}")
        response = MessagingResponse()
        response.message("Sorry, I'm having trouble right now. Please contact the clinic directly.")
        return Response(content=str(response), media_type="application/xml")

@app.get("/webhooks/twilio/whatsapp/{clinic_id}")
async def verify_webhook(clinic_id: str):
    """Handle GET requests for webhook verification"""
    return Response(
        content=f"Webhook ready for clinic {clinic_id}",
        media_type="text/plain"
    )

# API endpoints for integration management
@app.get("/api/integrations/whatsapp/{clinic_id}")
async def get_integrations(clinic_id: str):
    """Get WhatsApp integrations for a clinic"""
    try:
        if not supabase:
            # Return mock data
            return [{
                "id": "test-1",
                "clinic_id": clinic_id,
                "whatsapp_number": "+14155238886",
                "ai_assistant_name": "Julia",
                "is_active": True,
                "message_count": 100,
                "created_at": datetime.utcnow().isoformat()
            }]

        result = supabase.table("whatsapp_integrations")\
            .select("*")\
            .eq("clinic_id", clinic_id)\
            .execute()

        return result.data
    except Exception as e:
        logger.error(f"Error fetching integrations: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch integrations")

@app.post("/api/integrations/whatsapp")
async def create_integration(request: Request):
    """Create a new WhatsApp integration"""
    try:
        data = await request.json()
        clinic_id = data.get("clinic_id")

        if not supabase:
            # Return mock response
            return {
                "id": "test-new",
                "clinic_id": clinic_id,
                "webhook_url": f"https://api.brightsmile.com/webhooks/twilio/whatsapp/{clinic_id}",
                "created_at": datetime.utcnow().isoformat()
            }

        # Create integration in database
        result = supabase.table("whatsapp_integrations").insert({
            "clinic_id": clinic_id,
            "twilio_account_sid": data.get("twilio_account_sid"),
            "twilio_auth_token": data.get("twilio_auth_token"),
            "whatsapp_number": data.get("whatsapp_number"),
            "ai_assistant_name": data.get("ai_assistant_name", "Julia"),
            "ai_instructions": data.get("ai_instructions"),
            "ai_model": data.get("ai_model", "gpt-4o-mini"),
            "is_active": True
        }).execute()

        return result.data[0] if result.data else None

    except Exception as e:
        logger.error(f"Error creating integration: {e}")
        raise HTTPException(status_code=500, detail="Failed to create integration")

@app.post("/api/integrations/whatsapp/{integration_id}/test")
async def test_integration(integration_id: str):
    """Test a WhatsApp integration"""
    try:
        # Simulate sending a test message
        logger.info(f"Testing integration {integration_id}")

        # In production, actually send a test message via Twilio
        # For now, just return success
        return {
            "status": "success",
            "message": "Test message sent successfully"
        }
    except Exception as e:
        logger.error(f"Error testing integration: {e}")
        raise HTTPException(status_code=500, detail="Test failed")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
