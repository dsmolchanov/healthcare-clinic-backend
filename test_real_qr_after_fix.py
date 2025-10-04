#!/usr/bin/env python3
"""
Test Evolution API after fixing CONFIG_SESSION_PHONE_VERSION
Expected: Real WhatsApp QR codes (200+ chars) instead of mock codes (27 chars)
"""

import asyncio
import aiohttp
import json
import time

async def test_real_qr():
    base_url = "https://evolution-api-plaintalk.fly.dev"
    api_key = "evolution_api_key_2024"

    print("=== Testing Evolution API QR Code After Fix ===\n")
    print("Expected: Real WhatsApp QR codes (200+ characters)")
    print("Previous: Mock QR codes (27 characters: WA:instance:8chars)")
    print("-" * 50)

    headers = {"apikey": api_key}

    async with aiohttp.ClientSession() as session:
        # Wait a bit for deployment to complete
        print("\nWaiting for deployment to complete...")
        await asyncio.sleep(10)

        # Create instance
        instance_name = f"real-qr-test-{int(time.time())}"
        print(f"\nCreating instance: {instance_name}")

        create_payload = {
            "instanceName": instance_name,
            "token": "test_token",
            "qrcode": True,
            "integration": "WHATSAPP-BAILEYS"
        }

        try:
            async with session.post(
                f"{base_url}/instance/create",
                headers=headers,
                json=create_payload
            ) as response:
                if response.status != 200:
                    print(f"Error {response.status}: {await response.text()}")
                    return

                result = await response.json()

                if "qrcode" in result:
                    qr = result["qrcode"]
                    code = qr.get("code", "")

                    print(f"\n✅ QR Code Analysis:")
                    print(f"  - Code length: {len(code)} characters")
                    print(f"  - First 50 chars: {code[:50] if code else 'Empty'}...")

                    if len(code) > 100:
                        print(f"\n  🎉 SUCCESS! Real WhatsApp QR code generated!")
                        print(f"  - This should now work when scanned with WhatsApp")
                        print(f"  - Code format appears to be valid WhatsApp format")
                    elif code.startswith("WA:") and len(code) < 50:
                        print(f"\n  ❌ STILL MOCK QR CODE")
                        print(f"  - Evolution API still generating placeholder codes")
                        print(f"  - May need different configuration or version")
                    else:
                        print(f"\n  ⚠️  Unexpected QR format")
                        print(f"  - Not clearly mock or real")

                print(f"\nInstance state: {result.get('state')}")

                # Check connection state
                await asyncio.sleep(3)
                print(f"\nChecking connection state...")

                async with session.get(
                    f"{base_url}/instance/connectionState/{instance_name}",
                    headers=headers
                ) as conn_response:
                    if conn_response.status == 200:
                        conn_state = await conn_response.json()
                        print(f"Connection: {json.dumps(conn_state, indent=2)}")

                # Clean up
                print(f"\nCleaning up...")
                await session.delete(
                    f"{base_url}/instance/delete/{instance_name}",
                    headers=headers
                )

        except aiohttp.ClientError as e:
            print(f"Network error: {e}")
            print("Evolution API may still be deploying...")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_real_qr())
