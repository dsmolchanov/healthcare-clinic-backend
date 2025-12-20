#!/usr/bin/env python3
"""
Enable calendar sync for all calendar integrations
"""
import os
from dotenv import load_dotenv
from supabase import create_client
from supabase.client import ClientOptions

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

print("🔍 Checking calendar integrations...")

# Get all calendar integrations
result = supabase.from_('calendar_integrations').select('*').execute()

if not result.data:
    print("❌ No calendar integrations found")
    exit(1)

print(f"✅ Found {len(result.data)} calendar integration(s)")

for integration in result.data:
    print(f"\n📅 Integration:")
    print(f"   Clinic ID: {integration.get('clinic_id')}")
    print(f"   Provider: {integration.get('provider')}")
    print(f"   Sync Enabled: {integration.get('sync_enabled')}")
    print(f"   Calendar ID: {integration.get('calendar_id')}")

    # Enable sync if not already enabled
    if not integration.get('sync_enabled'):
        print("   🔧 Enabling sync...")
        update_result = supabase.from_('calendar_integrations').update({
            'sync_enabled': True
        }).eq('id', integration['id']).execute()
        print("   ✅ Sync enabled!")
    else:
        print("   ✅ Sync already enabled")

print("\n✅ Done! Calendar sync is now enabled for all integrations")
print("\n🔄 The background worker will sync appointments every 15 minutes")
print("💡 You can also manually trigger sync from the frontend")
