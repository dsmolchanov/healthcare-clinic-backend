"""
Updated Twilio WhatsApp webhook handler using OpenAI's new responses API
"""
from fastapi import FastAPI, Request, Form, Response
from fastapi.middleware.cors import CORSMiddleware
from twilio.twiml.messaging_response import MessagingResponse
import os
import openai
import logging
from typing import Dict, List
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
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
openai_client = None
if os.getenv("OPENAI_API_KEY"):
    openai_client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    logger.info("OpenAI client initialized successfully")
else:
    logger.warning("OPENAI_API_KEY not set")

# In-memory session storage (replace with Redis/database in production)
sessions: Dict[str, List[Dict]] = {}

async def get_ai_response(message: str, phone_number: str) -> str:
    """Get AI response for a message using new responses API"""
    try:
        # Initialize session if new
        if phone_number not in sessions:
            sessions[phone_number] = []

        # System instructions for Julia
        instructions = """You are Julia, a friendly and professional dental clinic assistant.
You help patients with:
- Booking appointments
- Answering questions about dental services
- Providing clinic information (hours, location, contact)
- General dental health inquiries

Keep responses concise and helpful. Be warm but professional.
If asked about specific medical advice, remind them to consult with the dentist directly."""

        # Build conversation context
        context = ""
        for msg in sessions[phone_number][-5:]:  # Keep last 5 exchanges
            if msg["role"] == "user":
                context += f"\nPatient: {msg['content']}"
            elif msg["role"] == "assistant":
                context += f"\nJulia: {msg['content']}"

        # Format input
        if context:
            full_input = f"Previous conversation:{context}\n\nPatient: {message}\n\nJulia:"
        else:
            full_input = f"Patient: {message}\n\nJulia:"

        # Get response from OpenAI using new responses API
        if not openai_client:
            raise Exception("OpenAI client not initialized")

        # Get model from environment or use default
        model = os.getenv("OPENAI_MODEL", "gpt-5-mini")
        logger.info(f"Using model: {model}")

        # Use the new responses API
        response = openai_client.responses.create(
            model=model,
            input=full_input,
            instructions=instructions,
            reasoning={"effort": "low"},  # Low effort for quick responses
            text={"verbosity": "medium"}  # Medium verbosity for clarity
        )

        ai_response = response.output_text

        # Handle empty responses
        if not ai_response or ai_response.strip() == "":
            logger.warning(f"Empty response from {model}, using fallback")
            raise Exception("Empty response from model")

        # Update session
        sessions[phone_number].append({"role": "user", "content": message})
        sessions[phone_number].append({"role": "assistant", "content": ai_response})

        # Keep only last 20 messages
        if len(sessions[phone_number]) > 20:
            sessions[phone_number] = sessions[phone_number][-20:]

        return ai_response

    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        return "I apologize, but I'm having technical difficulties. Please try again or call our clinic directly for assistance."

@app.post("/webhooks/twilio/whatsapp")
async def handle_whatsapp(
    From: str = Form(...),
    Body: str = Form(...),
    To: str = Form(None),
    ProfileName: str = Form(None)
):
    """Handle incoming WhatsApp messages via Twilio"""
    try:
        phone_number = From
        message = Body

        logger.info(f"Received message from {phone_number}: {message[:50]}...")

        # Get AI response
        ai_response = await get_ai_response(message, phone_number)

        # Create Twilio response
        twilio_response = MessagingResponse()
        twilio_response.message(ai_response)

        logger.info(f"Sending response: {ai_response[:50]}...")

        return Response(
            content=str(twilio_response),
            media_type="application/xml"
        )

    except Exception as e:
        logger.error(f"Error processing message: {str(e)}")

        # Fallback response
        twilio_response = MessagingResponse()
        twilio_response.message(
            "I apologize for the inconvenience. Please try again or call us at (555) 123-4567 for immediate assistance."
        )

        return Response(
            content=str(twilio_response),
            media_type="application/xml"
        )

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "whatsapp-handler",
        "model": os.getenv("OPENAI_MODEL", "gpt-5-mini"),
        "sessions_active": len(sessions),
        "timestamp": datetime.utcnow().isoformat()
    }

@app.get("/sessions/clear")
async def clear_sessions():
    """Clear all sessions"""
    sessions.clear()
    return {"message": "All sessions cleared"}

@app.delete("/sessions/{phone_number}")
async def clear_session(phone_number: str):
    """Clear a specific session"""
    if phone_number in sessions:
        del sessions[phone_number]
        return {"message": f"Session cleared for {phone_number}"}
    return {"message": "Session not found"}

@app.get("/webhooks/twilio/whatsapp")
async def verify_webhook(request: Request):
    """Handle Twilio webhook verification"""
    return Response(content="Webhook is ready!", media_type="text/plain")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
