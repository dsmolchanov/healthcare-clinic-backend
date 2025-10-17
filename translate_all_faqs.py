"""
Translate all FAQs using Google Translate

This script translates FAQ questions and answers and populates
the i18n JSONB fields, which will trigger automatic search vector population.

Usage:
    python3 translate_all_faqs.py [--clinic-id CLINIC_ID] [--dry-run] [--limit N]
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


async def translate_faq(
    supabase,
    faq: Dict,
    source_language: str = 'en',
    dry_run: bool = False
) -> bool:
    """
    Translate a single FAQ

    Args:
        supabase: Supabase client
        faq: FAQ record
        source_language: Source language code
        dry_run: If True, don't actually update database

    Returns:
        True if successful, False otherwise
    """
    faq_id = faq['id']
    question = faq.get('question', '')
    answer = faq.get('answer', '')

    # Build i18n objects
    question_i18n = {source_language: question}
    answer_i18n = {source_language: answer}

    # Translate to all target languages
    for lang_code in TARGET_LANGUAGES.keys():
        if lang_code == source_language:
            continue

        db_lang_code = LANG_CODE_MAP.get(lang_code, lang_code)

        if question:
            question_i18n[db_lang_code] = translate_text(question, source_language, lang_code)

        if answer:
            answer_i18n[db_lang_code] = translate_text(answer, source_language, lang_code)

        # Rate limiting
        await asyncio.sleep(0.1)

    if dry_run:
        logger.info(f"   [DRY RUN] Would update i18n fields for FAQ: {question[:50]}...")
        return True

    # Update database
    try:
        supabase.schema('public').table('faqs').update({
            'question_i18n': question_i18n,
            'answer_i18n': answer_i18n
        }).eq('id', faq_id).execute()
        return True

    except Exception as e:
        logger.error(f"   ❌ Error updating FAQ {faq_id}: {e}")
        return False


async def translate_all_faqs(
    supabase,
    clinic_id: str = None,
    dry_run: bool = False,
    limit: int = None
) -> Dict[str, int]:
    """
    Translate all active FAQs

    Args:
        supabase: Supabase client
        clinic_id: Optional clinic ID to filter FAQs
        dry_run: If True, don't actually update database
        limit: Optional limit on number of FAQs to translate

    Returns:
        Dict with counts of translated FAQs
    """
    stats = {
        "total_faqs": 0,
        "translated_faqs": 0,
        "skipped_faqs": 0,
        "failed_faqs": 0
    }

    try:
        # Fetch all active FAQs (public schema)
        query = supabase.schema('public').table('faqs').select('*').eq('is_active', True)
        if clinic_id:
            query = query.eq('clinic_id', clinic_id)
        if limit:
            query = query.limit(limit)

        result = query.execute()
        faqs = result.data if result.data else []
        stats["total_faqs"] = len(faqs)

        logger.info(f"📊 Found {len(faqs)} active FAQs to translate")

        for idx, faq in enumerate(faqs, 1):
            question = faq.get('question', '')[:60]

            # Check if already has JSONB translations
            existing_question_i18n = faq.get('question_i18n') or {}
            if existing_question_i18n and len(existing_question_i18n) > 1:
                logger.info(f"[{idx}/{len(faqs)}] ⏭️  Skipping FAQ '{question}...' - already translated")
                stats["skipped_faqs"] += 1
                continue

            logger.info(f"[{idx}/{len(faqs)}] 🔄 Translating FAQ: '{question}...'")

            success = await translate_faq(
                supabase,
                faq,
                source_language='en',
                dry_run=dry_run
            )

            if success:
                logger.info(f"   ✅ Translated successfully")
                stats["translated_faqs"] += 1
            else:
                stats["failed_faqs"] += 1

            # Rate limiting between FAQs
            await asyncio.sleep(0.5)

        logger.info(f"\n📈 Summary:")
        logger.info(f"   Total FAQs: {stats['total_faqs']}")
        logger.info(f"   Translated: {stats['translated_faqs']}")
        logger.info(f"   Skipped (already translated): {stats['skipped_faqs']}")
        logger.info(f"   Failed: {stats['failed_faqs']}")

        return stats

    except Exception as e:
        logger.error(f"❌ Error translating FAQs: {e}")
        raise


async def main():
    parser = argparse.ArgumentParser(
        description="Translate all FAQs using Google Translate"
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
        help='Limit number of FAQs to translate (for testing)'
    )

    args = parser.parse_args()

    logger.info("🚀 Starting automatic translation of FAQs...")
    if args.dry_run:
        logger.info("⚠️  DRY RUN MODE - No actual translations will be made")
    if args.limit:
        logger.info(f"   Limiting to {args.limit} FAQs")

    supabase = create_supabase_client()

    # Translate FAQs
    start_time = time.time()
    stats = await translate_all_faqs(
        supabase,
        clinic_id=args.clinic_id,
        dry_run=args.dry_run,
        limit=args.limit
    )
    elapsed = time.time() - start_time

    logger.info(f"\n⏱️  Translation took {elapsed:.2f} seconds")
    if stats['total_faqs'] > 0:
        logger.info(f"   Average: {elapsed/stats['total_faqs']:.2f}s per FAQ")

    logger.info("\n✅ Translation process complete!")
    logger.info("🔍 Search vectors will be automatically generated by database triggers")

    if args.dry_run:
        logger.info("\n💡 To apply translations, run without --dry-run flag")


if __name__ == "__main__":
    asyncio.run(main())
