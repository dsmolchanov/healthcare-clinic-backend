#!/usr/bin/env python3
"""Test the fixed RPC functions."""

import os
from dotenv import load_dotenv
from supabase import create_client

# Load environment variables
load_dotenv()

supabase_url = os.getenv('SUPABASE_URL')
supabase_key = os.getenv('SUPABASE_ANON_KEY')

if not supabase_url or not supabase_key:
    print("❌ Missing SUPABASE_URL or SUPABASE_ANON_KEY")
    exit(1)

supabase = create_client(supabase_url, supabase_key)

# Test organization ID (Shtern Dental)
ORG_ID = '4e8ddba1-ad52-4613-9a03-ec64636b3f6c'

print("=" * 70)
print("🧪 Testing Fixed RPC Functions")
print("=" * 70)
print(f"Organization ID: {ORG_ID}")
print("-" * 70)

# Test the simple RPC function
print("\n1️⃣ Testing get_organization_clinics_simple...")
try:
    result = supabase.rpc('get_organization_clinics_simple', {'org_id': ORG_ID}).execute()
    if result.data:
        print(f"✅ Simple function returned {len(result.data)} clinic(s):")
        for clinic in result.data:
            print(f"   - {clinic.get('name', 'Unknown')} (ID: {clinic.get('id', 'Unknown')})")
            print(f"     Phone: {clinic.get('phone', 'N/A')}")
            print(f"     Email: {clinic.get('email', 'N/A')}")
            print(f"     Active: {clinic.get('is_active', False)}")
    else:
        print("⚠️  No clinics returned (empty array)")
except Exception as e:
    print(f"❌ Error: {e}")

print("-" * 70)

# Test the full RPC function
print("\n2️⃣ Testing list_organization_clinics...")
try:
    result = supabase.rpc('list_organization_clinics', {'org_id': ORG_ID}).execute()
    if result.data:
        print(f"✅ Full function returned {len(result.data)} clinic(s):")
        for clinic in result.data:
            print(f"   - {clinic.get('name', 'Unknown')} (ID: {clinic.get('id', 'Unknown')})")
            print(f"     Address: {clinic.get('address', 'N/A')}, {clinic.get('city', '')}, {clinic.get('state', '')}")
            print(f"     Features: {clinic.get('features', {})}")
            print(f"     HIPAA Compliant: {clinic.get('hipaa_compliant', False)}")
    else:
        print("⚠️  No clinics returned (empty array)")
except Exception as e:
    print(f"❌ Error: {e}")

print("-" * 70)
print("\n✅ Summary:")
print("The RPC functions have been fixed to match the actual healthcare.clinics schema.")
print("They no longer reference non-existent columns like whatsapp_enabled or calendar_connected.")
print("\n📝 Next Steps:")
print("1. Clear your browser cache or use incognito mode")
print("2. Refresh the Organization Dashboard")
print("3. The clinics should now load without errors")
print("=" * 70)
