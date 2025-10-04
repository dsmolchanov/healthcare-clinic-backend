#!/usr/bin/env python3
"""
Test the fixed WhatsApp webhook endpoint
"""

import asyncio
import httpx
import json
from datetime import datetime

async def test_webhook():
    """Test the webhook endpoint with various scenarios"""

    webhook_url = "https://healthcare-clinic-backend.fly.dev/webhooks/whatsapp"

    # Test payload that mimics Evolution API
    test_payload = {
        "instanceName": "clinic-3e411ecb-3411-4add-91e2-8fa897310cb0-test",
        "message": {
            "key": {
                "remoteJid": "1234567890@s.whatsapp.net",
                "fromMe": False,
                "id": "test_message_123"
            },
            "messageTimestamp": str(int(datetime.now().timestamp())),
            "pushName": "Test User",
            "message": {
                "conversation": "Hola, necesito una cita para limpieza dental"
            },
            "from": "1234567890@s.whatsapp.net",
            "text": "Hola, necesito una cita para limpieza dental"
        }
    }

    print("Testing WhatsApp webhook...")
    print(f"URL: {webhook_url}")
    print(f"Payload: {json.dumps(test_payload, indent=2)}")

    async with httpx.AsyncClient() as client:
        try:
            # Test 1: Normal request with timeout
            print("\n1. Testing with 5-second timeout...")
            response = await client.post(
                webhook_url,
                json=test_payload,
                timeout=5.0
            )
            print(f"   Status: {response.status_code}")
            print(f"   Response: {response.json()}")

            # Test 2: Quick request (simulate Evolution API behavior)
            print("\n2. Testing with 1-second timeout (Evolution-like)...")
            try:
                response = await client.post(
                    webhook_url,
                    json=test_payload,
                    timeout=1.0
                )
                print(f"   Status: {response.status_code}")
                print(f"   Response: {response.json()}")
            except httpx.TimeoutException:
                print("   Timeout (expected if processing takes time)")

            # Test 3: Empty body
            print("\n3. Testing with empty body...")
            response = await client.post(
                webhook_url,
                json={},
                timeout=2.0
            )
            print(f"   Status: {response.status_code}")
            print(f"   Response: {response.json()}")

            # Test 4: Health check
            print("\n4. Testing health endpoint...")
            health_url = "https://healthcare-clinic-backend.fly.dev/health"
            response = await client.get(health_url)
            print(f"   Status: {response.status_code}")
            print(f"   Response: {response.json()}")

            print("\n✅ All tests completed!")

        except Exception as e:
            print(f"\n❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_webhook())