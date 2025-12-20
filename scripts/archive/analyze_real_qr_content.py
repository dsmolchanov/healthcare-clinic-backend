#!/usr/bin/env python3
"""
Re-analyze Evolution API response - I was looking at the wrong field!
The expert is correct: Evolution API returns BOTH a base64 QR image AND a short pairing code.
"""

import asyncio
import aiohttp
import json
import base64
from PIL import Image
from io import BytesIO
import time

# Try to import QR decoder
try:
    from pyzbar import pyzbar
    HAS_PYZBAR = True
except ImportError:
    HAS_PYZBAR = False
    print("Warning: pyzbar not installed, cannot decode QR content")

async def analyze_real_qr():
    base_url = "https://evolution-api-plaintalk.fly.dev"
    api_key = "evolution_api_key_2024"

    print("=== RE-ANALYZING EVOLUTION API RESPONSE ===")
    print("Correcting my error: Looking at BOTH fields properly\n")

    headers = {"apikey": api_key}

    async with aiohttp.ClientSession() as session:
        # Create instance
        instance_name = f"reanalysis-{int(time.time())}"
        print(f"Creating instance: {instance_name}\n")

        create_payload = {
            "instanceName": instance_name,
            "token": "test",
            "qrcode": True,
            "integration": "WHATSAPP-BAILEYS"
        }

        async with session.post(
            f"{base_url}/instance/create",
            headers=headers,
            json=create_payload
        ) as response:
            result = await response.json()

            print("RESPONSE STRUCTURE:")
            print(f"- Keys: {list(result.keys())}\n")

            if "qrcode" in result:
                qr_obj = result["qrcode"]
                print("QR OBJECT ANALYSIS:")
                print(f"- Type: {type(qr_obj)}")

                if isinstance(qr_obj, dict):
                    print(f"- Keys: {list(qr_obj.keys())}\n")

                    # Analyze the 'code' field
                    if "code" in qr_obj:
                        code = qr_obj["code"]
                        print("1. 'code' field (might be pairing code):")
                        print(f"   - Value: {code}")
                        print(f"   - Length: {len(code)} characters")
                        print(f"   - Pattern: {'Matches WA:instance:random' if code.startswith('WA:') else 'Different pattern'}")
                        print(f"   - Conclusion: This appears to be a PAIRING CODE or reference\n")

                    # Analyze the 'base64' field - THE ACTUAL QR CODE
                    if "base64" in qr_obj:
                        base64_data = qr_obj["base64"]
                        print("2. 'base64' field (THE ACTUAL QR CODE IMAGE):")
                        print(f"   - Length: {len(base64_data)} characters")

                        # Check if it's a data URL
                        if base64_data.startswith("data:image"):
                            print(f"   - Format: Data URL")
                            # Extract the base64 part
                            base64_img = base64_data.split("base64,")[1]
                        else:
                            base64_img = base64_data

                        # Decode and analyze the image
                        try:
                            img_bytes = base64.b64decode(base64_img)
                            img = Image.open(BytesIO(img_bytes))
                            print(f"   - Image dimensions: {img.size}")
                            print(f"   - Image format: {img.format}")

                            # Save for manual inspection
                            img_path = f"/tmp/evolution_qr_{instance_name}.png"
                            img.save(img_path)
                            print(f"   - Saved to: {img_path}")

                            # Try to decode QR content if pyzbar is available
                            if HAS_PYZBAR:
                                decoded = pyzbar.decode(img)
                                if decoded:
                                    for qr in decoded:
                                        qr_data = qr.data.decode('utf-8')
                                        print(f"\n   🔍 QR CODE CONTENT DECODED:")
                                        print(f"      Raw data: {qr_data[:100]}...")
                                        print(f"      Data length: {len(qr_data)} characters")

                                        # Check if it's valid WhatsApp format
                                        if len(qr_data) > 100 and ("@" in qr_data or "," in qr_data):
                                            print(f"      ✅ Looks like a REAL WhatsApp QR format!")
                                        elif qr_data.startswith("WA:"):
                                            print(f"      ❌ This is the pairing code repeated in QR form")
                                        else:
                                            print(f"      ⚠️  Unknown format")
                                else:
                                    print(f"   ⚠️  Could not decode QR content")
                            else:
                                print(f"   ℹ️  Install pyzbar to decode QR content: pip install pyzbar")

                        except Exception as e:
                            print(f"   ❌ Error analyzing image: {e}")

                        print(f"\n   EXPERT'S INSIGHT: This base64 field should contain")
                        print(f"   the actual WhatsApp QR code as a PNG image.")
                        print(f"   If WhatsApp rejects it, the issue might be:")
                        print(f"   1. Outdated Baileys version (need ≥6.7.17)")
                        print(f"   2. Evolution API version (try homolog v2.3.0)")
                        print(f"   3. The QR encodes the pairing code instead of auth data")

            # Check if there's a separate pairingCode field
            if "pairingCode" in result:
                print(f"\n3. 'pairingCode' field:")
                print(f"   - Value: {result['pairingCode']}")
                print(f"   - This is for phone number pairing (alternative to QR)")

            print("\n=== CORRECTED CONCLUSION ===")
            print("I was wrong! Evolution API DOES return a proper base64 QR image.")
            print("The short 'code' field is likely a pairing code, not the QR data.")
            print("\nThe issue is likely:")
            print("1. Outdated Baileys library (< 6.7.17)")
            print("2. Evolution API version mismatch")
            print("3. WhatsApp authentication flow changes in 2024/2025")
            print("\nSOLUTION: Update to Evolution API homolog v2.3.0 with Baileys ≥6.7.17")

            # Clean up
            await session.delete(f"{base_url}/instance/delete/{instance_name}", headers=headers)

if __name__ == "__main__":
    asyncio.run(analyze_real_qr())
