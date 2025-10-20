"""
Check for Legacy WhatsApp Instances

This script checks for existing WhatsApp instances that need migration
from old tables to the new healthcare.integrations table.

Usage:
    python check_legacy_instances.py
"""

import os
import sys

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

from app.db.supabase_client import get_supabase_client


def check_legacy_tables():
    """Check for data in legacy tables"""
    supabase = get_supabase_client()

    print("="*80)
    print("Checking Legacy WhatsApp Data")
    print("="*80)
    print()

    legacy_data = {}

    # Check public.evolution_instances
    try:
        result = supabase.table('evolution_instances').select('*').execute()
        count = len(result.data) if result.data else 0
        legacy_data['evolution_instances'] = {
            'count': count,
            'data': result.data if result.data else []
        }
        print(f"📊 public.evolution_instances: {count} records")
        if count > 0:
            print(f"   Sample: {result.data[0].get('instance_name', 'N/A')}")
    except Exception as e:
        print(f"❌ public.evolution_instances: Error - {e}")
        legacy_data['evolution_instances'] = {'count': 0, 'data': [], 'error': str(e)}

    # Check healthcare.integrations
    try:
        result = supabase.schema('healthcare').table('integrations').select(
            'id, clinic_id, webhook_token, config, status, enabled'
        ).eq('type', 'whatsapp').execute()
        count = len(result.data) if result.data else 0
        legacy_data['healthcare_integrations'] = {
            'count': count,
            'data': result.data if result.data else []
        }
        print(f"📊 healthcare.integrations (WhatsApp): {count} records")

        # Check how many have tokens
        with_tokens = sum(1 for r in (result.data or []) if r.get('webhook_token'))
        without_tokens = count - with_tokens
        print(f"   ✅ With webhook_token: {with_tokens}")
        print(f"   ❌ Without webhook_token: {without_tokens}")
    except Exception as e:
        print(f"❌ healthcare.integrations: Error - {e}")
        legacy_data['healthcare_integrations'] = {'count': 0, 'data': [], 'error': str(e)}

    print()
    print("="*80)
    print("Analysis")
    print("="*80)
    print()

    evo_count = legacy_data.get('evolution_instances', {}).get('count', 0)
    hc_count = legacy_data.get('healthcare_integrations', {}).get('count', 0)

    if evo_count > 0 and hc_count == 0:
        print("⚠️  MIGRATION NEEDED:")
        print(f"   - {evo_count} instances in evolution_instances")
        print(f"   - 0 instances in healthcare.integrations")
        print(f"   - ACTION: Run migrate_whatsapp_integrations.py")
    elif evo_count > 0 and hc_count > 0:
        if evo_count > hc_count:
            print("⚠️  PARTIAL MIGRATION:")
            print(f"   - {evo_count} instances in evolution_instances")
            print(f"   - {hc_count} instances in healthcare.integrations")
            print(f"   - {evo_count - hc_count} instances need migration")
            print(f"   - ACTION: Run migrate_whatsapp_integrations.py")
        else:
            print("✅ MIGRATION COMPLETE:")
            print(f"   - All instances migrated to healthcare.integrations")
            print(f"   - Ready to update webhooks to new URL format")
    elif hc_count > 0:
        print("✅ NEW SYSTEM IN USE:")
        print(f"   - {hc_count} instances in healthcare.integrations")

        # Check token status
        hc_data = legacy_data.get('healthcare_integrations', {}).get('data', [])
        without_tokens = [r for r in hc_data if not r.get('webhook_token')]

        if without_tokens:
            print(f"   - ⚠️  {len(without_tokens)} instances missing webhook_token")
            print(f"   - ACTION: Re-run schema migration to generate tokens")
        else:
            print(f"   - ✅ All instances have webhook_token")
            print(f"   - ACTION: Update Evolution webhooks to new URL format")
    else:
        print("ℹ️  NO INSTANCES FOUND:")
        print("   - Start fresh with UI integration setup")

    print()
    print("="*80)
    print("Detailed Records")
    print("="*80)
    print()

    # Show evolution_instances details
    evo_data = legacy_data.get('evolution_instances', {}).get('data', [])
    if evo_data:
        print("Legacy Evolution Instances:")
        for i, inst in enumerate(evo_data, 1):
            print(f"\n[{i}] {inst.get('instance_name', 'N/A')}")
            print(f"    ID: {inst.get('id', 'N/A')}")
            print(f"    Org: {inst.get('organization_id', 'N/A')}")
            print(f"    Phone: {inst.get('phone_number', 'N/A')}")
            print(f"    Status: {inst.get('status', 'N/A')}")

    # Show healthcare.integrations details
    hc_data = legacy_data.get('healthcare_integrations', {}).get('data', [])
    if hc_data:
        print("\nCurrent Healthcare Integrations:")
        for i, integ in enumerate(hc_data, 1):
            config = integ.get('config', {})
            print(f"\n[{i}] {config.get('instance', 'N/A')}")
            print(f"    Clinic: {integ.get('clinic_id', 'N/A')}")
            print(f"    Token: {integ.get('webhook_token', 'NOT GENERATED')[:16]}...")
            print(f"    Status: {integ.get('status', 'N/A')}")
            print(f"    Enabled: {integ.get('enabled', False)}")

    print()
    return legacy_data


if __name__ == '__main__':
    check_legacy_tables()
