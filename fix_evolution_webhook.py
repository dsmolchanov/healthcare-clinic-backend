#!/usr/bin/env python3
"""
Fix Evolution API webhook configuration
"""

import asyncio
import aiohttp
import json
import os

async def fix_evolution_webhook():
    """Configure webhook for Evolution API instance"""

    evolution_url = os.getenv("EVOLUTION_SERVER_URL", "https://evolution-api-prod.fly.dev")
    evolution_key = os.getenv("EVOLUTION_API_KEY", "evolution_api_key_2024")
    webhook_url = "https://healthcare-clinic-backend.fly.dev/webhooks/whatsapp"

    print("=" * 60)
    print("Fixing Evolution API Webhook Configuration")
    print("=" * 60)
    print(f"\nEvolution API URL: {evolution_url}")
    print(f"Target Webhook URL: {webhook_url}")

    async with aiohttp.ClientSession() as session:
        headers = {"apikey": evolution_key}

        # Step 1: Get all instances
        print("\n1. Getting Evolution instances...")
        try:
            async with session.get(
                f"{evolution_url}/instance/fetchInstances",
                headers=headers
            ) as response:
                instances = await response.json()
                print(f"   Found {len(instances) if isinstance(instances, list) else 0} instances")

                if not instances:
                    print("   ✗ No instances found. Need to create one first.")
                    return

                # Get the first instance
                instance_data = instances[0] if isinstance(instances, list) else instances

                # Try multiple ways to get instance name
                instance_name = None
                if isinstance(instance_data, dict):
                    # Try nested structure first
                    if 'instance' in instance_data and 'instanceName' in instance_data['instance']:
                        instance_name = instance_data['instance']['instanceName']
                    elif 'instance' in instance_data and 'name' in instance_data['instance']:
                        instance_name = instance_data['instance']['name']
                    elif 'instanceName' in instance_data:
                        instance_name = instance_data['instanceName']
                    elif 'name' in instance_data:
                        instance_name = instance_data['name']

                if not instance_name:
                    print("   ✗ Could not determine instance name from response")
                    print(f"   Response structure: {json.dumps(instance_data, indent=2)}")
                    return

                print(f"   Instance name: {instance_name}")

        except Exception as e:
            print(f"   ✗ Error getting instances: {e}")
            return

        # Step 2: Set webhook for the instance
        print(f"\n2. Setting webhook for instance '{instance_name}'...")
        try:
            webhook_payload = {
                "webhook": {
                    "url": webhook_url,
                    "enabled": True,
                    "events": [
                        "MESSAGE_RECEIVED",
                        "MESSAGE_UPDATE",
                        "MESSAGE_DELETE",
                        "CONNECTION_UPDATE",
                        "PRESENCE_UPDATE"
                    ]
                }
            }

            # Try the standard webhook set endpoint
            async with session.put(
                f"{evolution_url}/instance/webhook/{instance_name}",
                headers=headers,
                json=webhook_payload
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    print(f"   ✓ Webhook configured successfully!")
                    print(f"   Response: {json.dumps(result, indent=2)}")
                else:
                    # Try alternative endpoint
                    print(f"   First attempt failed with status {response.status}, trying alternative...")

                    # Alternative: Try settings endpoint
                    settings_payload = {
                        "webhook": webhook_url,
                        "webhook_enabled": True,
                        "webhook_events": [
                            "message",
                            "message.any",
                            "connection.update"
                        ]
                    }

                    async with session.post(
                        f"{evolution_url}/instance/settings/{instance_name}",
                        headers=headers,
                        json=settings_payload
                    ) as alt_response:
                        if alt_response.status == 200:
                            result = await alt_response.json()
                            print(f"   ✓ Webhook configured via settings!")
                        else:
                            error = await alt_response.text()
                            print(f"   ✗ Failed to set webhook: {error}")

        except Exception as e:
            print(f"   ✗ Error setting webhook: {e}")
            return

        # Step 3: Verify webhook configuration
        print(f"\n3. Verifying webhook configuration...")
        try:
            async with session.get(
                f"{evolution_url}/instance/fetchInstances",
                headers=headers
            ) as response:
                instances = await response.json()

                if isinstance(instances, list) and instances:
                    instance = instances[0]
                    current_webhook = instance.get('webhook', {}).get('url', '')

                    if current_webhook == webhook_url:
                        print(f"   ✓ Webhook verified: {current_webhook}")
                    else:
                        print(f"   ⚠️  Webhook mismatch:")
                        print(f"      Expected: {webhook_url}")
                        print(f"      Current: {current_webhook}")

        except Exception as e:
            print(f"   ✗ Error verifying webhook: {e}")

        # Step 4: Test webhook with a ping
        print(f"\n4. Testing webhook endpoint...")
        try:
            test_payload = {
                "instanceName": instance_name,
                "message": {
                    "text": "Test message",
                    "from": "1234567890@s.whatsapp.net",
                    "pushName": "Test User"
                }
            }

            async with session.post(
                webhook_url,
                json=test_payload,
                timeout=5
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    print(f"   ✓ Webhook test successful: {result}")
                else:
                    print(f"   ⚠️  Webhook returned status {response.status}")

        except Exception as e:
            print(f"   ✗ Error testing webhook: {e}")

    print("\n" + "=" * 60)
    print("Next Steps:")
    print("=" * 60)
    print("""
1. Send a WhatsApp message to test the integration
2. Check logs: fly logs -a healthcare-clinic-backend
3. Monitor Evolution logs: fly logs -a evolution-api-prod

If messages still don't work:
- Verify WhatsApp instance is connected (QR code scanned)
- Check if Evolution API is receiving messages
- Ensure webhook URL is accessible from Evolution API
""")

if __name__ == "__main__":
    asyncio.run(fix_evolution_webhook())