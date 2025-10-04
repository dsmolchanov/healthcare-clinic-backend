#!/usr/bin/env python3
"""
Test the healthcare.save_calendar_integration RPC function
"""
import os
from dotenv import load_dotenv
from supabase import create_client
import uuid

# Load environment variables
load_dotenv()

# Create Supabase client (public schema, like oauth_manager does)
supabase = create_client(
    os.environ.get("SUPABASE_URL"),
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
)

print("🧪 Testing healthcare.save_calendar_integration RPC\n")

# Test data
test_clinic_id = "e0c84f56-235d-49f2-9a44-37c1be579afc"  # Shtern Dental Clinic
test_org_id = "4e8ddba1-ad52-4613-9a03-ec64636b3f6c"
test_vault_ref = f"test_vault_{uuid.uuid4()}"

print(f"Clinic ID: {test_clinic_id}")
print(f"Org ID: {test_org_id}")
print(f"Vault Ref: {test_vault_ref}\n")

try:
    result = supabase.rpc('healthcare.save_calendar_integration', {
        'p_clinic_id': test_clinic_id,
        'p_organization_id': test_org_id,
        'p_provider': 'google',
        'p_calendar_id': 'primary',
        'p_credentials_vault_ref': test_vault_ref,
        'p_calendar_name': 'Test Google Calendar',
        'p_credentials_version': '1',
        'p_expires_at': None
    }).execute()

    print("✅ RPC call succeeded!")
    print(f"Result: {result.data}")

    if result.data and result.data.get('success'):
        print(f"\n✅ Integration created successfully!")
        print(f"Integration ID: {result.data.get('integration_id')}")
    else:
        print(f"\n❌ RPC returned success=false")
        print(f"Error: {result.data.get('error')}")

except Exception as e:
    print(f"❌ RPC call failed: {e}")
    import traceback
    traceback.print_exc()
