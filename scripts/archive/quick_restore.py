#!/usr/bin/env python3
"""Quick restoration script that can run both locally and in production."""

import os
import sys
import json
import requests

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from app.db.supabase_client import get_supabase_client
    HAS_SUPABASE = True
except Exception as e:
    print(f"⚠️  Cannot import Supabase client: {e}")
    HAS_SUPABASE = False

EVOLUTION_URL = "https://evolution-api-prod.fly.dev"


def get_integration_info():
    """Get integration info from database."""
    if not HAS_SUPABASE:
        print("❌ Supabase not available")
        return None

    try:
        supabase = get_supabase_client()
        result = supabase.schema("healthcare").table("integrations").select(
            "id,clinic_id,webhook_token,webhook_url,config"
        ).eq("type", "whatsapp").execute()

        if result.data and len(result.data) > 0:
            return result.data[0]
        return None
    except Exception as e:
        print(f"❌ Error querying database: {e}")
        return None


def create_instance(instance_name: str, webhook_url: str):
    """Create Evolution instance."""
    print(f"\n🆕 Creating instance: {instance_name}")
    print(f"📝 Webhook: {webhook_url}")

    payload = {
        "instanceName": instance_name,
        "qrcode": True,
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
            f"{EVOLUTION_URL}/instance/create",
            json=payload,
            timeout=30
        )
        response.raise_for_status()
        result = response.json()

        print("✅ Instance created!")

        if "qrcode" in result:
            print("\n📱 QR Code Information:")
            if "base64" in result["qrcode"]:
                print(f"   - Base64 available (length: {len(result['qrcode']['base64'])})")
            if "code" in result["qrcode"]:
                print(f"   - Text code available")
            print(f"\n🔗 Scan at: {EVOLUTION_URL}/manager")

        return result
    except Exception as e:
        print(f"❌ Error creating instance: {e}")
        if hasattr(e, 'response') and e.response:
            print(f"   Response: {e.response.text}")
        return None


def update_database(integration_id: str, new_instance_name: str):
    """Update database with new instance name."""
    if not HAS_SUPABASE:
        print("⚠️  Cannot update database - no Supabase connection")
        return False

    try:
        supabase = get_supabase_client()

        # Get current config
        result = supabase.schema("healthcare").table("integrations").select("config").eq(
            "id", integration_id
        ).execute()

        if not result.data:
            print("❌ Integration not found")
            return False

        config = result.data[0].get("config", {})
        config["instance"] = new_instance_name

        # Update
        update_result = supabase.schema("healthcare").table("integrations").update({
            "config": config
        }).eq("id", integration_id).execute()

        if update_result.data:
            print("✅ Database updated")
            return True
        else:
            print("⚠️  Database update may have failed")
            return False
    except Exception as e:
        print(f"❌ Error updating database: {e}")
        return False


def main():
    print("=" * 60)
    print("Quick Evolution Restoration")
    print("=" * 60)

    # Get integration info
    print("\n📥 Fetching integration info...")
    integration = get_integration_info()

    if not integration:
        print("\n❌ No integration found in database")
        print("\nManual steps required:")
        print("1. Go to: https://plaintalk-frontend.vercel.app")
        print("2. Create new WhatsApp integration via UI")
        print("3. Scan QR code")
        return

    print(f"✅ Found integration:")
    print(f"   ID: {integration['id']}")
    print(f"   Clinic ID: {integration.get('clinic_id')}")
    print(f"   Old Instance: {integration.get('config', {}).get('instance', 'N/A')}")
    print(f"   Webhook Token: {integration.get('webhook_token', 'N/A')[:16]}...")

    # Create new instance name (simplified)
    clinic_id = integration.get('clinic_id')
    if not clinic_id:
        print("❌ No clinic_id found")
        return

    new_instance_name = f"clinic-{clinic_id}"
    webhook_url = integration.get('webhook_url')

    if not webhook_url and integration.get('webhook_token'):
        webhook_token = integration['webhook_token']
        webhook_url = f"https://healthcare-clinic-backend.fly.dev/webhooks/evolution/whatsapp/{webhook_token}"

    if not webhook_url:
        print("❌ Cannot determine webhook URL")
        return

    print(f"\n📝 New instance name: {new_instance_name}")

    # Create instance
    result = create_instance(new_instance_name, webhook_url)

    if result:
        # Update database
        print("\n💾 Updating database...")
        update_database(integration['id'], new_instance_name)

        print("\n" + "=" * 60)
        print("✅ RESTORATION COMPLETE!")
        print("=" * 60)
        print(f"\n📱 Next steps:")
        print(f"1. Open: {EVOLUTION_URL}/manager")
        print(f"2. Find instance: {new_instance_name}")
        print(f"3. Scan QR code with WhatsApp")
        print(f"4. Wait for 'connected' status")
        print(f"5. Test by sending a message")
    else:
        print("\n❌ Instance creation failed")
        print("Try creating manually via frontend UI")


if __name__ == "__main__":
    main()
