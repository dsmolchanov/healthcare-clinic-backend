#!/usr/bin/env python3
"""Test what environment variables are available in production"""

import asyncio
import aiohttp
import json

PRODUCTION_URL = "https://healthcare-clinic-backend.fly.dev"

async def test_debug_endpoint():
    """Test a debug endpoint to see environment variables"""
    async with aiohttp.ClientSession() as session:
        # Try the health endpoint with a special debug parameter
        async with session.get(f"{PRODUCTION_URL}/api/debug/env") as resp:
            print(f"Debug endpoint status: {resp.status}")
            if resp.status == 200:
                data = await resp.json()
                print(f"Response: {json.dumps(data, indent=2)}")
            else:
                text = await resp.text()
                print(f"Response: {text}")

if __name__ == "__main__":
    asyncio.run(test_debug_endpoint())