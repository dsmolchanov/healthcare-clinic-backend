"""
Translate all clinics using Google Translate

This script translates clinic names and addresses and populates
the i18n JSONB fields, which will trigger automatic search vector population.

Usage:
    python3 translate_all_clinics.py [--dry-run] [--limit N]
"""

import asyncio
import argparse
import logging
import os
import time
from typing import Dict
from dotenv import load_dotenv
from deep_translator import GoogleTranslator
from app.database import create_supabase_client

# Load environment variables
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Target languages
TARGET_LANGUAGES = {
    'en': 'english',
    'ru': 'russian',
    'es': 'spanish',
    'pt': 'portuguese',
    'iw': 'hebrew'  # Google Translate uses 'iw' for Hebrew
}

# Mapping for database storage
LANG_CODE_MAP = {'iw': 'he'}


def translate_text(text: str, source_lang: str, target_lang: str) -> str:
    """Translate text using Google Translate"""
    if not text or text.strip() == '':
        return text

    try:
        translator = GoogleTranslator(source=source_lang, target=target_lang)
        translated = translator.translate(text)
        return translated
    except Exception as e:
        logger.error(f"Translation error ({source_lang} -> {target_lang}): {e}")
        return text


async def translate_clinic(
    supabase,
    clinic: Dict,
    source_language: str = 'en',
    dry_run: bool = False
) -> bool:
    """
    Translate a single clinic

    Args:
        supabase: Supabase client
        clinic: Clinic record
        source_language: Source language code
        dry_run: If True, don't actually update database

    Returns:
        True if successful, False otherwise
    """
    clinic_id = clinic['id']
    name = clinic.get('name', '')
    address = clinic.get('address', '')

    # Build i18n objects
    name_i18n = {source_language: name}
    address_i18n = {source_language: address} if address else {}

    # Translate to all target languages
    for lang_code in TARGET_LANGUAGES.keys():
        if lang_code == source_language:
            continue

        db_lang_code = LANG_CODE_MAP.get(lang_code, lang_code)

        if name:
            name_i18n[db_lang_code] = translate_text(name, source_language, lang_code)

        if address:
            address_i18n[db_lang_code] = translate_text(address, source_language, lang_code)

        # Rate limiting
        await asyncio.sleep(0.1)

    if dry_run:
        logger.info(f"   [DRY RUN] Would update i18n fields for clinic: {name}")
        logger.info(f"      Name translations: {list(name_i18n.values())}")
        return True

    # Update database
    try:
        update_data = {
            'name_i18n': name_i18n
        }

        if address:
            update_data['address_i18n'] = address_i18n

        supabase.schema('healthcare').table('clinics').update(update_data).eq('id', clinic_id).execute()
        return True

    except Exception as e:
        logger.error(f"   ❌ Error updating clinic {clinic_id}: {e}")
        return False


async def translate_all_clinics(
    supabase,
    dry_run: bool = False,
    limit: int = None
) -> Dict[str, int]:
    """
    Translate all active clinics

    Args:
        supabase: Supabase client
        dry_run: If True, don't actually update database
        limit: Optional limit on number of clinics to translate

    Returns:
        Dict with counts of translated clinics
    """
    stats = {
        "total_clinics": 0,
        "translated_clinics": 0,
        "skipped_clinics": 0,
        "failed_clinics": 0
    }

    try:
        # Fetch all active clinics
        query = supabase.schema('healthcare').table('clinics').select('*').eq('is_active', True)
        if limit:
            query = query.limit(limit)

        result = query.execute()
        clinics = result.data if result.data else []
        stats["total_clinics"] = len(clinics)

        logger.info(f"📊 Found {len(clinics)} active clinics to translate")

        for idx, clinic in enumerate(clinics, 1):
            name = clinic.get('name', '')

            # Check if already has JSONB translations
            existing_name_i18n = clinic.get('name_i18n') or {}
            if existing_name_i18n and len(existing_name_i18n) > 1:
                logger.info(f"[{idx}/{len(clinics)}] ⏭️  Skipping clinic '{name}' - already translated")
                stats["skipped_clinics"] += 1
                continue

            logger.info(f"[{idx}/{len(clinics)}] 🔄 Translating clinic: '{name}'")

            success = await translate_clinic(
                supabase,
                clinic,
                source_language='en',
                dry_run=dry_run
            )

            if success:
                logger.info(f"   ✅ Translated successfully")
                stats["translated_clinics"] += 1
            else:
                stats["failed_clinics"] += 1

            # Rate limiting between clinics
            await asyncio.sleep(0.5)

        logger.info(f"\n📈 Summary:")
        logger.info(f"   Total clinics: {stats['total_clinics']}")
        logger.info(f"   Translated: {stats['translated_clinics']}")
        logger.info(f"   Skipped (already translated): {stats['skipped_clinics']}")
        logger.info(f"   Failed: {stats['failed_clinics']}")

        return stats

    except Exception as e:
        logger.error(f"❌ Error translating clinics: {e}")
        raise


async def main():
    parser = argparse.ArgumentParser(
        description="Translate all clinics using Google Translate"
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview what would be translated without making changes'
    )
    parser.add_argument(
        '--limit',
        type=int,
        help='Limit number of clinics to translate (for testing)'
    )

    args = parser.parse_args()

    logger.info("🚀 Starting automatic translation of clinics...")
    if args.dry_run:
        logger.info("⚠️  DRY RUN MODE - No actual translations will be made")
    if args.limit:
        logger.info(f"   Limiting to {args.limit} clinics")

    supabase = create_supabase_client()

    # Translate clinics
    start_time = time.time()
    stats = await translate_all_clinics(
        supabase,
        dry_run=args.dry_run,
        limit=args.limit
    )
    elapsed = time.time() - start_time

    logger.info(f"\n⏱️  Translation took {elapsed:.2f} seconds")
    if stats['total_clinics'] > 0:
        logger.info(f"   Average: {elapsed/stats['total_clinics']:.2f}s per clinic")

    logger.info("\n✅ Translation process complete!")
    logger.info("🔍 Search vectors will be automatically generated by database triggers")

    if args.dry_run:
        logger.info("\n💡 To apply translations, run without --dry-run flag")


if __name__ == "__main__":
    asyncio.run(main())
