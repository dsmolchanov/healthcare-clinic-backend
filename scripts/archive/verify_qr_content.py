#!/usr/bin/env python3
"""
Verify what's actually encoded in the Evolution API QR code image
"""

import asyncio
import aiohttp
import json
import base64
from datetime import datetime
from PIL import Image
from io import BytesIO
from pyzbar.pyzbar import decode

EVOLUTION_URL = "https://evolution-api-plaintalk.fly.dev"
API_KEY = "evolution_api_key_2024"

async def verify_qr_content():
    headers = {"apikey": API_KEY, "Content-Type": "application/json"}
    instance_name = f"verify-qr-{int(datetime.now().timestamp())}"

    async with aiohttp.ClientSession(headers=headers) as session:
        print(f"\n{'='*60}")
        print("Verifying QR Code Content")
        print(f"{'='*60}\n")

        # Create instance
        payload = {
            "instanceName": instance_name,
            "token": "test123",
            "qrcode": True,
            "integration": "WHATSAPP-BAILEYS"
        }

        async with session.post(f"{EVOLUTION_URL}/instance/create", json=payload) as resp:
            if resp.status == 200:
                data = await resp.json()

                if 'qrcode' in data and isinstance(data['qrcode'], dict):
                    code_field = data['qrcode'].get('code', '')
                    base64_field = data['qrcode'].get('base64', '')

                    print(f"1. Evolution API Response:")
                    print(f"   Code field: {code_field}")

                    if base64_field:
                        # Remove data URI prefix
                        if base64_field.startswith('data:image'):
                            base64_data = base64_field.split(',')[1]
                        else:
                            base64_data = base64_field

                        # Decode base64 to image
                        img_data = base64.b64decode(base64_data)
                        img = Image.open(BytesIO(img_data))

                        # Decode QR code content
                        decoded_objects = decode(img)

                        print(f"\n2. QR Code Image Content:")
                        if decoded_objects:
                            for obj in decoded_objects:
                                qr_data = obj.data.decode('utf-8')
                                print(f"   Decoded: {qr_data}")

                                print(f"\n3. Analysis:")
                                if qr_data == code_field:
                                    print(f"   ❌ QR image contains the SAME mock data: {qr_data}")
                                    print(f"   This is NOT a valid WhatsApp QR code!")
                                    print(f"   WhatsApp expects different data format")
                                elif qr_data.startswith('WA:'):
                                    print(f"   ❌ QR still contains Evolution's mock format")
                                else:
                                    print(f"   ✅ QR contains different data!")
                                    print(f"   Length: {len(qr_data)} characters")
                                    print(f"   This might be valid WhatsApp data")
                        else:
                            print("   ⚠️ Could not decode QR code from image")

if __name__ == "__main__":
    asyncio.run(verify_qr_content())
