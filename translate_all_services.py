"""
Translate all services using Google Translate Edge Function

This script calls the Supabase edge function to translate all services
from English to multiple languages (ru, es, pt, he) and populates the
name_i18n and description_i18n JSONB fields.

Usage:
    python3 translate_all_services.py [--clinic-id CLINIC_ID] [--dry-run] [--limit N]
"""

import asyncio
import argparse
import logging
import os
import time
from typing import Dict, List
from dotenv import load_dotenv
import httpx
from app.database import create_supabase_client
from app.config import get_redis_client
from app.services.clinic_data_cache import ClinicDataCache

# Load environment variables
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def call_translate_edge_function(
    service_id: str,
    name: str,
    description: str = None,
    source_language: str = 'en'
) -> Dict:
    """
    Call the Supabase edge function to translate a service

    Args:
        service_id: Service ID
        name: Service name in source language
        description: Service description in source language
        source_language: Source language code (default: 'en')

    Returns:
        Translation result dict
    """
    supabase_url = os.getenv('SUPABASE_URL')
    supabase_anon_key = os.getenv('SUPABASE_ANON_KEY')

    if not supabase_url or not supabase_anon_key:
        raise ValueError("SUPABASE_URL and SUPABASE_ANON_KEY must be set")

    # Edge function URL
    edge_function_url = f"{supabase_url}/functions/v1/translate-service"

    payload = {
        "service_id": service_id,
        "name": name,
        "description": description,
        "source_language": source_language
    }

    headers = {
        "Authorization": f"Bearer {supabase_anon_key}",
        "Content-Type": "application/json"
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(edge_function_url, json=payload, headers=headers)

        if response.status_code != 200:
            error_text = response.text
            logger.error(f"❌ Edge function error: {response.status_code} - {error_text}")
            raise Exception(f"Translation failed: {error_text}")

        return response.json()


async def translate_all_services(
    supabase,
    clinic_id: str = None,
    dry_run: bool = False,
    limit: int = None
) -> Dict[str, int]:
    """
    Translate all active services

    Args:
        supabase: Supabase client
        clinic_id: Optional clinic ID to filter services
        dry_run: If True, don't actually call edge function
        limit: Optional limit on number of services to translate

    Returns:
        Dict with counts of translated services
    """
    stats = {
        "total_services": 0,
        "translated_services": 0,
        "skipped_services": 0,
        "failed_services": 0
    }

    try:
        # Fetch all active services
        query = supabase.schema('healthcare').table('services').select('*').eq('active', True)
        if clinic_id:
            query = query.eq('clinic_id', clinic_id)
        if limit:
            query = query.limit(limit)

        result = query.execute()
        services = result.data if result.data else []
        stats["total_services"] = len(services)

        logger.info(f"📊 Found {len(services)} active services to translate")

        for idx, service in enumerate(services, 1):
            service_id = service['id']
            name = service['name']
            description = service.get('description')

            # Check if already has JSONB translations
            existing_name_i18n = service.get('name_i18n') or {}
            if existing_name_i18n and len(existing_name_i18n) > 1:  # More than just 'en'
                logger.info(f"[{idx}/{len(services)}] ⏭️  Skipping '{name}' - already translated")
                stats["skipped_services"] += 1
                continue

            if dry_run:
                logger.info(f"[{idx}/{len(services)}] 🔍 [DRY RUN] Would translate '{name}'")
                stats["translated_services"] += 1
            else:
                try:
                    logger.info(f"[{idx}/{len(services)}] 🔄 Translating '{name}'...")

                    result = await call_translate_edge_function(
                        service_id=service_id,
                        name=name,
                        description=description,
                        source_language='en'
                    )

                    if result.get('success'):
                        translations = result.get('translations', {})
                        name_i18n = translations.get('name_i18n', {})
                        logger.info(f"   ✅ Translated to: {', '.join(name_i18n.keys())}")
                        stats["translated_services"] += 1
                    else:
                        logger.error(f"   ❌ Translation failed: {result.get('error')}")
                        stats["failed_services"] += 1

                    # Rate limiting: wait 0.5s between requests
                    await asyncio.sleep(0.5)

                except Exception as e:
                    logger.error(f"   ❌ Error translating service {service_id}: {e}")
                    stats["failed_services"] += 1

        logger.info(f"\n📈 Summary:")
        logger.info(f"   Total services: {stats['total_services']}")
        logger.info(f"   Translated: {stats['translated_services']}")
        logger.info(f"   Skipped (already translated): {stats['skipped_services']}")
        logger.info(f"   Failed: {stats['failed_services']}")

        return stats

    except Exception as e:
        logger.error(f"❌ Error translating services: {e}")
        raise


async def invalidate_service_cache(clinic_id: str = None):
    """
    Invalidate service cache to force reload with new JSONB i18n fields

    Args:
        clinic_id: Optional clinic ID to invalidate (if None, invalidates all)
    """
    try:
        redis = get_redis_client()
        cache = ClinicDataCache(redis)
        supabase = create_supabase_client()

        if clinic_id:
            cache.invalidate_services(clinic_id)
            logger.info(f"🗑️  Invalidated cache for clinic {clinic_id}")
        else:
            # Get all active clinics
            result = supabase.table('clinics').select('id').eq('is_active', True).execute()
            clinics = result.data if result.data else []

            logger.info(f"🗑️  Invalidating cache for {len(clinics)} clinics...")
            for clinic in clinics:
                cache.invalidate_services(clinic['id'])

            logger.info(f"✅ Invalidated cache for all {len(clinics)} clinics")

    except Exception as e:
        logger.error(f"❌ Error invalidating cache: {e}")
        raise


async def main():
    parser = argparse.ArgumentParser(
        description="Translate all services using Google Translate Edge Function"
    )
    parser.add_argument(
        '--clinic-id',
        type=str,
        help='Specific clinic ID to process (default: all clinics)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview what would be translated without making changes'
    )
    parser.add_argument(
        '--limit',
        type=int,
        help='Limit number of services to translate (for testing)'
    )
    parser.add_argument(
        '--skip-cache-invalidation',
        action='store_true',
        help='Skip cache invalidation step'
    )

    args = parser.parse_args()

    logger.info("🚀 Starting automatic translation of services...")
    logger.info(f"   Using Google Translate Edge Function at: {os.getenv('SUPABASE_URL')}/functions/v1/translate-service")
    if args.dry_run:
        logger.info("⚠️  DRY RUN MODE - No actual translations will be made")
    if args.limit:
        logger.info(f"   Limiting to {args.limit} services")

    supabase = create_supabase_client()

    # Translate services
    start_time = time.time()
    stats = await translate_all_services(
        supabase,
        clinic_id=args.clinic_id,
        dry_run=args.dry_run,
        limit=args.limit
    )
    elapsed = time.time() - start_time

    logger.info(f"\n⏱️  Translation took {elapsed:.2f} seconds")
    logger.info(f"   Average: {elapsed/stats['total_services']:.2f}s per service") if stats['total_services'] > 0 else None

    # Invalidate cache (unless skipped or dry run)
    if not args.dry_run and not args.skip_cache_invalidation and stats['translated_services'] > 0:
        logger.info("\n🔄 Invalidating service cache...")
        await invalidate_service_cache(clinic_id=args.clinic_id)
        logger.info("✅ Cache invalidated - services will reload with new translations")

    logger.info("\n✅ Translation process complete!")

    if args.dry_run:
        logger.info("\n💡 To apply translations, run without --dry-run flag")


if __name__ == "__main__":
    asyncio.run(main())
