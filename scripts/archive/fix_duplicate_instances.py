#!/usr/bin/env python3
"""
Fix duplicate Evolution instances for a clinic.

This script:
1. Fetches all instances for a clinic
2. Deletes ALL duplicate instances
3. Cleans up database records
4. Creates ONE fresh instance
"""

import os
import asyncio
import httpx
from supabase import create_client, Client
from datetime import datetime

# Configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "https://evolution-api-prod.fly.dev")
EVOLUTION_GLOBAL_KEY = os.getenv("EVOLUTION_GLOBAL_KEY", "plaintalk-global-key")

async def main():
    clinic_id = input("Enter clinic ID to fix: ").strip()

    if not clinic_id:
        print("❌ Clinic ID required")
        return

    print(f"\n🔍 Fixing instances for clinic: {clinic_id}")

    # Initialize Supabase
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    # Step 1: Fetch all instances from Evolution API
    print("\n📡 Fetching instances from Evolution API...")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{EVOLUTION_API_URL}/instance/fetchInstances",
            headers={"apikey": EVOLUTION_GLOBAL_KEY}
        )

        if response.status_code != 200:
            print(f"❌ Failed to fetch instances: {response.status_code}")
            return

        all_instances = response.json()

        # Filter instances for this clinic
        clinic_instances = [
            inst for inst in all_instances
            if inst.get("instance", {}).get("instanceName", "").startswith(f"clinic-{clinic_id}")
        ]

        print(f"✅ Found {len(clinic_instances)} instances for clinic {clinic_id}:")
        for inst in clinic_instances:
            name = inst["instance"]["instanceName"]
            status = inst["instance"]["status"]
            print(f"   - {name} ({status})")

        if not clinic_instances:
            print("ℹ️  No instances found to clean up")
            return

        # Step 2: Delete ALL instances from Evolution API
        print(f"\n🗑️  Deleting {len(clinic_instances)} instances from Evolution API...")
        deleted_count = 0

        for inst in clinic_instances:
            instance_name = inst["instance"]["instanceName"]
            try:
                delete_response = await client.delete(
                    f"{EVOLUTION_API_URL}/instance/delete/{instance_name}",
                    headers={"apikey": EVOLUTION_GLOBAL_KEY}
                )

                if delete_response.status_code in [200, 404]:
                    print(f"   ✅ Deleted: {instance_name}")
                    deleted_count += 1
                else:
                    print(f"   ⚠️  Failed to delete {instance_name}: {delete_response.status_code}")
            except Exception as e:
                print(f"   ❌ Error deleting {instance_name}: {e}")

        print(f"\n✅ Deleted {deleted_count} instances from Evolution API")

    # Step 3: Clean up database records
    print("\n🧹 Cleaning up database records...")

    try:
        # Delete from whatsapp_integrations
        result = supabase.table("whatsapp_integrations") \
            .delete() \
            .eq("clinic_id", clinic_id) \
            .execute()

        print(f"   ✅ Deleted {len(result.data)} records from whatsapp_integrations")
    except Exception as e:
        print(f"   ⚠️  Error cleaning whatsapp_integrations: {e}")

    print("\n✅ Cleanup complete!")
    print("\n📋 Next steps:")
    print("1. Go to the UI")
    print("2. Create a NEW WhatsApp integration")
    print("3. Scan the QR code")
    print("4. Verify the integration shows as 'connected'")

if __name__ == "__main__":
    asyncio.run(main())
