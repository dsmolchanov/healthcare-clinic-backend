#!/usr/bin/env python3
"""
One-time backfill script to geocode existing clinics.

Usage:
    cd apps/healthcare-backend
    python3 scripts/backfill_clinic_geocoding.py --dry-run
    python3 scripts/backfill_clinic_geocoding.py --execute
"""
import asyncio
import argparse
import os
import sys
from datetime import datetime, timezone

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

# Load environment variables from .env file
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
if os.path.exists(env_path):
    load_dotenv(env_path)
    print(f"Loaded environment from {env_path}")

from supabase import create_client
from supabase.client import ClientOptions
from app.utils.geocoding import geocode_address, build_location_data

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")


async def backfill_clinics(dry_run: bool = True):
    """Geocode all clinics that don't have location_data."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print("Error: SUPABASE_URL and SUPABASE_SERVICE_KEY/SUPABASE_SERVICE_ROLE_KEY must be set")
        return

    # Check for Google Maps API key
    if not os.getenv("GOOGLE_MAPS_API_KEY"):
        print("Error: GOOGLE_MAPS_API_KEY must be set for geocoding")
        return

    # Create client with healthcare schema
    options = ClientOptions(schema='healthcare')
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY, options=options)

    # Find clinics without location_data (empty or null)
    result = supabase.table('clinics').select(
        'id, name, address, city, state, zip_code, country, location_data'
    ).or_('location_data.is.null,location_data.eq.{}').execute()

    clinics = result.data or []
    print(f"Found {len(clinics)} clinics without location data")

    if len(clinics) == 0:
        print("No clinics need geocoding. Exiting.")
        return

    success_count = 0
    fail_count = 0

    for clinic in clinics:
        clinic_id = clinic['id']
        clinic_name = clinic['name']

        print(f"\nProcessing: {clinic_name} ({clinic_id})")
        print(f"  Address: {clinic.get('address')}, {clinic.get('city')}, {clinic.get('state')}")

        geocode_result = await geocode_address(
            address=clinic.get('address') or '',
            city=clinic.get('city') or '',
            state=clinic.get('state') or '',
            country=clinic.get('country') or 'USA',
            zip_code=clinic.get('zip_code')
        )

        if geocode_result.get('success'):
            location_data = build_location_data(geocode_result)
            location_data['geocoded_at'] = datetime.now(timezone.utc).isoformat()

            print(f"  Geocoded: {location_data.get('formatted_address')}")
            print(f"  Coords: {location_data.get('lat')}, {location_data.get('lng')}")
            print(f"  Place ID: {location_data.get('place_id')}")
            print(f"  Directions: {location_data.get('directions_url')}")

            if not dry_run:
                supabase.table('clinics').update({
                    'location_data': location_data
                }).eq('id', clinic_id).execute()
                print("  Updated!")
            else:
                print("  [DRY RUN - not updated]")

            success_count += 1
        else:
            print(f"  FAILED: {geocode_result.get('error')}")
            fail_count += 1

        # Rate limiting - 1 request per second to avoid API rate limits
        await asyncio.sleep(1)

    print(f"\n{'='*50}")
    print(f"Backfill complete: {success_count} success, {fail_count} failed")
    if dry_run:
        print("This was a DRY RUN. Run with --execute to apply changes.")


def main():
    parser = argparse.ArgumentParser(description='Backfill clinic geocoding data')
    parser.add_argument('--dry-run', action='store_true', help='Preview changes without applying')
    parser.add_argument('--execute', action='store_true', help='Apply changes to database')
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        print("Error: Must specify --dry-run or --execute")
        parser.print_help()
        return

    asyncio.run(backfill_clinics(dry_run=not args.execute))


if __name__ == '__main__':
    main()
