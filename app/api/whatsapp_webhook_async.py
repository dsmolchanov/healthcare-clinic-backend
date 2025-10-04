"""
Asynchronous WhatsApp Webhook Handler
Responds immediately to webhook calls and processes messages in background
"""

import asyncio
import json
import logging
from typing import Optional, Dict, Any
from datetime import datetime
from fastapi import Request, Response, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
import httpx

from app.database import get_supabase_client
from app.services.whatsapp_processor import WhatsAppMessageProcessor

logger = logging.getLogger(__name__)

# In-memory queue for message processing (replace with Redis in production)
message_queue = asyncio.Queue(maxsize=1000)

# Background task processor
async def process_message_queue():
    """Background worker to process messages from queue"""
    processor = WhatsAppMessageProcessor()

    while True:
        try:
            # Get message from queue (waits if empty)
            message_data = await message_queue.get()

            logger.info(f"Processing queued message: {message_data.get('from', 'unknown')}")

            try:
                # Process the message (AI, RAG, etc.)
                await processor.process_message(message_data)
                logger.info(f"Successfully processed message from {message_data.get('from')}")
            except Exception as e:
                logger.error(f"Error processing message: {str(e)}")
                # Could implement retry logic here

        except Exception as e:
            logger.error(f"Queue processing error: {str(e)}")
            await asyncio.sleep(1)  # Brief pause on error

# Start background processor on app startup
asyncio.create_task(process_message_queue())


async def whatsapp_webhook_async(request: Request, background_tasks: BackgroundTasks):
    """
    Async webhook handler that responds immediately
    """
    try:
        # Log request details
        logger.info(f"Webhook received from {request.client.host}")
        logger.info(f"Headers: {dict(request.headers)}")

        # Try to read the body with timeout
        try:
            # Read raw body first (more reliable than json())
            body_bytes = await asyncio.wait_for(
                request.body(),
                timeout=5.0  # 5 second timeout for reading body
            )

            if not body_bytes:
                logger.warning("Empty request body received")
                return JSONResponse(
                    status_code=200,
                    content={"status": "ok", "message": "Empty body accepted"}
                )

            # Parse JSON
            try:
                data = json.loads(body_bytes)
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON: {str(e)}")
                logger.error(f"Raw body: {body_bytes[:500]}")  # Log first 500 chars
                return JSONResponse(
                    status_code=200,
                    content={"status": "ok", "message": "Invalid JSON accepted"}
                )

        except asyncio.TimeoutError:
            logger.error("Timeout reading request body")
            return JSONResponse(
                status_code=200,
                content={"status": "ok", "message": "Request timeout"}
            )

        # Validate required fields
        if not data:
            logger.warning("No data in request")
            return JSONResponse(
                status_code=200,
                content={"status": "ok", "message": "No data"}
            )

        # Extract message data
        message_info = data.get('message', {})
        from_number = message_info.get('from', '')
        text = message_info.get('text', '')
        instance_name = data.get('instanceName', 'unknown')

        logger.info(f"Message from {from_number}: {text[:100]}...")

        # Add to processing queue (non-blocking)
        try:
            # Don't wait if queue is full
            message_queue.put_nowait({
                'data': data,
                'from': from_number,
                'text': text,
                'instance': instance_name,
                'timestamp': datetime.utcnow().isoformat(),
                'raw_body': body_bytes.decode('utf-8', errors='ignore')
            })
            logger.info(f"Message queued for processing. Queue size: {message_queue.qsize()}")
        except asyncio.QueueFull:
            logger.error("Message queue is full! Message dropped")
            # Still return success to avoid webhook retries

        # Return success immediately
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Webhook received",
                "queued": True,
                "timestamp": datetime.utcnow().isoformat()
            }
        )

    except Exception as e:
        logger.error(f"Webhook error: {str(e)}", exc_info=True)

        # Still return 200 to prevent webhook retry storms
        return JSONResponse(
            status_code=200,
            content={
                "status": "error",
                "message": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }
        )


async def whatsapp_webhook_simple(request: Request):
    """
    Ultra-simple webhook that just acknowledges receipt
    """
    try:
        # Don't even read the body - just acknowledge
        logger.info("Simple webhook called - immediate ACK")

        # Optional: Try to read body in background if needed
        try:
            body = await request.body()
            logger.info(f"Body length: {len(body)} bytes")
        except:
            logger.warning("Could not read body")

        return JSONResponse(
            status_code=200,
            content={"status": "ok", "timestamp": datetime.utcnow().isoformat()}
        )
    except Exception as e:
        logger.error(f"Simple webhook error: {str(e)}")
        return JSONResponse(
            status_code=200,
            content={"status": "error", "message": str(e)}
        )