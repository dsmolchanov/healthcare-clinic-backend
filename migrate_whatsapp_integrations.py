"""
Migrate WhatsApp integrations from fragmented tables to healthcare.integrations
Run after schema migration (20251020_add_webhook_tokens_to_integrations.sql)

Usage:
    python migrate_whatsapp_integrations.py --dry-run  # Preview changes
    python migrate_whatsapp_integrations.py            # Execute migration
"""

import os
import sys
from datetime import datetime
from supabase import create_client
from typing import Dict, List, Any

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

from app.db.supabase_client import get_supabase_client


def migrate_evolution_instances(supabase, dry_run: bool = False) -> Dict[str, int]:
    """
    Migrate from public.evolution_instances to healthcare.integrations
    """
    stats = {"migrated": 0, "skipped": 0, "errors": 0}

    # Fetch all Evolution instances with org info
    print("📥 Fetching Evolution instances...")
    instances = supabase.table('evolution_instances').select(
        'id, organization_id, instance_name, phone_number, status, config, created_at, updated_at'
    ).neq('status', 'error').execute()

    print(f"Found {len(instances.data)} Evolution instances")

    for instance in instances.data:
        try:
            org_id = instance['organization_id']

            # Get clinic for organization
            clinic = supabase.table('clinics').select('id, name').eq(
                'organization_id', org_id
            ).eq('is_active', True).limit(1).execute()

            if not clinic.data:
                print(f"⚠️  No clinic found for org {org_id}, skipping {instance['instance_name']}")
                stats["skipped"] += 1
                continue

            clinic_id = clinic.data[0]['id']
            clinic_name = clinic.data[0]['name']

            # Map Evolution status to integration status
            status_map = {
                'connected': 'active',
                'connecting': 'pending',
                'qr_pending': 'pending',
                'disconnected': 'disconnected',
            }
            integration_status = status_map.get(instance['status'], 'pending')

            # Build integration record
            integration = {
                'organization_id': org_id,
                'clinic_id': clinic_id,
                'type': 'whatsapp',
                'provider': 'evolution',
                'status': integration_status,
                'config': {
                    'instance': instance['instance_name'],
                    'instance_key': instance.get('config', {}).get('instance_key'),
                    'connection_type': 'baileys',
                },
                'phone_number': instance.get('phone_number'),
                'display_name': clinic_name,
                'connected_at': instance.get('created_at') if integration_status == 'active' else None,
                'last_seen_at': instance.get('updated_at'),
                'enabled': integration_status == 'active',
                'created_at': instance['created_at'],
                'updated_at': instance['updated_at'],
            }

            if dry_run:
                print(f"✓ Would migrate {instance['instance_name']} → {clinic_name}")
                stats["migrated"] += 1
            else:
                # Check if already exists
                existing = supabase.schema('healthcare').table('integrations').select('id').eq(
                    'clinic_id', clinic_id
                ).eq('type', 'whatsapp').eq('provider', 'evolution').execute()

                if existing.data:
                    # Update existing
                    supabase.schema('healthcare').table('integrations').update(integration).eq(
                        'id', existing.data[0]['id']
                    ).execute()
                    print(f"♻️  Updated {instance['instance_name']} → {clinic_name}")
                else:
                    # Insert new
                    supabase.schema('healthcare').table('integrations').insert(integration).execute()
                    print(f"✅ Migrated {instance['instance_name']} → {clinic_name}")

                stats["migrated"] += 1

        except Exception as e:
            print(f"❌ Error migrating {instance.get('instance_name', 'unknown')}: {e}")
            stats["errors"] += 1

    return stats


def verify_migration(supabase) -> bool:
    """Verify migration success by comparing counts"""

    # Count Evolution instances (excluding errors)
    evo_count = supabase.table('evolution_instances').select(
        'id', count='exact'
    ).neq('status', 'error').execute()

    # Count healthcare.integrations with type=whatsapp
    integration_count = supabase.schema('healthcare').table('integrations').select(
        'id', count='exact'
    ).eq('type', 'whatsapp').execute()

    print(f"\n📊 Migration Verification:")
    print(f"   Evolution instances (non-error): {evo_count.count}")
    print(f"   WhatsApp integrations: {integration_count.count}")

    if evo_count.count == integration_count.count:
        print("✅ Counts match - migration successful!")
        return True
    else:
        print("⚠️  Count mismatch - review migration logs")
        return False


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Migrate WhatsApp integrations')
    parser.add_argument('--dry-run', action='store_true', help='Preview without making changes')
    args = parser.parse_args()

    supabase = get_supabase_client()

    print("=" * 80)
    print("WhatsApp Integration Migration")
    print("=" * 80)
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE MIGRATION'}")
    print()

    # Migrate Evolution instances
    stats = migrate_evolution_instances(supabase, args.dry_run)

    print()
    print("=" * 80)
    print(f"Migration Complete:")
    print(f"  Migrated: {stats['migrated']}")
    print(f"  Skipped:  {stats['skipped']}")
    print(f"  Errors:   {stats['errors']}")
    print("=" * 80)

    # Verify if not dry run
    if not args.dry_run:
        print()
        verify_migration(supabase)


if __name__ == '__main__':
    main()
