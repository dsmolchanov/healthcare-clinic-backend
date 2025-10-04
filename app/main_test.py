"""
Main application for dental clinic backend (test version)
"""

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
import logging
import os
from typing import Dict, Any

# Import our modules
from .middleware import configure_rate_limiting
from .security import verify_twilio_signature
from .whatsapp import handle_whatsapp_webhook
from .database import db

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events"""
    # Startup
    logger.info("Starting up Dental Clinic Backend...")
    yield
    # Shutdown
    logger.info("Shutting down Dental Clinic Backend...")

app = FastAPI(
    title="Dental Clinic Backend",
    description="Backend service for dental clinic appointment booking via WhatsApp",
    version="1.0.0",
    lifespan=lifespan
)

# Configure rate limiting
limiter = configure_rate_limiting(app)
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "Dental Clinic Backend",
        "status": "operational",
        "version": "1.0.0"
    }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": "2024-01-01T00:00:00Z"
    }

@app.post("/webhooks/twilio/whatsapp/{organization_id}")
@limiter.limit("30/minute")
async def handle_twilio_webhook(organization_id: str, request: Request):
    """Handle incoming WhatsApp messages via Twilio webhook"""
    try:
        # Parse form data from Twilio
        form_data = await request.form()
        payload = dict(form_data)

        # Verify webhook signature
        signature = request.headers.get('X-Twilio-Signature', '')
        auth_token = os.environ.get('TWILIO_AUTH_TOKEN', '')

        if not verify_twilio_signature(str(request.url), payload, signature, auth_token):
            raise HTTPException(status_code=403, detail="Invalid signature")

        # Log the webhook
        logger.info(f"Received WhatsApp webhook for org: {organization_id}")

        # Process the message
        result = await handle_whatsapp_webhook(organization_id, payload)

        return {"status": "received", "result": result}
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


class DentalClinicBot:
    """Main bot for handling dental clinic conversations"""

    def __init__(self, clinic_id: str):
        self.clinic_id = clinic_id

    async def handle_message(self, message_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle incoming message"""
        from .whatsapp import process_whatsapp_message

        phone = message_data.get('From', '').replace('whatsapp:', '')
        body = message_data.get('Body', '')

        result = await process_whatsapp_message(
            self.clinic_id,
            phone,
            body
        )

        return {
            'message': result.get('message', ''),
            'reminder_scheduled': result.get('reminder_scheduled', False)
        }


class DentalClinicSystem:
    """Main system for handling multiple clinics"""

    async def handle_message(self, phone: str, message: str) -> Dict[str, Any]:
        """Handle message from any phone"""
        # For testing, use a default clinic
        bot = DentalClinicBot('test-clinic-001')
        return await bot.handle_message({
            'From': f'whatsapp:{phone}',
            'Body': message
        })
