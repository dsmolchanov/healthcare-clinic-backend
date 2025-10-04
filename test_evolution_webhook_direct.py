#!/usr/bin/env python3
"""
Test Evolution webhook directly
"""

import asyncio
import aiohttp
import json

async def test_webhook():
    """Test the Evolution webhook with a sample WhatsApp message"""

    # Sample WhatsApp message data (based on Evolution API format)
    webhook_data = {
        "instanceName": "test-instance",
        "message": {
            "key": {
                "remoteJid": "79857608984@s.whatsapp.net",
                "fromMe": False,
                "id": "TEST123"
            },
            "messageTimestamp": 1759105594,
            "pushName": "Test User",
            "message": {
                "conversation": "Test message from script"
            },
            "from": "79857608984@s.whatsapp.net",
            "text": "Test message from script"
        }
    }

    # Test locally first
    local_url = "http://localhost:8001/webhooks/evolution/test-instance"
    prod_url = "https://healthcare-clinic-backend.fly.dev/webhooks/evolution/test-instance"

    for url in [prod_url]:
        print(f"\n{'='*60}")
        print(f"Testing: {url}")
        print(f"{'='*60}")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=webhook_data,
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    status = response.status
                    text = await response.text()

                    print(f"Status: {status}")
                    print(f"Response: {text}")

                    if status == 200 or status == 202:
                        print("✅ Webhook accepted!")
                    else:
                        print(f"❌ Unexpected status: {status}")

        except asyncio.TimeoutError:
            print("❌ Request timed out")
        except Exception as e:
            print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_webhook())