#!/usr/bin/env python3
"""
Quick backend diagnostic test to identify the timeout issue
"""

import asyncio
import httpx
import time
import json

BACKEND_URL = "https://healthcare-clinic-backend.fly.dev"

async def test_health():
    """Test health endpoint"""
    print("Testing health endpoint...")
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            start = time.time()
            response = await client.get(f"{BACKEND_URL}/health")
            duration = (time.time() - start) * 1000
            print(f"✅ Health check: {response.status_code} ({duration:.2f}ms)")
            print(f"   Response: {response.json()}")
            return True
        except Exception as e:
            print(f"❌ Health check failed: {e}")
            return False

async def test_process_message_timeout():
    """Test process-message with short timeout to diagnose"""
    print("\nTesting process-message endpoint...")

    payload = {
        "from_phone": "widget_test_123",
        "to_phone": "+14155238886",
        "body": "Hello, quick test",
        "message_sid": f"test_{int(time.time())}",
        "clinic_id": "3e411ecb-3411-4add-91e2-8fa897310cb0",
        "clinic_name": "Test Clinic",
        "channel": "widget",
        "metadata": {
            "session_id": "test_session",
            "agent_id": "test-agent"
        }
    }

    print(f"Payload: {json.dumps(payload, indent=2)}")

    # Try with progressively longer timeouts
    for timeout in [5, 10, 30]:
        print(f"\n⏱️  Trying with {timeout}s timeout...")
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                start = time.time()
                response = await client.post(
                    f"{BACKEND_URL}/api/process-message",
                    json=payload
                )
                duration = (time.time() - start) * 1000
                print(f"✅ Got response: {response.status_code} ({duration:.2f}ms)")

                if response.status_code == 200:
                    data = response.json()
                    print(f"   Message: {data.get('message', '')[:100]}...")
                    print(f"   Session: {data.get('session_id')}")
                    print(f"   Metadata: {json.dumps(data.get('metadata', {}), indent=2)}")
                    return True
                else:
                    print(f"   Error response: {response.text}")

            except httpx.TimeoutException:
                print(f"⏱️  Timeout after {timeout}s")
                continue
            except Exception as e:
                print(f"❌ Error: {type(e).__name__}: {e}")
                continue

    print("\n❌ All timeout attempts failed")
    return False

async def main():
    print("="*60)
    print("BACKEND DIAGNOSTIC TEST")
    print("="*60)
    print(f"Backend URL: {BACKEND_URL}\n")

    # Test health
    health_ok = await test_health()
    if not health_ok:
        print("\n❌ Health check failed, aborting")
        return

    # Test message processing
    await test_process_message_timeout()

if __name__ == "__main__":
    asyncio.run(main())