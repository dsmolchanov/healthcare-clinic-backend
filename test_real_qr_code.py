#!/usr/bin/env python3
"""
Test if Evolution API generates real WhatsApp QR codes after fix
"""

import asyncio
import aiohttp
import json
from datetime import datetime

EVOLUTION_URL = "https://evolution-api-plaintalk.fly.dev"
API_KEY = "evolution_api_key_2024"

async def test_qr_code():
    headers = {"apikey": API_KEY, "Content-Type": "application/json"}
    instance_name = f"test-real-qr-{int(datetime.now().timestamp())}"

    async with aiohttp.ClientSession(headers=headers) as session:
        print(f"\n{'='*60}")
        print("Testing Evolution API QR Code Generation")
        print(f"Time: {datetime.now().isoformat()}")
        print(f"Instance: {instance_name}")
        print(f"{'='*60}\n")

        # Create new instance
        print("1. Creating new WhatsApp instance...")
        payload = {
            "instanceName": instance_name,
            "token": "test123",
            "qrcode": True,
            "integration": "WHATSAPP-BAILEYS"
        }

        try:
            async with session.post(f"{EVOLUTION_URL}/instance/create", json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    print(f"   ✓ Instance created: {data.get('instance', {}).get('instanceName')}")

                    # Check QR code format
                    if 'qrcode' in data and isinstance(data['qrcode'], dict):
                        qr_code = data['qrcode'].get('code', '')
                        qr_base64 = data['qrcode'].get('base64', '')

                        print(f"\n2. QR Code Analysis:")
                        print(f"   Code format: {qr_code[:50] if qr_code else 'No code'}")
                        print(f"   Has base64 image: {'Yes' if qr_base64 else 'No'}")

                        if qr_code.startswith('WA:'):
                            print(f"\n   ❌ FAILED: Still generating MOCK QR codes")
                            print(f"   Format: WA:instance:code")
                            print(f"   This will NOT work with WhatsApp")
                            print(f"\n   Possible issues:")
                            print(f"   - CONFIG_SESSION_PHONE_VERSION not applied")
                            print(f"   - Evolution API needs restart")
                            print(f"   - Using wrong Evolution API version")
                        else:
                            print(f"\n   ✅ SUCCESS: Real WhatsApp QR code detected!")
                            print(f"   Length: {len(qr_code)} characters")
                            print(f"   This should work with WhatsApp scanning")
                            print(f"\n   Next steps:")
                            print(f"   1. Open WhatsApp on your phone")
                            print(f"   2. Go to Settings → Linked Devices → Link a Device")
                            print(f"   3. Scan the QR code displayed in the frontend")
                    else:
                        print("   ⚠️ No QR code in response")
                else:
                    print(f"   ❌ Failed to create instance: HTTP {resp.status}")
                    text = await resp.text()
                    print(f"   Response: {text[:200]}")
        except Exception as e:
            print(f"   ❌ Error: {e}")

        print(f"\n{'='*60}")
        print("Test complete")
        print(f"{'='*60}\n")

if __name__ == "__main__":
    asyncio.run(test_qr_code())
