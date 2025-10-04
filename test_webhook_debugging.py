#!/usr/bin/env python3
"""
Test script to verify webhook debugging output
"""

import asyncio
import aiohttp
import json
from datetime import datetime

async def test_webhook_debugging():
    """Test the Evolution webhook with debugging output"""

    # Sample WhatsApp message data
    webhook_data = {
        "instanceName": "clinic-3e411ecb-3411-4add-91e2-8fa897310cb0-1757905315621",
        "message": {
            "key": {
                "remoteJid": "79857608984@s.whatsapp.net",
                "fromMe": False,
                "id": f"TEST_{datetime.now().timestamp()}"
            },
            "messageTimestamp": int(datetime.now().timestamp()),
            "pushName": "Debugging Test",
            "message": {
                "conversation": "Hello, I need to schedule an appointment"
            },
            "from": "79857608984@s.whatsapp.net",
            "text": "Hello, I need to schedule an appointment"
        }
    }

    # Test against production
    url = "https://healthcare-clinic-backend.fly.dev/webhooks/evolution/clinic-3e411ecb-3411-4add-91e2-8fa897310cb0-1757905315621"

    print(f"\n{'='*80}")
    print(f"TESTING WEBHOOK WITH DEBUGGING")
    print(f"URL: {url}")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"{'='*80}\n")

    print("Sending test webhook data:")
    print(json.dumps(webhook_data, indent=2))

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=webhook_data,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=5)  # 5 second timeout
            ) as response:
                status = response.status
                text = await response.text()

                print(f"\nResponse Status: {status}")
                print(f"Response Body: {text}")

                if status == 200:
                    print("✅ Webhook accepted successfully!")
                    print("\n⏳ Now check the logs to see the debugging output:")
                    print("   fly logs -a healthcare-clinic-backend")
                else:
                    print(f"❌ Unexpected status: {status}")

    except asyncio.TimeoutError:
        print("❌ Request timed out (webhook took too long to respond)")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_webhook_debugging())