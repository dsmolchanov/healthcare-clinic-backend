#!/usr/bin/env python3
"""
Restore Evolution WhatsApp Instance

This script helps restore a failed Evolution API instance by:
1. Checking database configuration
2. Deleting invalid instances
3. Creating a new instance
4. Providing QR code for authentication
"""

import os
import sys
import json
import requests
from typing import Optional, Dict, Any
from app.db.supabase_client import get_supabase_client

# Evolution API configuration
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "https://evolution-api-prod.fly.dev")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")  # Set this if you have an API key


def get_clinic_integration(clinic_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Get WhatsApp integration from database."""
    print("📥 Fetching WhatsApp integration from database...")

    supabase = get_supabase_client()
    query = supabase.schema("healthcare").table("integrations").select("*").eq("type", "whatsapp")

    if clinic_id:
        query = query.eq("clinic_id", clinic_id)

    result = query.execute()

    if result.data and len(result.data) > 0:
        integration = result.data[0]
        print(f"✅ Found integration: {integration['id']}")
        print(f"   Clinic ID: {integration.get('clinic_id')}")
        print(f"   Instance: {integration.get('config', {}).get('instance')}")
        print(f"   Webhook Token: {integration.get('webhook_token', 'N/A')[:16]}...")
        return integration
    else:
        print("❌ No WhatsApp integration found in database")
        return None


def list_evolution_instances() -> list:
    """List all instances in Evolution API."""
    print(f"\n📋 Fetching instances from Evolution API...")

    headers = {}
    if EVOLUTION_API_KEY:
        headers["apikey"] = EVOLUTION_API_KEY

    try:
        response = requests.get(f"{EVOLUTION_API_URL}/instance/fetchInstances", headers=headers)
        response.raise_for_status()
        instances = response.json()

        print(f"✅ Found {len(instances)} instances")
        for inst in instances:
            instance_name = inst.get("instance", {}).get("instanceName", "unknown")
            status = inst.get("instance", {}).get("status", "unknown")
            print(f"   - {instance_name}: {status}")

        return instances
    except Exception as e:
        print(f"❌ Error fetching instances: {e}")
        return []


def delete_instance(instance_name: str) -> bool:
    """Delete an instance from Evolution API."""
    print(f"\n🗑️  Deleting instance: {instance_name}")

    headers = {}
    if EVOLUTION_API_KEY:
        headers["apikey"] = EVOLUTION_API_KEY

    try:
        response = requests.delete(
            f"{EVOLUTION_API_URL}/instance/delete/{instance_name}",
            headers=headers
        )
        response.raise_for_status()
        print(f"✅ Deleted {instance_name}")
        return True
    except Exception as e:
        print(f"❌ Error deleting {instance_name}: {e}")
        return False


def create_instance(instance_name: str, webhook_url: str) -> Optional[Dict[str, Any]]:
    """Create a new instance in Evolution API."""
    print(f"\n🆕 Creating new instance: {instance_name}")

    headers = {"Content-Type": "application/json"}
    if EVOLUTION_API_KEY:
        headers["apikey"] = EVOLUTION_API_KEY

    payload = {
        "instanceName": instance_name,
        "token": instance_name,  # Optional token for this instance
        "qrcode": True,
        "number": "",
        "webhook": webhook_url,
        "webhook_by_events": False,
        "events": [
            "QRCODE_UPDATED",
            "MESSAGES_UPSERT",
            "MESSAGES_UPDATE",
            "CONNECTION_UPDATE"
        ]
    }

    try:
        response = requests.post(
            f"{EVOLUTION_API_URL}/instance/create",
            headers=headers,
            json=payload
        )
        response.raise_for_status()
        result = response.json()

        print(f"✅ Instance created successfully")

        # Check if there's a QR code
        if "qrcode" in result:
            qr_data = result["qrcode"]
            if "base64" in qr_data:
                print(f"\n📱 QR Code ready!")
                print(f"   Base64 length: {len(qr_data['base64'])}")
                print(f"\n   Visit the Evolution Manager to scan:")
                print(f"   🔗 {EVOLUTION_API_URL}/manager")
            elif "code" in qr_data:
                print(f"\n📱 QR Code: {qr_data['code']}")

        return result
    except Exception as e:
        print(f"❌ Error creating instance: {e}")
        if hasattr(e, 'response'):
            print(f"   Response: {e.response.text}")
        return None


def cleanup_test_instances():
    """Delete all test instances."""
    test_instances = [
        "complete-test-1757901945",
        "final-rpc-test-1757903110",
        "frontend-format-test-1757903854",
        "frontend-test-camel",
        "test-debug-422",
        "test-final-1757902500",
        "test-from-curl",
        "test-instance",
        "test-snakecase",
    ]

    print(f"\n🧹 Cleaning up {len(test_instances)} test instances...")

    for instance in test_instances:
        delete_instance(instance)


def main():
    """Main restoration flow."""
    print("=" * 60)
    print("Evolution WhatsApp Instance Restoration")
    print("=" * 60)

    # Step 1: Check database
    integration = get_clinic_integration()
    if not integration:
        print("\n❌ Cannot proceed without database integration")
        sys.exit(1)

    # Step 2: List current instances
    instances = list_evolution_instances()

    # Step 3: Ask what to do
    print("\n" + "=" * 60)
    print("Options:")
    print("=" * 60)
    print("1. Delete failed instance and create new one")
    print("2. Clean up all test instances")
    print("3. Just show status (no changes)")
    print("4. Do both (delete failed + cleanup tests)")

    choice = input("\nEnter choice (1-4): ").strip()

    if choice == "1" or choice == "4":
        # Delete the failed instance
        failed_instance = integration.get("config", {}).get("instance")
        if failed_instance:
            delete_instance(failed_instance)

        # Create new instance
        webhook_url = integration.get("webhook_url")
        if not webhook_url:
            webhook_token = integration.get("webhook_token")
            webhook_url = f"https://healthcare-clinic-backend.fly.dev/webhooks/evolution/whatsapp/{webhook_token}"

        new_instance_name = f"clinic-{integration.get('clinic_id')}"
        print(f"\n📝 New instance name: {new_instance_name}")
        print(f"📝 Webhook URL: {webhook_url}")

        confirm = input("\nProceed with creation? (yes/no): ").strip().lower()
        if confirm == "yes":
            result = create_instance(new_instance_name, webhook_url)

            if result:
                # Update database with new instance name
                print(f"\n💾 Updating database...")
                supabase = get_supabase_client()

                new_config = integration.get("config", {})
                new_config["instance"] = new_instance_name

                update_result = supabase.schema("healthcare").table("integrations").update({
                    "config": new_config
                }).eq("id", integration["id"]).execute()

                if update_result.data:
                    print(f"✅ Database updated with new instance name")
                else:
                    print(f"⚠️  Database update may have failed")

                print("\n" + "=" * 60)
                print("✅ RESTORATION COMPLETE")
                print("=" * 60)
                print(f"\n📱 Next steps:")
                print(f"1. Open: {EVOLUTION_API_URL}/manager")
                print(f"2. Find instance: {new_instance_name}")
                print(f"3. Scan QR code with WhatsApp")
                print(f"4. Test webhook by sending a message")

    if choice == "2" or choice == "4":
        cleanup_test_instances()

    if choice == "3":
        print("\n✅ Status check complete")

    print("\n" + "=" * 60)
    print("Done!")
    print("=" * 60)


if __name__ == "__main__":
    main()
