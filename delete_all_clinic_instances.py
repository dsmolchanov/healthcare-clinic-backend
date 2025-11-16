#!/usr/bin/env python3
"""
Delete all Evolution instances for the clinic
"""
import asyncio
import aiohttp
import os

EVOLUTION_URL = os.getenv("EVOLUTION_SERVER_URL", "https://evolution-api-prod.fly.dev")
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

async def main():
    print("🧹 Deleting All Clinic Evolution Instances\n")
    print("=" * 60)

    # All instances to delete
    instances_to_delete = [
        f"clinic-{CLINIC_ID}",  # The main one we just created
        f"clinic-{CLINIC_ID}-1763130965955",
        f"clinic-{CLINIC_ID}-1763132737941",
        f"clinic-{CLINIC_ID}-1759348170665",
        f"clinic-{CLINIC_ID}-1760989115152",
        f"clinic-{CLINIC_ID}-1760991930032",
        f"clinic-{CLINIC_ID}-1760992328639",
        f"clinic-{CLINIC_ID}-1763129689903",
        f"clinic-{CLINIC_ID}-1763129934831",
    ]

    print(f"\nDeleting {len(instances_to_delete)} instances...\n")

    for instance in instances_to_delete:
        await delete_instance(instance)
        await asyncio.sleep(0.3)  # Rate limiting

    print("\n" + "=" * 60)
    print("\n✅ All instances deleted!")
    print("\nYou can now test the UI flow to create a fresh instance.")
    print("\n" + "=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
