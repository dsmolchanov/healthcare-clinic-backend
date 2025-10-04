#!/usr/bin/env python3
"""
Create calendar integration directly in the database
This bypasses the RPC function until PostgREST cache refreshes
"""
import os
from dotenv import load_dotenv
from supabase import create_client
from supabase.client import ClientOptions
import uuid
from datetime import datetime

# Load environment variables
load_dotenv()

# Create Supabase client with healthcare schema
options = ClientOptions(
    schema='healthcare',
    auto_refresh_token=True,
    persist_session=False
)

supabase = create_client(
    os.environ.get("SUPABASE_URL"),
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY"),
    options=options
)

print("🔧 Creating calendar integration directly...\n")

# Data
clinic_id = "e0c84f56-235d-49f2-9a44-37c1be579afc"  # Shtern Dental Clinic
org_id = "4e8ddba1-ad52-4613-9a03-ec64636b3f6c"
doctor_id = "22da5539-1d99-43ba-85d2-24623981484a"  # Dr. Mark Shtern

# Create integration
integration_data = {
    "id": str(uuid.uuid4()),
    "clinic_id": clinic_id,
    "organization_id": org_id,
    "doctor_id": doctor_id,
    "provider": "google",
    "calendar_id": "primary",
    "calendar_name": "Google Calendar",
    "credentials_vault_ref": f"manual_setup_{datetime.now().isoformat()}",
    "credentials_version": "1",
    "sync_enabled": True,
    "created_at": datetime.now().isoformat(),
    "updated_at": datetime.now().isoformat()
}

try:
    # Try to insert directly
    result = supabase.from_('calendar_integrations').insert(integration_data).execute()

    print("✅ Calendar integration created successfully!")
    print(f"Integration ID: {result.data[0]['id']}")
    print(f"Clinic ID: {result.data[0]['clinic_id']}")
    print(f"Provider: {result.data[0]['provider']}")
    print(f"Sync Enabled: {result.data[0]['sync_enabled']}")

    print("\n🎉 You can now use the Sync button on the frontend!")
    print("⚠️  Note: This integration doesn't have real OAuth credentials.")
    print("   You'll need to complete the OAuth flow again to get valid credentials.")

except Exception as e:
    print(f"❌ Failed to create integration: {e}")
    import traceback
    traceback.print_exc()
