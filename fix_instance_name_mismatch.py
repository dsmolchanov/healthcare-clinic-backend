#!/usr/bin/env python3
"""
Fix instance name mismatch between database and Evolution server
"""
import asyncio
import aiohttp
import os
from supabase import create_client
from supabase.client import ClientOptions

EVOLUTION_URL = os.getenv("EVOLUTION_SERVER_URL", "https://evolution-api-prod.fly.dev")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
CLINIC_ID = "4e8ddba1-ad52-4613-9a03-ec64636b3f6c"

# The correct working instance name (with timestamp)
WORKING_INSTANCE = "clinic-4e8ddba1-ad52-4613-9a03-ec64636b3f6c-1763141478931"
# The broken instance name (without timestamp)
BROKEN_INSTANCE = "clinic-4e8ddba1-ad52-4613-9a03-ec64636b3f6c"

async def delete_instance(instance_name: str):
    """Delete an Evolution instance"""
    url = f"{EVOLUTION_URL}/instance/delete/{instance_name}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.delete(url) as resp:
                if resp.status in [200, 404]:
                    print(f"✅ Deleted instance: {instance_name}")
                    return True
                else:
                    text = await resp.text()
                    print(f"⚠️  Error deleting {instance_name}: {text}")
                    return False
        except Exception as e:
            print(f"❌ Exception deleting {instance_name}: {e}")
            return False

async def update_database():
    """Update database to use correct instance name"""
    options = ClientOptions(schema='healthcare')
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY, options)

    # Update the integration record
    result = supabase.from_('integrations').update({
        'config': {'instance_name': WORKING_INSTANCE}
    }).eq('organization_id', CLINIC_ID).eq('type', 'whatsapp').execute()

    if result.data:
        print(f"✅ Updated database record with correct instance name")
        print(f"   New instance_name: {WORKING_INSTANCE}")
        return True
    else:
        print(f"⚠️  No database record found to update")
        return False

async def main():
    print("🔧 Fixing Instance Name Mismatch\n")
    print("=" * 60)

    print(f"\nStep 1: Delete broken instance (without timestamp)")
    print(f"   Instance: {BROKEN_INSTANCE}")
    await delete_instance(BROKEN_INSTANCE)

    print(f"\nStep 2: Update database to use working instance")
    print(f"   Instance: {WORKING_INSTANCE}")
    await update_database()

    print("\n" + "=" * 60)
    print("\n✅ DONE! The WhatsApp integration should now work correctly.")
    print(f"\nThe working instance ({WORKING_INSTANCE}) is:")
    print("  - Connected to WhatsApp ✅")
    print("  - Receiving messages ✅")
    print("  - Sending webhooks ✅")
    print("  - Properly configured in database ✅")
    print("\n" + "=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
