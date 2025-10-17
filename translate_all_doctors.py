"""
Translate all doctors using Google Translate

This script translates doctor names, specializations, and bios
and populates the i18n JSONB fields, which will trigger automatic
search vector population.

Usage:
    python3 translate_all_doctors.py [--clinic-id CLINIC_ID] [--dry-run] [--limit N]
"""

import asyncio
import argparse
import logging
import os
import time
from typing import Dict, List
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
    'iw': 'hebrew'  # Google Translate uses 'iw' for Hebrew, stored as 'he' in DB
}

# Mapping for database storage (Google uses 'iw', we store as 'he')
LANG_CODE_MAP = {
    'iw': 'he'
}


def translate_text(text: str, source_lang: str, target_lang: str) -> str:
    """
    Translate text using Google Translate

    Args:
        text: Text to translate
        source_lang: Source language code
        target_lang: Target language code

    Returns:
        Translated text
    """
    if not text or text.strip() == '':
        return text

    try:
        translator = GoogleTranslator(source=source_lang, target=target_lang)
        translated = translator.translate(text)
        return translated
    except Exception as e:
        logger.error(f"Translation error ({source_lang} -> {target_lang}): {e}")
        return text  # Return original on error


async def translate_doctor(
    supabase,
    doctor: Dict,
    source_language: str = 'en',
    dry_run: bool = False
) -> bool:
    """
    Translate a single doctor's fields

    Args:
        supabase: Supabase client
        doctor: Doctor record
        source_language: Source language code
        dry_run: If True, don't actually update database

    Returns:
        True if successful, False otherwise
    """
    doctor_id = doctor['id']
    first_name = doctor.get('first_name', '')
    last_name = doctor.get('last_name', '')
    specialization = doctor.get('specialization', '')
    bio = doctor.get('bio')

    # Build i18n objects
    first_name_i18n = {source_language: first_name}
    last_name_i18n = {source_language: last_name}
    specialization_i18n = {source_language: specialization}
    bio_i18n = {source_language: bio} if bio else {}

    # Translate to all target languages
    for lang_code in TARGET_LANGUAGES.keys():
        if lang_code == source_language:
            continue

        # Map language code for DB storage (e.g., 'iw' -> 'he')
        db_lang_code = LANG_CODE_MAP.get(lang_code, lang_code)

        # Translate first name (names often stay the same, but transliterate for some languages)
        if first_name:
            first_name_i18n[db_lang_code] = translate_text(first_name, source_language, lang_code)

        # Translate last name
        if last_name:
            last_name_i18n[db_lang_code] = translate_text(last_name, source_language, lang_code)

        # Translate specialization
        if specialization:
            specialization_i18n[db_lang_code] = translate_text(specialization, source_language, lang_code)

        # Translate bio if exists
        if bio:
            bio_i18n[db_lang_code] = translate_text(bio, source_language, lang_code)

        # Rate limiting
        await asyncio.sleep(0.1)

    if dry_run:
        logger.info(f"   [DRY RUN] Would update i18n fields for Dr. {first_name} {last_name}")
        logger.info(f"      Specialization translations: {list(specialization_i18n.values())}")
        return True

    # Update database
    try:
        update_data = {
            'first_name_i18n': first_name_i18n,
            'last_name_i18n': last_name_i18n,
            'specialization_i18n': specialization_i18n
        }

        if bio:
            update_data['bio_i18n'] = bio_i18n

        supabase.schema('healthcare').table('doctors').update(update_data).eq('id', doctor_id).execute()
        return True

    except Exception as e:
        logger.error(f"   ❌ Error updating doctor {doctor_id}: {e}")
        return False


async def translate_all_doctors(
    supabase,
    clinic_id: str = None,
    dry_run: bool = False,
    limit: int = None
) -> Dict[str, int]:
    """
    Translate all active doctors

    Args:
        supabase: Supabase client
        clinic_id: Optional clinic ID to filter doctors
        dry_run: If True, don't actually update database
        limit: Optional limit on number of doctors to translate

    Returns:
        Dict with counts of translated doctors
    """
    stats = {
        "total_doctors": 0,
        "translated_doctors": 0,
        "skipped_doctors": 0,
        "failed_doctors": 0
    }

    try:
        # Fetch all active doctors
        query = supabase.schema('healthcare').table('doctors').select('*').eq('active', True)
        if clinic_id:
            query = query.eq('clinic_id', clinic_id)
        if limit:
            query = query.limit(limit)

        result = query.execute()
        doctors = result.data if result.data else []
        stats["total_doctors"] = len(doctors)

        logger.info(f"📊 Found {len(doctors)} active doctors to translate")

        for idx, doctor in enumerate(doctors, 1):
            first_name = doctor.get('first_name', '')
            last_name = doctor.get('last_name', '')
            doctor_name = f"Dr. {first_name} {last_name}"

            # Check if already has JSONB translations
            existing_specialization_i18n = doctor.get('specialization_i18n') or {}
            if existing_specialization_i18n and len(existing_specialization_i18n) > 1:
                logger.info(f"[{idx}/{len(doctors)}] ⏭️  Skipping {doctor_name} - already translated")
                stats["skipped_doctors"] += 1
                continue

            logger.info(f"[{idx}/{len(doctors)}] 🔄 Translating {doctor_name}...")

            success = await translate_doctor(
                supabase,
                doctor,
                source_language='en',
                dry_run=dry_run
            )

            if success:
                logger.info(f"   ✅ Translated successfully")
                stats["translated_doctors"] += 1
            else:
                stats["failed_doctors"] += 1

            # Rate limiting between doctors
            await asyncio.sleep(0.5)

        logger.info(f"\n📈 Summary:")
        logger.info(f"   Total doctors: {stats['total_doctors']}")
        logger.info(f"   Translated: {stats['translated_doctors']}")
        logger.info(f"   Skipped (already translated): {stats['skipped_doctors']}")
        logger.info(f"   Failed: {stats['failed_doctors']}")

        return stats

    except Exception as e:
        logger.error(f"❌ Error translating doctors: {e}")
        raise


async def main():
    parser = argparse.ArgumentParser(
        description="Translate all doctors using Google Translate"
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
        help='Limit number of doctors to translate (for testing)'
    )

    args = parser.parse_args()

    logger.info("🚀 Starting automatic translation of doctors...")
    if args.dry_run:
        logger.info("⚠️  DRY RUN MODE - No actual translations will be made")
    if args.limit:
        logger.info(f"   Limiting to {args.limit} doctors")

    supabase = create_supabase_client()

    # Translate doctors
    start_time = time.time()
    stats = await translate_all_doctors(
        supabase,
        clinic_id=args.clinic_id,
        dry_run=args.dry_run,
        limit=args.limit
    )
    elapsed = time.time() - start_time

    logger.info(f"\n⏱️  Translation took {elapsed:.2f} seconds")
    if stats['total_doctors'] > 0:
        logger.info(f"   Average: {elapsed/stats['total_doctors']:.2f}s per doctor")

    logger.info("\n✅ Translation process complete!")
    logger.info("🔍 Search vectors will be automatically generated by database triggers")

    if args.dry_run:
        logger.info("\n💡 To apply translations, run without --dry-run flag")


if __name__ == "__main__":
    asyncio.run(main())
