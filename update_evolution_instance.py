#!/usr/bin/env python3
"""
Update Evolution API instance name and webhook
This script helps migrate the Evolution API instance to use the correct organization ID
"""

import os
import httpx
import asyncio
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

EVOLUTION_API_URL = "https://evolution-api-prod.fly.dev"
OLD_INSTANCE_NAME = "clinic-3e411ecb-3411-4add-91e2-8fa897310cb0-1757905315621"
NEW_INSTANCE_NAME = "clinic-4e8ddba1-ad52-4613-9a03-ec64636b3f6c-1757905315621"
NEW_WEBHOOK_URL = f"https://healthcare-clinic-backend.fly.dev/webhooks/evolution/{NEW_INSTANCE_NAME}"

# You'll need to set this in your environment or replace with actual API key
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")

async def check_instance_status(instance_name: str):
    """Check if an instance exists and its status"""
    url = f"{EVOLUTION_API_URL}/instance/fetchInstances"
    headers = {"apikey": EVOLUTION_API_KEY} if EVOLUTION_API_KEY else {}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            instances = response.json()

            for instance in instances:
                if instance.get("instance", {}).get("instanceName") == instance_name:
                    print(f"✅ Found instance: {instance_name}")
                    print(f"   Status: {instance.get('instance', {}).get('status')}")
                    print(f"   Owner: {instance.get('instance', {}).get('owner')}")
                    return instance

            print(f"❌ Instance not found: {instance_name}")
            return None

        except Exception as e:
            print(f"❌ Error checking instance: {e}")
            return None

async def update_webhook(instance_name: str, webhook_url: str):
    """Update webhook URL for an instance"""
    url = f"{EVOLUTION_API_URL}/webhook/set/{instance_name}"
    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json"
    } if EVOLUTION_API_KEY else {"Content-Type": "application/json"}

    payload = {
        "url": webhook_url,
        "webhook_by_events": False,
        "webhook_base64": False,
        "events": [
            "QRCODE_UPDATED",
            "MESSAGES_UPSERT",
            "MESSAGES_UPDATE",
            "SEND_MESSAGE",
            "CONNECTION_UPDATE"
        ]
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            result = response.json()
            print(f"✅ Webhook updated successfully")
            print(f"   Instance: {instance_name}")
            print(f"   Webhook URL: {webhook_url}")
            return result

        except Exception as e:
            print(f"❌ Error updating webhook: {e}")
            return None

async def create_new_instance(instance_name: str, webhook_url: str):
    """Create a new Evolution API instance"""
    url = f"{EVOLUTION_API_URL}/instance/create"
    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json"
    } if EVOLUTION_API_KEY else {"Content-Type": "application/json"}

    payload = {
        "instanceName": instance_name,
        "qrcode": True,
        "webhook": webhook_url,
        "webhook_by_events": False,
        "webhook_base64": False,
        "events": [
            "QRCODE_UPDATED",
            "MESSAGES_UPSERT",
            "MESSAGES_UPDATE",
            "SEND_MESSAGE",
            "CONNECTION_UPDATE"
        ]
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            result = response.json()
            print(f"✅ Instance created successfully")
            print(f"   Instance: {instance_name}")
            print(f"   QR Code will be generated for WhatsApp connection")
            return result

        except Exception as e:
            print(f"❌ Error creating instance: {e}")
            return None

async def main():
    print("=" * 80)
    print("Evolution API Instance Migration")
    print("=" * 80)
    print()

    if not EVOLUTION_API_KEY:
        print("⚠️  WARNING: EVOLUTION_API_KEY not set in environment")
        print("   Some operations may fail without authentication")
        print()

    print("Step 1: Checking old instance status...")
    print("-" * 80)
    old_instance = await check_instance_status(OLD_INSTANCE_NAME)
    print()

    print("Step 2: Checking if new instance already exists...")
    print("-" * 80)
    new_instance = await check_instance_status(NEW_INSTANCE_NAME)
    print()

    if new_instance:
        print("Step 3: Updating webhook for existing new instance...")
        print("-" * 80)
        await update_webhook(NEW_INSTANCE_NAME, NEW_WEBHOOK_URL)
        print()
        print("✅ Migration complete! New instance is already set up.")
        print()
        print("Next steps:")
        print("1. The new instance is ready to use")
        print("2. You can delete the old instance if no longer needed")
        print(f"3. Old instance: {OLD_INSTANCE_NAME}")

    elif old_instance:
        print("Step 3: Old instance exists, but new instance doesn't")
        print("-" * 80)
        print()
        print("⚠️  MANUAL ACTION REQUIRED:")
        print()
        print("You need to UPDATE the webhook URL for the old instance:")
        print(f"   Old webhook: {old_instance.get('webhook', {}).get('url', 'N/A')}")
        print(f"   New webhook: {NEW_WEBHOOK_URL}")
        print()
        print("Run this command to update:")
        print()
        print(f"  python3 {__file__} --update-webhook")
        print()
        print("OR create a new instance and re-scan QR code:")
        print(f"  python3 {__file__} --create-new")

    else:
        print("Step 3: Neither instance exists")
        print("-" * 80)
        print()
        print("⚠️  MANUAL ACTION REQUIRED:")
        print()
        print("You need to create a new instance:")
        print(f"  python3 {__file__} --create-new")

    print()
    print("=" * 80)

if __name__ == "__main__":
    import sys

    if "--update-webhook" in sys.argv:
        asyncio.run(update_webhook(OLD_INSTANCE_NAME, NEW_WEBHOOK_URL))
    elif "--create-new" in sys.argv:
        asyncio.run(create_new_instance(NEW_INSTANCE_NAME, NEW_WEBHOOK_URL))
    else:
        asyncio.run(main())
