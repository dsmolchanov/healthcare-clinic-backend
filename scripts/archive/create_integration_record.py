#!/usr/bin/env python3
"""
Create integration record in database for existing Evolution instance.
"""

import os
import sys
import uuid
import secrets
import base64

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.db.supabase_client import get_supabase_client

# Instance info from Evolution
INSTANCE_NAME = "clinic-4e8ddba1-ad52-4613-9a03-ec64636b3f6c-1763129934831"
CLINIC_ID = "4e8ddba1-ad52-4613-9a03-ec64636b3f6c"  # Extracted from instance name
ORGANIZATION_ID = "e0c84f56-235d-49f2-9a44-37c1be579afc"  # From logs


def generate_webhook_token():
    """Generate a secure webhook token."""
    random_bytes = secrets.token_bytes(24)
    return base64.urlsafe_b64encode(random_bytes).decode('utf-8').rstrip('=')


def create_integration():
    """Create the integration record."""
    print("=" * 60)
    print("Creating WhatsApp Integration Record")
    print("=" * 60)

    supabase = get_supabase_client()

    # Generate webhook token
    webhook_token = generate_webhook_token()
    print(f"\n📝 Generated webhook token: {webhook_token[:16]}...")

    # Create integration record
    integration_data = {
        "id": str(uuid.uuid4()),
        "organization_id": ORGANIZATION_ID,
        "clinic_id": CLINIC_ID,
        "type": "whatsapp",
        "provider": "evolution",
        "status": "active",
        "enabled": True,
        "webhook_token": webhook_token,
        "display_name": "Main WhatsApp",
        "config": {
            "instance": INSTANCE_NAME,
            "api_url": "https://evolution-api-prod.fly.dev",
            "sync_enabled": True
        },
        "credentials_version": "1"
    }

    print(f"\n💾 Creating integration record...")
    print(f"   Organization ID: {ORGANIZATION_ID}")
    print(f"   Clinic ID: {CLINIC_ID}")
    print(f"   Instance: {INSTANCE_NAME}")

    try:
        result = supabase.schema("healthcare").table("integrations").insert(
            integration_data
        ).execute()

        if result.data:
            print(f"\n✅ Integration created successfully!")
            print(f"   ID: {result.data[0]['id']}")
            print(f"   Webhook Token: {webhook_token}")
            print(f"   Webhook URL: https://healthcare-clinic-backend.fly.dev/webhooks/evolution/whatsapp/{webhook_token}")

            return {
                "integration_id": result.data[0]['id'],
                "webhook_token": webhook_token,
                "webhook_url": f"https://healthcare-clinic-backend.fly.dev/webhooks/evolution/whatsapp/{webhook_token}"
            }
        else:
            print(f"\n❌ Failed to create integration")
            return None

    except Exception as e:
        print(f"\n❌ Error creating integration: {e}")
        return None


def main():
    try:
        result = create_integration()

        if result:
            print("\n" + "=" * 60)
            print("✅ DATABASE RECORD CREATED!")
            print("=" * 60)
            print(f"\n📱 Next step: Update Evolution webhook URL")
            print(f"\nRun this command:")
            print(f"\ncurl -X PUT https://evolution-api-prod.fly.dev/instance/webhook/{INSTANCE_NAME} \\")
            print(f"  -H 'Content-Type: application/json' \\")
            print(f"  -d '{{")
            print(f"    \"webhook\": \"{result['webhook_url']}\",")
            print(f"    \"webhook_by_events\": false,")
            print(f"    \"events\": [\"MESSAGES_UPSERT\", \"CONNECTION_UPDATE\"]")
            print(f"  }}'\n")

            print(f"Or visit Evolution Manager:")
            print(f"https://evolution-api-prod.fly.dev/manager\n")

        else:
            print("\n❌ Failed to create integration record")
            sys.exit(1)

    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
