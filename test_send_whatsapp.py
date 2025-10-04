#!/usr/bin/env python3
"""
Test sending a WhatsApp message through Evolution API
"""

import asyncio
import aiohttp
import json

async def test_send_message():
    """Test sending a WhatsApp message"""

    evolution_url = "https://evolution-api-prod.fly.dev"
    api_key = "evolution_api_key_2024"
    instance_name = "clinic-3e411ecb-3411-4add-91e2-8fa897310cb0-1757905315621"

    # Test sending to Dmitry's number to trigger webhook response
    test_number = "79857608984"  # Dmitry's number
    test_message = "Test webhook - please reply with 'hello' to test the AI assistant response"

    print(f"Sending test message to {test_number}...")

    async with aiohttp.ClientSession() as session:
        headers = {
            "apikey": api_key,
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