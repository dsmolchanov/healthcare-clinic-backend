#!/usr/bin/env python3
"""
Verify the EXACT response structure from our Evolution API deployment
Compare with what the documentation says it SHOULD return
"""

import asyncio
import aiohttp
import json
import time
import base64
import cv2
import numpy as np
from PIL import Image
from io import BytesIO

async def verify_response():
    base_url = "https://evolution-api-plaintalk.fly.dev"
    api_key = "evolution_api_key_2024"

    print("=== VERIFYING EXACT API RESPONSE STRUCTURE ===\n")
    print("According to expert/documentation, Evolution API should return:")
    print("- code: Starting with '2@' (200+ chars of auth data)")
    print("- pairingCode: 8 character alphanumeric string")
    print("- base64: QR image encoding the long 'code' value\n")
    print("Let's see what our deployment ACTUALLY returns:\n")

    headers = {"apikey": api_key}

    async with aiohttp.ClientSession() as session:
        # Create instance
        instance_name = f"verify-{int(time.time())}"

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

            print("RAW RESPONSE:")
            print(json.dumps(result, indent=2, default=str))
            print("\n" + "="*60 + "\n")

            # Analyze the exact structure
            print("RESPONSE ANALYSIS:\n")

            # Top level keys
            print("1. Top-level keys:", list(result.keys()))

            # QR object analysis
            if "qrcode" in result:
                qr_obj = result["qrcode"]
                print(f"\n2. 'qrcode' object type: {type(qr_obj)}")

                if isinstance(qr_obj, dict):
                    print(f"   Keys in qrcode object: {list(qr_obj.keys())}")

                    # Check for 'code' field
                    if "code" in qr_obj:
                        code = qr_obj["code"]
                        print(f"\n3. 'code' field:")
                        print(f"   - Value: {code[:50]}{'...' if len(code) > 50 else ''}")
                        print(f"   - Length: {len(code)} chars")
                        print(f"   - Starts with '2@': {code.startswith('2@')}")
                        print(f"   - Starts with 'WA:': {code.startswith('WA:')}")

                    # Check for 'pairingCode' field
                    if "pairingCode" in qr_obj:
                        pairing = qr_obj["pairingCode"]
                        print(f"\n4. 'pairingCode' field:")
                        print(f"   - Value: {pairing}")
                        print(f"   - Length: {len(pairing)} chars")
                    else:
                        print(f"\n4. 'pairingCode' field: NOT PRESENT")

                    # Check base64 QR image
                    if "base64" in qr_obj:
                        b64 = qr_obj["base64"]
                        print(f"\n5. 'base64' QR image:")
                        print(f"   - Length: {len(b64)} chars")

                        # Decode QR content
                        if b64.startswith("data:image"):
                            b64_img = b64.split("base64,")[1]
                            img_bytes = base64.b64decode(b64_img)
                            img = Image.open(BytesIO(img_bytes))
                            img_array = np.array(img)

                            detector = cv2.QRCodeDetector()
                            qr_content, _, _ = detector.detectAndDecode(img_array)

                            if qr_content:
                                print(f"   - QR encodes: {qr_content[:50]}{'...' if len(qr_content) > 50 else ''}")
                                print(f"   - QR content length: {len(qr_content)} chars")
                                print(f"   - QR starts with '2@': {qr_content.startswith('2@')}")
                                print(f"   - QR matches 'code' field: {qr_content == code if 'code' in locals() else 'N/A'}")

            # Check for pairingCode at top level
            if "pairingCode" in result:
                print(f"\n6. Top-level 'pairingCode': {result['pairingCode']}")

            print("\n" + "="*60 + "\n")
            print("CONCLUSION:\n")

            # Determine what's happening
            if "qrcode" in result and isinstance(result["qrcode"], dict):
                qr = result["qrcode"]
                code = qr.get("code", "")

                if code.startswith("2@") and len(code) > 100:
                    print("✅ OUR DEPLOYMENT IS WORKING CORRECTLY!")
                    print("   Returns proper '2@' format WhatsApp auth code")
                    print("   Expert was right - the API should work")
                elif code.startswith("WA:") and len(code) < 50:
                    print("❌ OUR DEPLOYMENT HAS AN ISSUE!")
                    print("   Returns 'WA:instance:random' instead of '2@...' auth data")
                    print("   This is NOT normal Evolution API behavior")
                    print("\nPOSSIBLE CAUSES:")
                    print("   1. Our deployment is missing Baileys connection logic")
                    print("   2. Silent WebSocket connection failure with fallback")
                    print("   3. Demo/mock mode enabled in our configuration")
                    print("   4. Outdated or modified Evolution API build")
                else:
                    print("⚠️  UNEXPECTED RESPONSE FORMAT")

            # Clean up
            try:
                await session.delete(f"{base_url}/instance/delete/{instance_name}", headers=headers)
            except:
                pass

if __name__ == "__main__":
    asyncio.run(verify_response())
