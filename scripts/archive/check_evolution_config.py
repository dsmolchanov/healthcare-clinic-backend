#!/usr/bin/env python3
"""
Check Evolution API configuration and webhook setup
"""

import asyncio
import aiohttp
import json
import os
from app.evolution_api import EvolutionAPIClient
from app.database import create_supabase_client

async def check_evolution_config():
    """Check Evolution API configuration and webhook settings"""

    print("=" * 60)
    print("Evolution API Configuration Check")
    print("=" * 60)

    # Check environment variables
    evolution_url = os.getenv("EVOLUTION_SERVER_URL", "https://evolution-api-prod.fly.dev")
    evolution_key = os.getenv("EVOLUTION_API_KEY", "evolution_api_key_2024")
    webhook_base = os.getenv("WEBHOOK_BASE_URL", "https://healthcare-clinic-backend.fly.dev")

    print(f"\n1. Environment Configuration:")
    print(f"   Evolution API URL: {evolution_url}")
    print(f"   Evolution API Key: {evolution_key[:10]}..." if evolution_key else "   Evolution API Key: NOT SET")
    print(f"   Webhook Base URL: {webhook_base}")
    print(f"   Expected Webhook: {webhook_base}/webhooks/whatsapp")

    # Check database for WhatsApp configurations
    print(f"\n2. Database WhatsApp Configurations:")
    try:
        supabase = create_supabase_client()

        # Get clinics with WhatsApp config
        clinics = supabase.table('clinics').select('id, name, whatsapp_config').execute()

        if clinics.data:
            for clinic in clinics.data:
                if clinic.get('whatsapp_config'):
                    config = clinic['whatsapp_config']
                    print(f"\n   Clinic: {clinic['name']} (ID: {clinic['id']})")
                    print(f"   - Instance Name: {config.get('instance_name', 'NOT SET')}")
                    print(f"   - Evolution URL: {config.get('evolution_api_url', 'NOT SET')}")
                    print(f"   - Has API Key: {'Yes' if config.get('evolution_api_key') else 'No'}")
                    print(f"   - Phone Number: {config.get('phone_number', 'NOT SET')}")
        else:
            print("   No clinics found in database")

    except Exception as e:
        print(f"   Error accessing database: {e}")

    # Test Evolution API connection
    print(f"\n3. Testing Evolution API Connection:")
    try:
        async with EvolutionAPIClient() as client:
            # Try to list instances
            try:
                response = await client._make_request("GET", "/instance/fetchInstances")
                print(f"   ✓ Successfully connected to Evolution API")
                print(f"   Found {len(response) if isinstance(response, list) else 0} instances")

                if isinstance(response, list):
                    for instance in response:
                        instance_name = instance.get('instance', {}).get('name', 'UNKNOWN')
                        state = instance.get('instance', {}).get('state', 'UNKNOWN')
                        print(f"\n   Instance: {instance_name}")
                        print(f"   - State: {state}")

                        # Check webhook configuration
                        webhook_url = instance.get('webhook', {}).get('url', '')
                        print(f"   - Webhook URL: {webhook_url}")

                        if webhook_url and 'healthcare-clinic-backend' in webhook_url:
                            print(f"   - ✓ Webhook pointing to our backend")
                        elif webhook_url:
                            print(f"   - ⚠️  Webhook pointing elsewhere")
                        else:
                            print(f"   - ✗ No webhook configured")

            except Exception as e:
                print(f"   ✗ Failed to connect: {e}")

    except Exception as e:
        print(f"   ✗ Error initializing Evolution client: {e}")

    # Check if our webhook endpoint is accessible
    print(f"\n4. Testing Webhook Endpoint:")
    try:
        async with aiohttp.ClientSession() as session:
            test_url = f"{webhook_base}/webhooks/whatsapp/test"
            async with session.get(test_url, timeout=5) as response:
                if response.status == 200:
                    result = await response.json()
                    print(f"   ✓ Webhook endpoint is accessible: {result}")
                else:
                    print(f"   ✗ Webhook returned status {response.status}")
    except Exception as e:
        print(f"   ✗ Cannot reach webhook endpoint: {e}")

    print("\n" + "=" * 60)
    print("Troubleshooting Steps:")
    print("=" * 60)
    print("""
1. If Evolution instances exist but webhooks aren't configured:
   - Need to update instance webhook settings in Evolution API

2. If no Evolution instances exist:
   - Need to create an instance using the integrations API

3. If webhook endpoint is not accessible:
   - Check if healthcare-clinic-backend is deployed and running
   - Verify the URL is correct

4. To fix webhook configuration, run:
   python fix_evolution_webhook.py
""")

if __name__ == "__main__":
    asyncio.run(check_evolution_config())