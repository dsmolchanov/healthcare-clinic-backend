#!/usr/bin/env python3
"""
Clean up duplicate Evolution instances and reconnect properly
"""
import asyncio
import aiohttp
import os
from supabase import create_client
import secrets
import base64

EVOLUTION_URL = os.getenv("EVOLUTION_SERVER_URL", "https://evolution-api-prod.fly.dev")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

CLINIC_ID = "4e8ddba1-ad52-4613-9a03-ec64636b3f6c"

async def delete_instance(instance_name: str):
    """Delete an Evolution instance"""
    url = f"{EVOLUTION_URL}/instance/delete/{instance_name}"

    async with aiohttp.ClientSession() as session:
        try:
            async with session.delete(url) as resp:
                if resp.status in [200, 404]:
                    print(f"✅ Deleted: {instance_name}")
                    return True
                else:
                    text = await resp.text()
                    print(f"⚠️  Error deleting {instance_name}: {text}")
                    return False
        except Exception as e:
            print(f"❌ Exception deleting {instance_name}: {e}")
            return False

async def create_fresh_instance(clinic_id: str):
    """Create a brand new Evolution instance with proper configuration"""

    # Generate new instance name with timestamp
    import time
    timestamp = int(time.time())
    instance_name = f"clinic-{clinic_id}"  # Simple name without timestamp

    # Generate webhook token
    webhook_token = base64.urlsafe_b64encode(secrets.token_bytes(24)).decode('utf-8').rstrip('=')
    webhook_url = f"https://healthcare-clinic-backend.fly.dev/webhooks/evolution/whatsapp/{webhook_token}"

    print(f"\n📝 Creating new instance:")
    print(f"   Instance: {instance_name}")
    print(f"   Webhook: {webhook_url}")

    url = f"{EVOLUTION_URL}/instance/create"

    payload = {
        "instanceName": instance_name,
        "qrcode": True,
        "integration": "WHATSAPP-BAILEYS",
        "webhook": webhook_url,
        "webhook_by_events": False,
        "webhook_base64": False,
        "reject_call": False,
        "msg_call": "",
        "groups_ignore": True,
        "always_online": False,
        "read_messages": False,
        "read_status": False,
        "sync_full_history": False
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload) as resp:
                if resp.status in [200, 201]:
                    data = await resp.json()
                    print(f"✅ Instance created successfully")

                    # Save to database
                    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

                    # First, clean up old integrations
                    existing = supabase.table("integrations").select("*").eq(
                        "clinic_id", clinic_id
                    ).eq("integration_type", "whatsapp").execute()

                    if existing.data:
                        for old in existing.data:
                            print(f"🗑️  Removing old integration: {old['id']}")
                            supabase.table("integrations").delete().eq("id", old['id']).execute()

                    # Create new integration record
                    integration_data = {
                        "clinic_id": clinic_id,
                        "integration_type": "whatsapp",
                        "provider": "evolution",
                        "status": "pending",
                        "is_enabled": True,
                        "display_name": "WhatsApp Integration",
                        "description": "Evolution API WhatsApp integration",
                        "config": {
                            "instance_name": instance_name,
                            "evolution_server_url": EVOLUTION_URL
                        },
                        "webhook_token": webhook_token,
                        "webhook_url": webhook_url
                    }

                    result = supabase.table("integrations").insert(integration_data).execute()

                    print(f"\n✅ Database record created")
                    print(f"   Integration ID: {result.data[0]['id']}")
                    print(f"\n🔗 Next step: Scan QR code at:")
                    print(f"   {EVOLUTION_URL}/instance/connect/{instance_name}")

                    return {
                        "instance_name": instance_name,
                        "webhook_token": webhook_token,
                        "qr_url": f"{EVOLUTION_URL}/instance/connect/{instance_name}"
                    }
                else:
                    text = await resp.text()
                    print(f"❌ Error creating instance: {text}")
                    return None
        except Exception as e:
            print(f"❌ Exception: {e}")
            return None

async def main():
    print("🧹 Evolution Instance Cleanup & Reconnect\n")
    print("=" * 60)

    # List all duplicate instances to delete
    duplicate_instances = [
        f"clinic-{CLINIC_ID}-1763130965955",
        f"clinic-{CLINIC_ID}-1763132737941",
        f"clinic-{CLINIC_ID}-1759348170665",
        f"clinic-{CLINIC_ID}-1760989115152",
        f"clinic-{CLINIC_ID}-1760991930032",
        f"clinic-{CLINIC_ID}-1760992328639",
        f"clinic-{CLINIC_ID}-1763129689903",
        f"clinic-{CLINIC_ID}-1763129934831",
    ]

    print(f"\nStep 1: Deleting {len(duplicate_instances)} duplicate instances...\n")

    for instance in duplicate_instances:
        await delete_instance(instance)
        await asyncio.sleep(0.5)  # Rate limiting

    # Also delete the base instance name
    await delete_instance(f"clinic-{CLINIC_ID}")

    print("\n" + "=" * 60)
    print("\nStep 2: Creating fresh instance with proper configuration...\n")

    result = await create_fresh_instance(CLINIC_ID)

    if result:
        print("\n" + "=" * 60)
        print("\n✅ SUCCESS! Next steps:")
        print("\n1. Open this URL in your browser:")
        print(f"   {result['qr_url']}")
        print("\n2. Scan the QR code with WhatsApp")
        print("\n3. Wait for 'Connected' status")
        print("\n4. Test by sending a message to the WhatsApp number")
        print("\n" + "=" * 60)
    else:
        print("\n❌ Failed to create instance. Check errors above.")

if __name__ == "__main__":
    asyncio.run(main())
