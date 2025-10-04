"""
Twilio WhatsApp webhook handler using OpenAI's new responses API (gpt-5)
"""
from fastapi import FastAPI, Request, Form, Response
from fastapi.middleware.cors import CORSMiddleware
from twilio.twiml.messaging_response import MessagingResponse
import os
import openai
import logging
from typing import Dict, List, Optional
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI()

# Add CORS middleware for flexibility
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize OpenAI client
openai_client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# In-memory session storage (replace with Redis/database in production)
sessions: Dict[str, List[Dict]] = {}

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

        # Get conversation history
        if phone_number not in sessions:
            sessions[phone_number] = []

        # Get model from environment or use default
        model = os.getenv("OPENAI_MODEL", "gpt-5-mini")

        # Use the exact model name from .env

        logger.info(f"Using model: {model}")

        # Build input for new API format
        # Option 1: Using instructions parameter (simpler)
        instructions = """You are Julia, a friendly healthcare assistant for Bright Smile Dental Clinic.
You help patients with:
- Booking appointments
- Rescheduling or canceling appointments
- Providing clinic information
- Answering general questions

Be warm, professional, and helpful. Keep responses concise for WhatsApp.

Clinic Info:
📍 123 Main St, New York, NY 10001
📞 +1-212-555-0100
🕐 Mon-Thu: 9AM-6PM, Fri: 9AM-5PM, Sat: 10AM-2PM"""

        # Build conversation context
        context = ""
        for msg in sessions[phone_number][-5:]:  # Keep last 5 exchanges
            if msg["role"] == "user":
                context += f"\nPatient: {msg['content']}"
            elif msg["role"] == "assistant":
                context += f"\nJulia: {msg['content']}"

        # Add current message to context
        if context:
            full_input = f"Previous conversation:{context}\n\nPatient: {message}\n\nJulia:"
        else:
            full_input = f"Patient: {message}\n\nJulia:"

        # Use the new responses API
        response = openai_client.responses.create(
            model=model,
            input=full_input,
            instructions=instructions,
            reasoning={"effort": "low"},  # Low effort for quick WhatsApp responses
            text={"verbosity": "low"}  # Keep responses concise
        )

        # Extract text from response
        ai_response = response.output_text

        # Handle empty responses
        if not ai_response or ai_response.strip() == "":
            logger.warning(f"Empty response from {model}, using fallback")
            raise Exception("Empty response from model")

        # Update session history
        sessions[phone_number].append({"role": "user", "content": message})
        sessions[phone_number].append({"role": "assistant", "content": ai_response})

        # Keep only last 20 messages in session
        if len(sessions[phone_number]) > 20:
            sessions[phone_number] = sessions[phone_number][-20:]

        # Create Twilio response
        twilio_response = MessagingResponse()
        twilio_response.message(ai_response)

        # Log the interaction
        logger.info(f"✅ Processed message from {phone_number}")
        logger.info(f"User: {message[:100]}...")
        logger.info(f"Julia: {ai_response[:100]}...")

        return Response(
            content=str(twilio_response),
            media_type="application/xml"
        )

    except Exception as e:
        logger.error(f"❌ Error processing message: {str(e)}")

        # Fallback response - keep it simple
        fallback_messages = [
            "I apologize, I'm having technical difficulties. Please try again in a moment.",
            "Sorry, I couldn't process that request. Please try rephrasing or call us at +1-212-555-0100.",
            "I'm experiencing some issues right now. For urgent matters, please call our clinic directly."
        ]

        import random
        fallback = random.choice(fallback_messages)

        twilio_response = MessagingResponse()
        twilio_response.message(fallback)

        return Response(
            content=str(twilio_response),
            media_type="application/xml"
        )

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    model = os.getenv("OPENAI_MODEL", "gpt-5-mini")
    return {
        "status": "healthy",
        "service": "whatsapp-handler",
        "model": model,
        "sessions_active": len(sessions),
        "timestamp": datetime.utcnow().isoformat()
    }

@app.get("/sessions/count")
async def session_count():
    """Get active session count"""
    return {"active_sessions": len(sessions)}

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
    # Twilio doesn't require verification like Meta does, but handle GET requests
    return Response(content="Webhook is ready for gpt-5!", media_type="text/plain")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
