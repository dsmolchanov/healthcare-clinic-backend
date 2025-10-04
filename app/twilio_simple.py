"""Simple WhatsApp webhook handler without Form parsing"""

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from twilio.twiml.messaging_response import MessagingResponse
import logging
import os
import requests
import urllib.parse
from pathlib import Path
from dotenv import load_dotenv
from app.api.quick_onboarding_improved import router as onboarding_router

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
env_path = Path(__file__).resolve().parent.parent.parent / '.env'
if env_path.exists():
    load_dotenv(env_path)
    logger.info(f"Loaded .env from {env_path}")
else:
    logger.info("Running in production mode - using environment variables")

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount onboarding routes
app.include_router(onboarding_router)

# Get OpenAI API key - try multiple ways
openai_api_key = os.environ.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
if openai_api_key:
    openai_api_key = openai_api_key.strip()
    if '\n' in openai_api_key:
        openai_api_key = openai_api_key.split('\n')[0].strip()
    logger.info(f"OpenAI API key loaded (length: {len(openai_api_key)})")
else:
    # Try to debug what environment variables are available
    logger.warning("OpenAI API key not found")
    logger.info(f"Available env vars: {list(os.environ.keys())}")

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    # Re-check environment variable at runtime
    current_key = os.environ.get("OPENAI_API_KEY")
    return {
        "status": "healthy",
        "openai_configured": bool(openai_api_key),
        "openai_key_length": len(openai_api_key) if openai_api_key else 0,
        "current_env_key_exists": bool(current_key),
        "current_env_key_length": len(current_key) if current_key else 0,
        "env_vars_count": len(os.environ),
        "has_fly_vars": "FLY_APP_NAME" in os.environ
    }

async def get_ai_response(message: str) -> str:
    """Get response from OpenAI"""
    try:
        # Get the API key at runtime (in case it wasn't available at startup)
        api_key = openai_api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            logger.error("No OpenAI API key available")
            return "I'm currently unavailable. Please call our clinic directly."

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        messages = [
            {
                "role": "system",
                "content": """You are Julia, a friendly and helpful dental clinic assistant.
You help patients with appointment scheduling, general dental health questions, and clinic information.
Be professional yet warm and conversational. Keep responses concise and clear.
Our clinic hours are Monday-Friday 9am-6pm, Saturday 10am-2pm."""
            },
            {"role": "user", "content": message}
        ]

        data = {
            "model": "gpt-4o-mini",
            "messages": messages,
            "max_tokens": 150,
            "temperature": 0.7
        }

        logger.info(f"Calling OpenAI API...")

        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=data,
            timeout=30
        )

        if response.status_code == 200:
            response_data = response.json()
            ai_text = response_data['choices'][0]['message']['content'].strip()
            logger.info(f"AI response: {ai_text[:100]}...")
            return ai_text
        else:
            logger.error(f"OpenAI API error: {response.status_code} - {response.text}")
            if response.status_code == 401:
                logger.error("Authentication failed - check API key")
            return "I apologize, but I'm having trouble processing your request right now."

    except Exception as e:
        logger.error(f"Error getting AI response: {e}")
        return "I apologize, but I'm experiencing technical difficulties."

@app.post("/webhooks/twilio/whatsapp/{clinic_id}")
async def handle_whatsapp_webhook(clinic_id: str, request: Request):
    """Handle incoming WhatsApp messages from Twilio - raw body parsing"""
    logger.info(f"Webhook called for clinic: {clinic_id}")
    logger.info(f"Headers: {dict(request.headers)}")

    try:
        # Get raw body
        body_bytes = await request.body()
        body_str = body_bytes.decode('utf-8')
        logger.info(f"Raw body: {body_str[:200]}...")

        # Parse the URL-encoded form data
        params = urllib.parse.parse_qs(body_str)

        # Extract fields
        body_text = params.get('Body', [None])[0]
        from_number = params.get('From', [None])[0]
        message_sid = params.get('MessageSid', [None])[0]

        logger.info(f"Parsed - Body: {body_text}, From: {from_number}, MessageSid: {message_sid}")

        # Create response
        resp = MessagingResponse()

        if body_text:
            # Get AI response
            ai_response = await get_ai_response(body_text)
            resp.message(ai_response)
        else:
            resp.message("I didn't receive any message. How can I help you today?")

        # Return TwiML response
        response_xml = str(resp)
        logger.info(f"Returning TwiML: {response_xml[:200]}...")

        return Response(content=response_xml, media_type="application/xml")

    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        resp = MessagingResponse()
        resp.message("I apologize, but I'm having trouble processing your message. Please try again or call our clinic directly.")
        return Response(content=str(resp), media_type="application/xml")

@app.get("/")
async def root():
    """Root endpoint"""
    return {"status": "WhatsApp webhook server running", "clinic_endpoint": "/webhooks/twilio/whatsapp/{clinic_id}"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
