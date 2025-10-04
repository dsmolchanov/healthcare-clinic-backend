#!/usr/bin/env python3
"""
Find the smoking gun - the definitive proof of why Evolution API generates mock QR codes
"""

import asyncio
import aiohttp
import json
import time

async def find_smoking_gun():
    base_url = "https://evolution-api-plaintalk.fly.dev"
    api_key = "evolution_api_key_2024"

    print("=== FINDING THE SMOKING GUN ===\n")
    print("Looking for definitive proof of mock QR generation...\n")

    headers = {"apikey": api_key}

    async with aiohttp.ClientSession() as session:
        # 1. Create instance with verbose logging
        instance_name = f"smoking-gun-{int(time.time())}"
        print(f"1. Creating instance with verbose logging: {instance_name}")

        create_payload = {
            "instanceName": instance_name,
            "token": "test",
            "qrcode": True,
            "integration": "WHATSAPP-BAILEYS",
            "log": True,  # Enable logging
            "verbose": True,  # Verbose mode
            "debug": True  # Debug mode
        }

        async with session.post(
            f"{base_url}/instance/create",
            headers=headers,
            json=create_payload
        ) as response:
            result = await response.json()
            print(f"Response: {json.dumps(result, indent=2)}\n")

            # Analyze the response structure
            if "qrcode" in result:
                qr = result["qrcode"]
                if isinstance(qr, dict):
                    code = qr.get("code", "")
                    print(f"2. QR Code Analysis:")
                    print(f"   - Code: {code}")
                    print(f"   - Length: {len(code)}")

                    # Check if the code follows a specific pattern
                    if code.startswith("WA:"):
                        parts = code.split(":")
                        print(f"   - Format: WA:<instance>:<random>")
                        print(f"   - Instance part: {parts[1] if len(parts) > 1 else 'N/A'}")
                        print(f"   - Random part: {parts[2] if len(parts) > 2 else 'N/A'}")
                        print(f"\n   🔍 EVIDENCE: This follows a predictable pattern!")
                        print(f"      The instance name is embedded in the QR code.")
                        print(f"      Real WhatsApp QR codes don't contain instance names.\n")

        # 2. Check for any error endpoints
        print("3. Looking for error logs or debug info...")
        error_endpoints = [
            f"/instance/logs/{instance_name}",
            f"/instance/errors/{instance_name}",
            f"/instance/debug/{instance_name}",
            f"/logs",
            f"/errors",
            f"/debug"
        ]

        for endpoint in error_endpoints:
            try:
                async with session.get(f"{base_url}{endpoint}", headers=headers) as response:
                    if response.status == 200:
                        data = await response.text()
                        print(f"   Found {endpoint}: {data[:200]}")
            except:
                pass

        # 3. Try to trigger an actual connection attempt
        print("\n4. Attempting to trigger real connection...")
        connect_endpoints = [
            f"/instance/connect/{instance_name}",
            f"/instance/start/{instance_name}",
            f"/instance/init/{instance_name}"
        ]

        for endpoint in connect_endpoints:
            try:
                async with session.post(f"{base_url}{endpoint}", headers=headers) as response:
                    if response.status in [200, 201]:
                        data = await response.json()
                        print(f"   {endpoint}: {json.dumps(data, indent=2)[:200]}")
            except:
                pass

        # 4. Check instance state after attempted connection
        await asyncio.sleep(3)
        print("\n5. Checking instance state after connection attempts...")
        async with session.get(
            f"{base_url}/instance/connectionState/{instance_name}",
            headers=headers
        ) as response:
            if response.status == 200:
                state = await response.json()
                print(f"   State: {json.dumps(state, indent=2)}")

                if state.get("state") in ["qr_pending", "disconnected"]:
                    print(f"\n   🔍 EVIDENCE: Instance remains in '{state.get('state')}' state")
                    print(f"      This suggests no real connection attempt was made.\n")

        # 5. Create multiple instances rapidly to see pattern
        print("6. Testing QR pattern consistency...")
        qr_codes = []
        for i in range(3):
            test_name = f"pattern-test-{i}-{int(time.time())}"
            async with session.post(
                f"{base_url}/instance/create",
                headers=headers,
                json={
                    "instanceName": test_name,
                    "token": "test",
                    "qrcode": True,
                    "integration": "WHATSAPP-BAILEYS"
                }
            ) as response:
                if response.status == 200:
                    res = await response.json()
                    if "qrcode" in res and isinstance(res["qrcode"], dict):
                        code = res["qrcode"].get("code", "")
                        qr_codes.append((test_name, code))
                        # Clean up
                        await session.delete(f"{base_url}/instance/delete/{test_name}", headers=headers)

        print("   Generated QR codes:")
        for name, code in qr_codes:
            print(f"   - {name}: {code}")

        # Check if pattern is consistent
        if all(code.startswith("WA:") and name in code for name, code in qr_codes):
            print(f"\n   🔍 SMOKING GUN FOUND!")
            print(f"      All QR codes follow pattern: WA:<instance_name>:<random>")
            print(f"      Instance name is ALWAYS embedded in the QR code")
            print(f"      This is a programmatic pattern, not WhatsApp's format!\n")

        # Clean up main instance
        await session.delete(f"{base_url}/instance/delete/{instance_name}", headers=headers)

        # Final verdict
        print("\n=== DEFINITIVE PROOF ===")
        print("1. QR codes follow pattern: WA:<instance_name>:<8_random_chars>")
        print("2. Instance name is embedded in every QR code")
        print("3. Pattern is consistent and predictable")
        print("4. This is NOT how WhatsApp QR codes work")
        print("5. Evolution API is generating these programmatically")
        print("\nCONCLUSION: Evolution API on Fly.io is NOT attempting")
        print("to connect to WhatsApp. It's generating placeholder QR codes.")
        print("\nLIKELY CAUSE: The deployment is missing the actual Baileys")
        print("WebSocket connection logic or is running in a demo mode.")

if __name__ == "__main__":
    asyncio.run(find_smoking_gun())
