#!/usr/bin/env python3
"""
Diagnose calendar integration status
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

print("🔍 CALENDAR INTEGRATION DIAGNOSTICS\n")

# Check calendar integrations
print("1️⃣ Checking calendar_integrations table...")
integrations = supabase.from_('calendar_integrations').select('*').execute()
print(f"   Found: {len(integrations.data)} integration(s)")
for integration in integrations.data:
    print(f"   - Clinic: {integration['clinic_id']}")
    print(f"     Provider: {integration['provider']}")
    print(f"     Sync Enabled: {integration['sync_enabled']}")
    print(f"     Calendar ID: {integration['calendar_id']}")

# Check clinics
print("\n2️⃣ Checking clinics table...")
clinics = supabase.from_('clinics').select('id, name, organization_id').execute()
print(f"   Found: {len(clinics.data)} clinic(s)")
for clinic in clinics.data[:5]:  # Show first 5
    print(f"   - {clinic['name']} ({clinic['id']})")

# Check appointments
print("\n3️⃣ Checking appointments table...")
appointments = supabase.from_('appointments').select('id, patient_name, appointment_date, google_event_id').limit(10).execute()
print(f"   Found: {len(appointments.data)} appointment(s)")
unsynced = [a for a in appointments.data if not a.get('google_event_id')]
print(f"   Unsynced: {len(unsynced)} appointment(s)")
for appt in unsynced[:3]:
    print(f"   - {appt.get('patient_name')} on {appt.get('appointment_date')}")

# Check vault credentials
print("\n4️⃣ Checking credential_vault table...")
try:
    vault = supabase.from_('credential_vault').select('id, organization_id, provider').execute()
    print(f"   Found: {len(vault.data)} credential(s)")
    for cred in vault.data:
        print(f"   - {cred['provider']} for org {cred['organization_id']}")
except Exception as e:
    print(f"   ❌ Error: {e}")

print("\n" + "="*60)
print("SUMMARY:")
print("="*60)
if len(integrations.data) == 0:
    print("❌ No calendar integrations found!")
    print("   → You need to complete OAuth flow on frontend")
    print("   → Go to: https://plaintalk.io/intelligence/integrations")
    print("   → Click 'Connect Google Calendar'")
elif len([i for i in integrations.data if i['sync_enabled']]) == 0:
    print("⚠️  Calendar integration exists but sync is disabled!")
    print("   → Run: python3 enable_calendar_sync.py")
else:
    print("✅ Calendar integration is properly configured!")
    if len(unsynced) > 0:
        print(f"   → {len(unsynced)} appointments ready to sync")
        print("   → Click 'Sync' button on frontend or wait 15 min")
    else:
        print("   → All appointments are synced!")
