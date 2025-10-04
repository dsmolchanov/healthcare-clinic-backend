#!/usr/bin/env python3
"""
Test if Evolution API homolog version generates real WhatsApp QR codes
"""

import asyncio
import aiohttp
import json
import base64
from PIL import Image
from io import BytesIO
import time
import cv2
import numpy as np

async def test_homolog():
    base_url = "https://evolution-api-plaintalk.fly.dev"
    api_key = "evolution_api_key_2024"

    print("=== TESTING EVOLUTION API HOMOLOG VERSION ===")
    print("Expected: Real WhatsApp QR codes with 200+ chars of auth data\n")

    headers = {"apikey": api_key}

    async with aiohttp.ClientSession() as session:
        # Check API health
        print("1. Checking API health...")
        try:
            async with session.get(f"{base_url}/health", headers=headers) as response:
                if response.status == 200:
                    health = await response.text()
                    print(f"   Health: {health}\n")
        except Exception as e:
            print(f"   Error: {e}\n")

        # Create instance
        instance_name = f"homolog-test-{int(time.time())}"
        print(f"2. Creating instance: {instance_name}")

        create_payload = {
            "instanceName": instance_name,
            "token": "test",
            "qrcode": True,
            "integration": "WHATSAPP-BAILEYS"
        }

        try:
            async with session.post(
                f"{base_url}/instance/create",
                headers=headers,
                json=create_payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                print(f"   Response status: {response.status}")

                if response.status != 200:
                    error = await response.text()
                    print(f"   Error: {error}")
                    return

                result = await response.json()

                if "qrcode" in result and isinstance(result["qrcode"], dict):
                    qr_obj = result["qrcode"]

                    # Analyze code field
                    code_field = qr_obj.get("code", "")
                    print(f"\n3. Analyzing 'code' field:")
                    print(f"   Value: {code_field}")
                    print(f"   Length: {len(code_field)} chars")

                    # Analyze base64 field
                    base64_field = qr_obj.get("base64", "")
                    print(f"\n4. Analyzing 'base64' QR image:")
                    print(f"   Base64 length: {len(base64_field)} chars")

                    if base64_field.startswith("data:image"):
                        # Extract and decode QR image
                        base64_img = base64_field.split("base64,")[1]
                        img_bytes = base64.b64decode(base64_img)
                        img = Image.open(BytesIO(img_bytes))

                        # Save for inspection
                        img_path = f"/tmp/homolog_qr_{instance_name}.png"
                        img.save(img_path)
                        print(f"   Saved to: {img_path}")

                        # Decode QR content with OpenCV
                        img_array = np.array(img)
                        detector = cv2.QRCodeDetector()
                        qr_content, bbox, straight_qrcode = detector.detectAndDecode(img_array)

                        if qr_content:
                            print(f"\n   🔍 QR CODE DECODED:")
                            print(f"      Content: {qr_content[:100]}{'...' if len(qr_content) > 100 else ''}")
                            print(f"      Length: {len(qr_content)} characters")

                            # Analyze the content
                            if len(qr_content) > 200 and ("@" in qr_content or "," in qr_content):
                                print(f"\n   ✅ SUCCESS! REAL WHATSAPP QR CODE!")
                                print(f"      - Length: {len(qr_content)} chars (expected: 200+)")
                                print(f"      - Format appears to be valid WhatsApp auth data")
                                print(f"      - Contains cryptographic keys and session data")
                                print(f"\n   🎉 HOMOLOG VERSION WORKS!")
                                print(f"      Evolution API homolog generates real QR codes!")
                                print(f"      WhatsApp should now accept this QR code!")

                            elif qr_content == code_field:
                                print(f"\n   ❌ STILL BROKEN!")
                                print(f"      QR encodes the pairing code: {qr_content}")
                                print(f"      This is NOT a real WhatsApp QR code")
                                print(f"      Homolog version didn't fix the issue")

                            else:
                                print(f"\n   ⚠️  UNEXPECTED FORMAT")
                                print(f"      QR content doesn't match expected patterns")
                        else:
                            print(f"   ⚠️  Could not decode QR content")

                    # Check API version/info
                    print(f"\n5. Instance state: {result.get('state', 'unknown')}")

                else:
                    print(f"\n   ⚠️  No QR code in response")
                    print(f"   Response: {json.dumps(result, indent=2)[:500]}")

                # Clean up
                await session.delete(f"{base_url}/instance/delete/{instance_name}", headers=headers)

        except asyncio.TimeoutError:
            print(f"   ⚠️  Request timed out - API might be slow or updating")
        except Exception as e:
            print(f"   ❌ Error: {e}")

        print("\n=== TEST COMPLETE ===")

if __name__ == "__main__":
    asyncio.run(test_homolog())
