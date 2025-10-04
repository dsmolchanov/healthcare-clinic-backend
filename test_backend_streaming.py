#!/usr/bin/env python3
"""
Test backend with streaming and timeout monitoring
"""

import asyncio
import httpx
import time
import json
import sys

BACKEND_URL = "https://healthcare-clinic-backend.fly.dev"

async def test_with_streaming():
    """Test with streaming to see partial responses"""

    print("Testing with streaming connection...")
    print("="*60)

    payload = {
        "from_phone": "widget_streaming_test",
        "to_phone": "+14155238886",
        "body": "Hello",
        "message_sid": f"test_{int(time.time())}",
        "clinic_id": "3e411ecb-3411-4add-91e2-8fa897310cb0",
        "clinic_name": "Test Clinic",
        "channel": "widget",
        "metadata": {
            "session_id": "streaming_test",
            "agent_id": "test-agent"
        }
    }

    print(f"Sending request to: {BACKEND_URL}/api/process-message")
    print(f"Payload: {json.dumps(payload, indent=2)}\n")

    timeout_config = httpx.Timeout(
        connect=10.0,
        read=60.0,
        write=10.0,
        pool=10.0
    )

    start_time = time.time()
    last_update = start_time

    try:
        async with httpx.AsyncClient(timeout=timeout_config) as client:
            print("Connecting...")

            async with client.stream(
                'POST',
                f"{BACKEND_URL}/api/process-message",
                json=payload
            ) as response:
                print(f"✅ Connected! Status: {response.status_code}")
                print(f"   Time to connect: {(time.time() - start_time)*1000:.2f}ms\n")

                if response.status_code != 200:
                    print(f"❌ Error status: {response.status_code}")
                    text = await response.aread()
                    print(f"Response: {text.decode()}")
                    return

                print("Reading response...")
                print("-"*60)

                chunks_received = 0
                total_bytes = 0

                async for chunk in response.aiter_bytes():
                    chunks_received += 1
                    total_bytes += len(chunk)
                    current_time = time.time()

                    if current_time - last_update >= 1.0:
                        elapsed = current_time - start_time
                        print(f"⏱️  {elapsed:.1f}s | Chunks: {chunks_received} | Bytes: {total_bytes}")
                        last_update = current_time

                total_time = time.time() - start_time
                print("-"*60)
                print(f"\n✅ Response received!")
                print(f"   Total time: {total_time:.2f}s")
                print(f"   Total chunks: {chunks_received}")
                print(f"   Total bytes: {total_bytes}")

                # Try to parse JSON
                try:
                    # Response should already be read
                    response_text = await response.aread()
                    data = json.loads(response_text.decode())
                    print(f"\n📥 Response data:")
                    print(json.dumps(data, indent=2))
                    return True
                except json.JSONDecodeError:
                    print(f"⚠️  Could not parse JSON response")
                    return False

    except httpx.TimeoutException as e:
        elapsed = time.time() - start_time
        print(f"\n❌ Timeout after {elapsed:.1f}s")
        print(f"   Exception: {e}")
        return False

    except Exception as e:
        elapsed = time.time() - start_time
        print(f"\n❌ Error after {elapsed:.1f}s")
        print(f"   Exception: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    print("\n" + "🔵" * 60)
    print("BACKEND STREAMING TEST")
    print("🔵" * 60 + "\n")

    success = await test_with_streaming()

    if success:
        print("\n🎉 Test PASSED")
        sys.exit(0)
    else:
        print("\n❌ Test FAILED")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())