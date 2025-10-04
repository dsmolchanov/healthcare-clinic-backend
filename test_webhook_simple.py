#!/usr/bin/env python3
"""
Test WhatsApp webhook with simple payload
"""

import asyncio
import aiohttp
import json

async def test_webhook():
    """Test the webhook endpoint with a simple payload"""

    webhook_url = "https://healthcare-clinic-backend.fly.dev/webhooks/whatsapp"

    # Simple test payload matching Evolution API structure
    payload = {
        "instanceName": "test-instance",
        "message": {
            "text": "Test message",
            "from": "1234567890@s.whatsapp.net",
            "pushName": "Test User"
        }
    }

    print(f"Testing webhook: {webhook_url}")
    print(f"Payload: {json.dumps(payload, indent=2)}")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                webhook_url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30)  # 30 second timeout
            ) as response:
                result = await response.json()
                print(f"\nResponse status: {response.status}")
                print(f"Response: {json.dumps(result, indent=2)}")

                if response.status == 200:
                    print("\n✓ Webhook is working!")
                else:
                    print(f"\n✗ Webhook returned status {response.status}")

    except asyncio.TimeoutError:
        print("\n✗ Request timed out after 30 seconds")
    except Exception as e:
        print(f"\n✗ Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_webhook())