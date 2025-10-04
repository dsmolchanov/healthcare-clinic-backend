import os
import asyncio
from supabase import create_client

async def check_calendar_setup():
    supabase = create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    )
    
    print("=" * 60)
    print("CALENDAR INTEGRATION STATUS CHECK")
    print("=" * 60)
    
    # 1. Check calendar integrations
    print("\n1. Checking healthcare.calendar_integrations...")
    try:
        result = supabase.rpc('healthcare.get_calendar_integration_by_clinic', {
            'p_clinic_id': '3e411ecb-3411-4add-91e2-8fa897310cb0',
            'p_provider': 'google'
        }).execute()
        
        if result.data and len(result.data) > 0:
            integration = result.data[0]
            print(f"   ✅ Integration found:")
            print(f"      - Clinic ID: {integration.get('clinic_id')}")
            print(f"      - Provider: {integration.get('provider')}")
            print(f"      - Sync Enabled: {integration.get('sync_enabled')}")
            print(f"      - Calendar ID: {integration.get('calendar_id')}")
            print(f"      - Vault Ref: {integration.get('credentials_vault_ref')[:50]}..." if integration.get('credentials_vault_ref') else "None")
            print(f"      - Expires At: {integration.get('expires_at')}")
        else:
            print("   ❌ No calendar integration found")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    # 2. Check appointments
    print("\n2. Checking appointments...")
    try:
        result = supabase.table('appointments').select('*').limit(5).execute()
        if result.data:
            print(f"   ✅ Found {len(result.data)} appointments:")
            for apt in result.data[:3]:
                print(f"      - {apt.get('id')}: {apt.get('appointment_type')} on {apt.get('appointment_date')}")
                print(f"        Synced: {bool(apt.get('google_event_id'))}")
        else:
            print("   ❌ No appointments found")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    # 3. Check old clinic_calendar_tokens
    print("\n3. Checking old clinic_calendar_tokens table...")
    try:
        result = supabase.table('clinic_calendar_tokens').select('*').execute()
        if result.data:
            print(f"   ⚠️  Found {len(result.data)} entries in old table (should be migrated)")
            for token in result.data:
                print(f"      - Clinic: {token.get('clinic_id')}, Provider: {token.get('provider')}")
        else:
            print("   ✅ Old table empty or migrated")
    except Exception as e:
        print(f"   ℹ️  Table might not exist: {e}")
    
    print("\n" + "=" * 60)

if __name__ == "__main__":
    asyncio.run(check_calendar_setup())
