"""
Invalidate service cache to force reload with new i18n fields

Usage:
    python3 invalidate_service_cache.py [--clinic-id CLINIC_ID]
"""

import sys
import argparse
from app.config import get_redis_client
from app.database import create_supabase_client

def main():
    parser = argparse.ArgumentParser(description="Invalidate service cache")
    parser.add_argument(
        '--clinic-id',
        type=str,
        help='Specific clinic ID to invalidate (default: all clinics)'
    )
    args = parser.parse_args()

    try:
        redis = get_redis_client()
        supabase = create_supabase_client()

        if args.clinic_id:
            # Invalidate specific clinic
            cache_key = f"clinic:{args.clinic_id}:services"
            redis.delete(cache_key)
            print(f"✅ Invalidated cache for clinic {args.clinic_id}")
        else:
            # Get all clinics and invalidate
            result = supabase.table('clinics').select('id').eq('is_active', True).execute()
            clinics = result.data if result.data else []

            print(f"🗑️  Invalidating cache for {len(clinics)} clinics...")
            for clinic in clinics:
                cache_key = f"clinic:{clinic['id']}:services"
                redis.delete(cache_key)

            print(f"✅ Invalidated service cache for {len(clinics)} clinics")

        print("\n💡 Next request will reload services with i18n fields from database")

    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
