#!/usr/bin/env python3
"""
Seed doctor_services table for a clinic.
Uses the centralized mapping module for consistency.
"""

import os
import sys
from pathlib import Path

# Add parent to path for imports
script_dir = Path(__file__).parent
backend_dir = script_dir.parent
sys.path.insert(0, str(backend_dir))

# Load environment variables from .env file
env_file = backend_dir / '.env'
if env_file.exists():
    with open(env_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                # Remove quotes if present
                value = value.strip('"').strip("'")
                os.environ[key] = value

from supabase import create_client

from app.domain.eligibility.mapping import derive_mappings

# =============================================================================
# CONFIGURATION
# =============================================================================
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

# Shtern Dental clinic ID
SHTERN_CLINIC_ID = "e0c84f56-235d-49f2-9a44-37c1be579afc"


def validate_env():
    """Fail fast if required env vars are missing."""
    if not SUPABASE_URL:
        raise EnvironmentError("SUPABASE_URL environment variable is required")
    if not SUPABASE_KEY:
        raise EnvironmentError("SUPABASE_SERVICE_ROLE_KEY environment variable is required")


def seed_doctor_services(clinic_id: str, dry_run: bool = True):
    """
    Seed doctor_services table for a clinic.

    Args:
        clinic_id: UUID of the clinic
        dry_run: If True, only print what would be inserted
    """
    validate_env()
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    print(f"{'[DRY RUN] ' if dry_run else ''}Seeding doctor_services for clinic {clinic_id}")
    print("=" * 60)

    # 1. Get all active doctors at the clinic
    doctors_result = supabase.schema('healthcare').table('doctors').select(
        'id, first_name, last_name, specialization'
    ).eq('clinic_id', clinic_id).eq('active', True).execute()

    doctors = doctors_result.data or []
    print(f"Found {len(doctors)} active doctors")

    # 2. Get all active services at the clinic
    services_result = supabase.schema('healthcare').table('services').select(
        'id, name, category, service_code'
    ).eq('clinic_id', clinic_id).eq('is_active', True).execute()

    services = services_result.data or []
    print(f"Found {len(services)} active services")

    # 3. Get doctor_specialties (structured data, preferred over text field)
    doctor_ids = [d['id'] for d in doctors]
    if doctor_ids:
        specialties_result = supabase.schema('healthcare').table('doctor_specialties').select(
            'doctor_id, specialty_code, is_active, approval_status'
        ).in_('doctor_id', doctor_ids).execute()
        doctor_specialties = specialties_result.data or []
    else:
        doctor_specialties = []
    print(f"Found {len(doctor_specialties)} doctor_specialties records")

    # 4. Derive mappings using centralized logic
    mappings, stats = derive_mappings(doctors, services, doctor_specialties)

    # 5. Print detailed report
    print("\n" + "=" * 60)
    print("DERIVATION REPORT")
    print("=" * 60)
    print(f"Total doctors:      {stats['total_doctors']}")
    print(f"Total services:     {stats['total_services']}")
    print(f"Mappings created:   {stats['mappings_created']}")
    print(f"Restricted skipped: {stats['restricted_skipped']} (generalist->specialist services)")

    if stats['unmapped_services']:
        print(f"\n  {len(stats['unmapped_services'])} services have NO eligible doctors:")
        for svc_id in stats['unmapped_services'][:5]:
            svc = next((s for s in services if s['id'] == svc_id), {})
            print(f"   - {svc.get('name', svc_id)} (category: {svc.get('category', '<none>')})")
        if len(stats['unmapped_services']) > 5:
            print(f"   ... and {len(stats['unmapped_services']) - 5} more")

    if stats['unknown_categories']:
        print(f"\n  Unknown categories found (need manual mapping):")
        for cat in stats['unknown_categories']:
            print(f"   - '{cat}'")

    # 6. Show doctor breakdown
    print("\n" + "-" * 60)
    print("DOCTOR BREAKDOWN")
    print("-" * 60)
    for doctor in doctors:
        doc_name = f"Dr. {doctor['first_name']} {doctor['last_name']}"
        doc_mappings = [m for m in mappings if m['doctor_id'] == doctor['id']]
        primary_count = sum(1 for m in doc_mappings if m.get('is_primary'))
        print(f"{doc_name}: {len(doc_mappings)} services ({primary_count} as primary)")

    # 7. Insert mappings (if not dry run)
    if not dry_run:
        print("\n" + "=" * 60)
        print("APPLYING CHANGES")
        print("=" * 60)

        # Clear existing derived mappings (keep manual overrides)
        if doctor_ids:
            delete_result = supabase.schema('healthcare').table('doctor_services').delete().eq(
                'source', 'system'
            ).in_('doctor_id', doctor_ids).execute()

            print(f"Cleared existing system-derived mappings")

        # Insert new mappings in batches
        if mappings:
            BATCH_SIZE = 100
            for i in range(0, len(mappings), BATCH_SIZE):
                batch = mappings[i:i + BATCH_SIZE]
                supabase.schema('healthcare').table('doctor_services').upsert(
                    batch,
                    on_conflict='doctor_id,service_id'
                ).execute()
                print(f"  Inserted batch {i // BATCH_SIZE + 1} ({len(batch)} mappings)")

        print(f"Inserted {len(mappings)} doctor-service mappings")

        # Refresh materialized view
        try:
            supabase.rpc('refresh_eligibility_matrix').execute()
            print("Refreshed eligibility matrix")
        except Exception as e:
            print(f"  Could not refresh matrix (may not exist): {e}")

    else:
        print("\n[DRY RUN] No changes made. Run with --execute to apply.")

    # Return stats for programmatic use
    return {
        "mappings": mappings,
        "stats": stats,
        "dry_run": dry_run,
    }


if __name__ == "__main__":
    dry_run = "--execute" not in sys.argv

    if "--help" in sys.argv or "-h" in sys.argv:
        print("Usage: python seed_doctor_services.py [--execute] [--clinic-id UUID]")
        print("")
        print("Options:")
        print("  --execute       Actually insert data (default is dry-run)")
        print("  --clinic-id     Override the default clinic ID")
        print("")
        print(f"Default clinic: {SHTERN_CLINIC_ID}")
        sys.exit(0)

    # Allow clinic ID override
    clinic_id = SHTERN_CLINIC_ID
    if "--clinic-id" in sys.argv:
        idx = sys.argv.index("--clinic-id")
        if idx + 1 < len(sys.argv):
            clinic_id = sys.argv[idx + 1]

    result = seed_doctor_services(clinic_id, dry_run=dry_run)

    # Exit with error if no mappings were created
    if result["stats"]["mappings_created"] == 0:
        print("\nERROR: No mappings were created!")
        sys.exit(1)
