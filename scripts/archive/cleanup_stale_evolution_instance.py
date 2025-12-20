#!/usr/bin/env python3
"""
Cleanup stale Evolution API instance that keeps pinging QR codes.
This removes the instance data so it can be re-initialized cleanly.
"""
import os
import requests
import sys

# Evolution API configuration
EVOLUTION_API_URL = os.getenv('EVOLUTION_API_URL', 'https://evolution-api-prod.fly.dev')
EVOLUTION_API_KEY = os.getenv('EVOLUTION_API_KEY', 'B6D711FCDE4D4FD5936544120E713976')

# The problematic instance
STALE_INSTANCE = "clinic-3e411ecb-3411-4add-91e2-8fa897310cb0-1757905315621"

def delete_instance(instance_name: str):
    """Delete an Evolution API instance"""
    url = f"{EVOLUTION_API_URL}/instance/delete/{instance_name}"
    headers = {
        "apikey": EVOLUTION_API_KEY
    }

    print(f"🗑️  Deleting instance: {instance_name}")
    print(f"URL: {url}")

    response = requests.delete(url, headers=headers)

    if response.status_code == 200:
        print(f"✅ Successfully deleted instance: {instance_name}")
        return True
    else:
        print(f"❌ Failed to delete instance: {response.status_code}")
        print(f"Response: {response.text}")
        return False

def main():
    print("=" * 60)
    print("Evolution API Instance Cleanup")
    print("=" * 60)

    # Delete the stale instance
    success = delete_instance(STALE_INSTANCE)

    if success:
        print("\n✅ Cleanup complete!")
        print("\nNext steps:")
        print("1. The instance has been removed from Evolution API")
        print("2. The next time this organization tries to connect,")
        print("   a fresh QR code will be generated")
        print("3. User should scan the NEW QR code in their WhatsApp app")
    else:
        print("\n❌ Cleanup failed!")
        sys.exit(1)

if __name__ == "__main__":
    main()
