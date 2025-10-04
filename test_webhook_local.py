#!/usr/bin/env python3
"""
Local test script for Evolution API webhook integration
Tests the complete flow from webhook receipt to LangGraph processing
"""

import asyncio
import aiohttp
import json
import logging
from datetime import datetime
import sys
import os

# Add the app directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Test configuration
LOCAL_URL = "http://localhost:8000"  # Adjust if running on different port
WEBHOOK_PATH = "/webhooks/evolution/clinic-3e411ecb-3411-4add-91e2-8fa897310cb0-1757905315621"

# Sample Evolution API webhook payload
SAMPLE_WEBHOOK_PAYLOAD = {
    "instance": {
        "instanceName": "clinic-3e411ecb-3411-4add-91e2-8fa897310cb0-1757905315621",
        "status": "open"
    },
    "data": {
        "key": {
            "remoteJid": "14155238886@s.whatsapp.net",
            "fromMe": False,
            "id": "3EB0B4F5A7D8C9E2F1A3"
        },
        "message": {
            "conversation": "Hello, I need to schedule an appointment for next week",
            "messageTimestamp": "1757905315"
        },
        "pushName": "Test Patient",
        "messageType": "conversation"
    },
    "event": "messages.upsert",
    "apikey": "test-api-key"
}

async def start_local_server():
    """Start the FastAPI server locally"""
    logger.info("Starting local FastAPI server...")

    # Import the FastAPI app
    try:
        from app.main import app
        import uvicorn

        # Run in a separate thread
        import threading

        def run_server():
            uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()

        # Wait for server to start
        await asyncio.sleep(3)
        logger.info("✅ Local server started on port 8000")
        return True

    except ImportError as e:
        logger.error(f"Failed to import FastAPI app: {e}")
        logger.info("Please ensure you're running from the clinics/backend directory")
        return False

async def test_health_check():
    """Test if the server is running"""
    logger.info("Testing health check...")

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{LOCAL_URL}/health") as response:
                if response.status == 200:
                    data = await response.json()
                    logger.info(f"✅ Health check passed: {data}")
                    return True
                else:
                    logger.error(f"❌ Health check failed with status {response.status}")
                    return False
        except aiohttp.ClientError as e:
            logger.error(f"❌ Failed to connect to server: {e}")
            logger.info("Please ensure the server is running: uvicorn app.main:app --reload")
            return False

async def test_webhook_endpoint(payload=None):
    """Test the Evolution webhook endpoint"""
    if payload is None:
        payload = SAMPLE_WEBHOOK_PAYLOAD

    logger.info(f"Testing webhook endpoint: {WEBHOOK_PATH}")
    logger.info(f"Payload: {json.dumps(payload, indent=2)}")

    async with aiohttp.ClientSession() as session:
        try:
            url = f"{LOCAL_URL}{WEBHOOK_PATH}"
            headers = {
                "Content-Type": "application/json",
                "X-Webhook-Signature": "test-signature"  # Add if HMAC is enabled
            }

            async with session.post(url, json=payload, headers=headers) as response:
                response_data = await response.text()

                logger.info(f"Response Status: {response.status}")
                logger.info(f"Response Headers: {dict(response.headers)}")
                logger.info(f"Response Body: {response_data}")

                if response.status == 200:
                    logger.info("✅ Webhook processed successfully")
                    try:
                        data = json.loads(response_data)
                        if "response" in data:
                            logger.info(f"🤖 Agent Response: {data['response']}")
                    except json.JSONDecodeError:
                        pass
                    return True
                else:
                    logger.error(f"❌ Webhook failed with status {response.status}")
                    return False

        except aiohttp.ClientError as e:
            logger.error(f"❌ Request failed: {e}")
            return False

async def test_langgraph_direct():
    """Test LangGraph service directly"""
    logger.info("Testing LangGraph service directly...")

    payload = {
        "session_id": "test-session-123",
        "text": "Hello, I need to schedule an appointment for next week",
        "metadata": {
            "user_id": "test-user",
            "channel": "whatsapp"
        },
        "use_healthcare": True,
        "enable_rag": True,
        "enable_memory": True
    }

    async with aiohttp.ClientSession() as session:
        try:
            url = f"{LOCAL_URL}/langgraph/process"
            headers = {"Content-Type": "application/json"}

            async with session.post(url, json=payload, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    response_text = data.get('response', 'No response')
                    if isinstance(response_text, dict):
                        response_text = response_text.get('text', 'No text')
                    logger.info(f"✅ LangGraph response: {response_text}")
                    logger.info(f"Processing time: {data.get('latency_ms', 'N/A')}ms")
                    return True
                else:
                    error = await response.text()
                    logger.error(f"❌ LangGraph failed: {error}")
                    return False

        except aiohttp.ClientError as e:
            logger.error(f"❌ Request failed: {e}")
            return False

async def test_multiple_messages():
    """Test a conversation flow with multiple messages"""
    logger.info("Testing conversation flow...")

    messages = [
        "Hello, I need to schedule an appointment",
        "I prefer morning appointments",
        "Next Tuesday would be great",
        "My name is John Doe",
        "Thank you!"
    ]

    session_id = f"test-session-{datetime.now().timestamp()}"

    for i, message in enumerate(messages, 1):
        logger.info(f"\n--- Message {i}/{len(messages)} ---")
        payload = SAMPLE_WEBHOOK_PAYLOAD.copy()
        payload["data"]["message"]["conversation"] = message
        payload["data"]["key"]["id"] = f"msg-{i}-{datetime.now().timestamp()}"

        await test_webhook_endpoint(payload)
        await asyncio.sleep(1)  # Wait between messages

async def main():
    """Main test orchestrator"""
    logger.info("=" * 60)
    logger.info("Evolution API Webhook Local Testing")
    logger.info("=" * 60)

    # Check if server needs to be started
    if "--start-server" in sys.argv:
        if not await start_local_server():
            logger.error("Failed to start local server")
            return

    # Test 1: Health check
    logger.info("\n📍 Test 1: Health Check")
    if not await test_health_check():
        logger.error("Server is not running. Start it with: uvicorn app.main:app --reload")
        return

    # Test 2: Direct LangGraph test
    logger.info("\n📍 Test 2: Direct LangGraph Service")
    await test_langgraph_direct()

    # Test 3: Single webhook message
    logger.info("\n📍 Test 3: Single Webhook Message")
    await test_webhook_endpoint()

    # Test 4: Conversation flow
    logger.info("\n📍 Test 4: Multi-Message Conversation")
    await test_multiple_messages()

    logger.info("\n" + "=" * 60)
    logger.info("Testing completed!")
    logger.info("=" * 60)

if __name__ == "__main__":
    print("""
    Evolution API Webhook Local Testing

    Usage:
        python test_webhook_local.py              # Test against running server
        python test_webhook_local.py --start-server  # Start server and test

    Make sure to:
    1. Set environment variables (OPENAI_API_KEY, etc.)
    2. Run from the clinics/backend directory
    """)

    asyncio.run(main())