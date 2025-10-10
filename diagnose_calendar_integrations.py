#!/usr/bin/env python3
"""
Diagnose calendar_integrations table to see why sync isn't enabled
"""
import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from app.db.supabase_client import get_supabase_client

def main():
    print("🔍 Diagnosing calendar_integrations table...")
    print()
    
    supabase = get_supabase_client(schema='healthcare')
    
    # Get all calendar integrations
    result = supabase.from_('calendar_integrations').select('*').execute()
    
    if not result.data:
        print("❌ No records found in calendar_integrations table")
        print()
        print("💡 This means OAuth was never completed, or records are in a different table.")
        print()
        
        # Check if there are any clinics at all
        clinics = supabase.from_('clinics').select('id, name').limit(5).execute()
        print(f"📊 Found {len(clinics.data)} clinics in healthcare.clinics table")
        for clinic in clinics.data[:3]:
            print(f"   - {clinic.get('name')} ({clinic.get('id')})")
        
        return
    
    print(f"📊 Found {len(result.data)} calendar integration records:")
    print()
    
    enabled_count = 0
    disabled_count = 0
    missing_creds_count = 0
    
    for integration in result.data:
        clinic_id = integration.get('clinic_id')
        provider = integration.get('provider')
        sync_enabled = integration.get('sync_enabled')
        has_creds = bool(integration.get('credentials_vault_ref'))
        
        status = "✅ ENABLED" if sync_enabled else "❌ DISABLED"
        creds_status = "🔑 Has credentials" if has_creds else "⚠️ Missing credentials"
        
        print(f"{status} - Clinic: {clinic_id[:8]}...")
        print(f"         Provider: {provider}")
        print(f"         {creds_status}")
        print(f"         Vault ref: {integration.get('credentials_vault_ref', 'None')[:30]}...")
        print()
        
        if sync_enabled:
            enabled_count += 1
        else:
            disabled_count += 1
        
        if not has_creds:
            missing_creds_count += 1
    
    print("=" * 60)
    print(f"Summary:")
    print(f"  Total integrations: {len(result.data)}")
    print(f"  Enabled: {enabled_count}")
    print(f"  Disabled: {disabled_count}")
    print(f"  Missing credentials: {missing_creds_count}")
    print()
    
    if disabled_count > 0 and missing_creds_count == 0:
        print("⚠️ Issue: Integrations have credentials but sync is disabled")
        print("   The migration UPDATE should have fixed this.")
        print("   Try manually enabling:")
        print()
        print("   UPDATE healthcare.calendar_integrations")
        print("   SET sync_enabled = true")
        print("   WHERE credentials_vault_ref IS NOT NULL;")
    
    if missing_creds_count > 0:
        print("⚠️ Issue: Some integrations don't have credentials")
        print("   OAuth was not completed for these integrations.")
        print("   Users need to re-authenticate via the UI.")

if __name__ == "__main__":
    main()
