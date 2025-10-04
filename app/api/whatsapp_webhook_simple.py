"""
Simple WhatsApp Webhook Handler for Evolution API

Minimal implementation that responds quickly to avoid timeouts
"""

from fastapi import APIRouter, Request
from typing import Dict, Any
import os
import json
import logging
from openai import AsyncOpenAI

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)

# Initialize OpenAI client
openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

@router.post("/whatsapp/simple")
async def whatsapp_webhook_simple(request: Request):
    """Simple WhatsApp webhook that responds quickly"""
    try:
        # Read request body immediately
        data = await request.json()

        # Extract basic information
        instance_name = data.get("instanceName", "")
        message_data = data.get("message", {})
        text = message_data.get("text", "")
        from_number = message_data.get("from", "")
        push_name = message_data.get("pushName", "User")

        logger.info(f"Received WhatsApp message from {push_name} ({from_number}): {text}")

        # Ignore empty messages
        if not text or not from_number:
            return {"status": "ignored", "reason": "No text or sender"}

        # Send immediate acknowledgment response
        # This prevents timeout while we process the actual response
        return {"status": "received", "message": "Processing your request..."}

    except Exception as e:
        logger.error(f"Error in simple webhook: {e}")
        return {"status": "error", "error": str(e)}

@router.post("/whatsapp/echo")
async def whatsapp_webhook_echo(request: Request):
    """Echo webhook for testing - just returns what was sent"""
    try:
        data = await request.json()
        return {
            "status": "success",
            "received": data,
            "echo": True
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}