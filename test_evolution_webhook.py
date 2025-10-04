#!/usr/bin/env python3
"""
Test Evolution API message flow by sending a test message
"""

import asyncio
import aiohttp
import json

async def test_send_message():
    """Send a test message to trigger webhook"""

    evolution_url = "https://evolution-api-prod.fly.dev"
    instance_name = "clinic-3e411ecb-3411-4add-91e2-8fa897310cb0-1757905315621"

    # Send to a test number (you can change this)
    test_number = "79857608984"  # The number that sent messages earlier
    test_message = "Test reply from Evolution API - webhook test"

    print(f"Sending test message to {test_number}...")

    async with aiohttp.ClientSession() as session:
        headers = {
            "Content-Type": "application/json"
        }

        payload = {
            "number": test_number,
            "text": test_message,
            "delay": 1000
        }

        try:
            async with session.post(
                f"{evolution_url}/message/sendText/{instance_name}",
                headers=headers,
                json=payload
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    print(f"✓ Message sent successfully!")
                    print(f"Response: {json.dumps(result, indent=2)}")
                else:
                    error = await response.text()
                    print(f"✗ Failed to send message: Status {response.status}")
                    print(f"Error: {error}")

        except Exception as e:
            print(f"✗ Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_send_message())