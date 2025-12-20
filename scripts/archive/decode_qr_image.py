#!/usr/bin/env python3
"""
Decode the actual QR code image content to see what Evolution API is encoding
"""

import asyncio
import aiohttp
import json
import base64
from PIL import Image
from io import BytesIO
import time
import qrcode

async def decode_qr():
    base_url = "https://evolution-api-plaintalk.fly.dev"
    api_key = "evolution_api_key_2024"

    print("=== DECODING QR IMAGE CONTENT ===\n")

    headers = {"apikey": api_key}

    async with aiohttp.ClientSession() as session:
        # Create instance
        instance_name = f"decode-test-{int(time.time())}"
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

            if "qrcode" in result and isinstance(result["qrcode"], dict):
                qr_obj = result["qrcode"]

                # Get both fields
                code_field = qr_obj.get("code", "")
                base64_field = qr_obj.get("base64", "")

                print(f"1. CODE FIELD (pairing code):")
                print(f"   Value: {code_field}")
                print(f"   Length: {len(code_field)}\n")

                print(f"2. BASE64 FIELD (QR image):")
                print(f"   Length: {len(base64_field)}")

                if base64_field.startswith("data:image"):
                    # Extract base64 data
                    base64_img = base64_field.split("base64,")[1]

                    # Decode to image
                    img_bytes = base64.b64decode(base64_img)
                    img = Image.open(BytesIO(img_bytes))

                    # Save for inspection
                    img_path = f"/tmp/qr_{instance_name}.png"
                    img.save(img_path)
                    print(f"   Saved to: {img_path}\n")

                    # Try using cv2 to decode
                    try:
                        import cv2
                        import numpy as np

                        # Convert PIL image to cv2
                        img_array = np.array(img)
                        detector = cv2.QRCodeDetector()
                        data, bbox, straight_qrcode = detector.detectAndDecode(img_array)

                        if data:
                            print(f"   🔍 QR DECODED WITH CV2:")
                            print(f"      Content: {data}")
                            print(f"      Length: {len(data)} characters")

                            # Check what format it is
                            if data == code_field:
                                print(f"      ❌ QR encodes the same pairing code!")
                                print(f"      This explains why WhatsApp rejects it.")
                            elif len(data) > 100:
                                print(f"      ✅ This looks like real WhatsApp data!")
                            else:
                                print(f"      ⚠️  Unexpected format")
                        else:
                            print(f"   ⚠️  CV2 couldn't decode QR")
                    except ImportError:
                        print(f"   ℹ️  Install opencv-python to decode: pip install opencv-python")

                    # Also try to create a QR from the code field to compare
                    print(f"\n3. CREATING QR FROM CODE FIELD FOR COMPARISON:")
                    test_qr = qrcode.QRCode(version=1, box_size=10, border=5)
                    test_qr.add_data(code_field)
                    test_qr.make(fit=True)
                    test_img = test_qr.make_image(fill_color="black", back_color="white")

                    # Convert to same format for comparison
                    test_buffer = BytesIO()
                    test_img.save(test_buffer, format='PNG')
                    test_bytes = test_buffer.getvalue()

                    print(f"   Generated QR size: {len(test_bytes)} bytes")
                    print(f"   Evolution QR size: {len(img_bytes)} bytes")

                    if abs(len(test_bytes) - len(img_bytes)) < 100:
                        print(f"   ⚠️  Sizes are very similar!")
                        print(f"   This suggests Evolution API might be encoding")
                        print(f"   the pairing code into the QR image!")

                print(f"\n=== CRITICAL FINDING ===")
                print(f"If the QR image encodes '{code_field}',")
                print(f"then Evolution API is generating a QR of the pairing code,")
                print(f"NOT a real WhatsApp authentication QR code.")
                print(f"\nThis would explain why WhatsApp rejects it as invalid!")

            # Clean up
            await session.delete(f"{base_url}/instance/delete/{instance_name}", headers=headers)

if __name__ == "__main__":
    asyncio.run(decode_qr())
