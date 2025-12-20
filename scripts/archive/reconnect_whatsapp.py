#!/usr/bin/env python3
"""
Reconnect existing WhatsApp instance in Evolution API
"""

import asyncio
import aiohttp
import json

async def reconnect_whatsapp():
    """Reconnect the existing WhatsApp instance"""

    evolution_url = "https://evolution-api-prod.fly.dev"
    api_key = "evolution_api_key_2024"

    # The instance name we found in the auth folder
    instance_name = "clinic-3e411ecb-3411-4add-91e2-8fa897310cb0-1757905315621"

    print("=" * 60)
    print("Reconnecting WhatsApp Instance")
    print("=" * 60)
    print(f"\nEvolution API: {evolution_url}")
    print(f"Instance: {instance_name}")

    async with aiohttp.ClientSession() as session:
        headers = {"apikey": api_key, "Content-Type": "application/json"}

        # First, check current instances
        print("\n1. Checking current instances...")
        try:
            async with session.get(
                f"{evolution_url}/instance/fetchInstances",
                headers=headers
            ) as response:
                instances = await response.json()
                print(f"   Current instances: {len(instances)}")
                if instances:
                    for inst in instances:
                        print(f"   - {inst.get('instance', {}).get('instanceName', 'unknown')}")
        except Exception as e:
            print(f"   Error fetching instances: {e}")

        # Connect the existing instance
        print(f"\n2. Connecting instance '{instance_name}'...")
        try:
            # Evolution API requires creating/connecting an instance
            payload = {
                "instanceName": instance_name,
                "qrcode": True,
                "integration": "WHATSAPP-BAILEYS"
            }

            async with session.post(
                f"{evolution_url}/instance/create",
                headers=headers,
                json=payload
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    print(f"   ✓ Instance connected successfully!")
                    print(f"   Response: {json.dumps(result, indent=2)}")

                    # If QR code is needed
                    if result.get("qrcode"):
                        print("\n   ⚠️  QR Code scan required!")
                        print("   The instance needs to be re-authenticated.")
                    else:
                        print("\n   ✓ Instance reconnected with existing session!")
                else:
                    error = await response.text()
                    print(f"   ✗ Failed to connect: Status {response.status}")
                    print(f"   Error: {error}")

        except Exception as e:
            print(f"   ✗ Error connecting instance: {e}")

        # Check connection status
        print(f"\n3. Verifying connection...")
        await asyncio.sleep(2)  # Wait for connection to establish

        try:
            async with session.get(
                f"{evolution_url}/instance/connectionState/{instance_name}",
                headers=headers
            ) as response:
                if response.status == 200:
                    state = await response.json()
                    print(f"   Connection state: {json.dumps(state, indent=2)}")
                else:
                    print(f"   Could not get connection state: {response.status}")
        except Exception as e:
            print(f"   Error checking state: {e}")

        # Check instances again
        print(f"\n4. Final instance check...")
        try:
            async with session.get(
                f"{evolution_url}/instance/fetchInstances",
                headers=headers
            ) as response:
                instances = await response.json()
                print(f"   Total instances: {len(instances)}")
                for inst in instances:
                    inst_data = inst.get('instance', {})
                    print(f"   - {inst_data.get('instanceName', 'unknown')}: {inst_data.get('status', 'unknown')}")
        except Exception as e:
            print(f"   Error: {e}")

    print("\n" + "=" * 60)
    print("Next Steps:")
    print("=" * 60)
    print("""
1. If QR code is required:
   - Get the QR code: GET /instance/connect/{instance_name}
   - Scan with WhatsApp on your phone

2. Test the connection:
   - Send a test message through WhatsApp
   - Check logs: fly logs -a healthcare-clinic-backend

3. Verify webhook is working:
   - Messages should now reach the backend
""")

if __name__ == "__main__":
    asyncio.run(reconnect_whatsapp())