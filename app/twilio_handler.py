"""
Twilio WhatsApp webhook handler
"""
from fastapi import FastAPI, Request, Form, Response
from fastapi.middleware.cors import CORSMiddleware
from twilio.twiml.messaging_response import MessagingResponse
import logging
import os
from openai import OpenAI
from typing import Optional

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize OpenAI client
openai_api_key = os.getenv("OPENAI_API_KEY")
if openai_api_key:
    openai_client = OpenAI(api_key=openai_api_key)
else:
    logger.warning("OPENAI_API_KEY not set, will use fallback responses")
    openai_client = None

# Store conversation sessions
sessions = {}

async def get_ai_response(message: str, phone_number: str) -> str:
    """Get response from OpenAI"""
    try:
        # Get conversation history
        if phone_number not in sessions:
            sessions[phone_number] = []

        sessions[phone_number].append({"role": "user", "content": message})

        # Create messages for OpenAI
        messages = [
            {"role": "system", "content": """You are Julia, a friendly healthcare assistant for Bright Smile Dental Clinic.
You help patients with:
- Booking appointments
- Rescheduling or canceling appointments
- Providing clinic information
- Answering general questions

Be warm, professional, and helpful. Keep responses concise for WhatsApp.

Clinic Info:
📍 123 Main St, New York, NY 10001
📞 +1-212-555-0100
🕐 Mon-Thu: 9AM-6PM, Fri: 9AM-5PM, Sat: 10AM-2PM"""},
        ] + sessions[phone_number][-10:]  # Keep last 10 messages

        # Get response from OpenAI
        if not openai_client:
            raise Exception("OpenAI client not initialized")

        # Get model from environment or use default
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        logger.info(f"Using model: {model}")

        # Create chat completion
        response = openai_client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.7,
            max_tokens=150
        )

        ai_response = response.choices[0].message.content

        # Handle empty responses
        if not ai_response or ai_response.strip() == "":
            logger.warning(f"Empty response from {model}, using fallback")
            raise Exception("Empty response from model")

        sessions[phone_number].append({"role": "assistant", "content": ai_response})

        return ai_response
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        # Fallback to simple responses
        if "appointment" in message.lower():
            return "I can help you book an appointment! Our available times this week are:\n📅 Tomorrow - 10:00 AM, 2:00 PM\n📅 Friday - 9:00 AM, 11:00 AM, 3:00 PM\n\nWhich time works best for you?"
        elif "cancel" in message.lower() or "reschedule" in message.lower():
            return "To cancel or reschedule, please provide your appointment details or call us at +1-212-555-0100."
        elif "hours" in message.lower() or "open" in message.lower():
            return "🕐 Our hours are:\nMon-Thu: 9AM-6PM\nFri: 9AM-5PM\nSat: 10AM-2PM\nSun: Closed"
        else:
            return "I'm Julia from Bright Smile Dental! I can help with appointments, clinic info, or questions. What do you need today?"

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy", "service": "twilio-whatsapp-handler"}

@app.post("/webhooks/twilio/whatsapp")
async def handle_whatsapp(
    Body: str = Form(None),
    From: str = Form(None),
    To: Optional[str] = Form(None),
    MessageSid: Optional[str] = Form(None)
):
    """Handle incoming WhatsApp messages from Twilio"""
    try:
        # Log all received data for debugging
        logger.info(f"Received webhook - Body: {Body}, From: {From}, To: {To}, MessageSid: {MessageSid}")

        # Check if we have required fields
        if not Body or not From:
            logger.warning("Missing required fields in Twilio webhook")
            response = MessagingResponse()
            response.message("Hello! I'm Julia from Bright Smile Dental. How can I help you today?")
            return Response(content=str(response), media_type="application/xml")

        # Clean phone number
        from_number = From.replace("whatsapp:", "")

        logger.info(f"Received message from {from_number}: {Body}")

        # Get AI response
        response_text = await get_ai_response(Body, from_number)

        # Create TwiML response
        response = MessagingResponse()
        response.message(response_text)

        logger.info(f"Sending response: {response_text}")

        return Response(content=str(response), media_type="application/xml")

    except Exception as e:
        logger.error(f"Error handling message: {e}")
        response = MessagingResponse()
        response.message("Sorry, I'm having trouble right now. Please try again or call us at +1-212-555-0100.")
        return Response(content=str(response), media_type="application/xml")

@app.get("/webhooks/twilio/whatsapp")
async def verify_webhook(request: Request):
    """Handle Twilio webhook verification"""
    # Twilio doesn't require verification like Meta does, but handle GET requests
    return Response(content="Webhook is ready!", media_type="text/plain")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
