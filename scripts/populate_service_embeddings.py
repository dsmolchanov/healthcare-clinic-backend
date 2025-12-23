#!/usr/bin/env python3
"""Populate service embeddings for semantic search.

Run with: python -m scripts.populate_service_embeddings --clinic-id <UUID>

This script:
1. Fetches all active services for a clinic
2. Generates embeddings for each language's name
3. Stores embeddings in service_semantic_index table
"""
import os
import sys
import argparse
import logging
from typing import List, Dict, Any

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from supabase import create_client

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Languages to generate embeddings for
LANGUAGES = ['en', 'ru', 'es', 'pt', 'he']


def get_supabase_client():
    """Get Supabase client with service key for admin access."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_KEY required")
    return create_client(url, key)


def get_translation(service: Dict, field: str, language: str, fallback_languages: List[str] = None) -> str:
    """Get translated value from JSONB or columnar fields."""
    # Try JSONB first
    i18n_field = f"{field}_i18n"
    if i18n_field in service and service[i18n_field]:
        i18n_data = service[i18n_field]
        if isinstance(i18n_data, dict):
            if language in i18n_data:
                return i18n_data[language]
            # Try fallbacks
            if fallback_languages:
                for fb_lang in fallback_languages:
                    if fb_lang in i18n_data:
                        return i18n_data[fb_lang]

    # Try columnar field
    lang_field = f"{field}_{language}"
    if lang_field in service and service[lang_field]:
        return service[lang_field]

    # Fallback to base field
    if field in service and service[field]:
        return service[field]

    return ""


def fetch_services(client, clinic_id: str) -> List[Dict[str, Any]]:
    """Fetch all active services for a clinic."""
    response = client.schema('healthcare').from_('services').select(
        'id, name, name_ru, name_en, name_es, name_pt, name_he, name_i18n'
    ).eq('clinic_id', clinic_id).eq('is_active', True).execute()
    return response.data or []


def generate_and_store_embeddings(client, services: List[Dict], clinic_id: str):
    """Generate embeddings for all services and store in database."""
    from app.utils.embedding_utils import get_embedding_generator

    generator = get_embedding_generator()

    # Collect all texts to embed
    embedding_tasks = []  # (service_id, language, text)

    for service in services:
        for lang in LANGUAGES:
            # Get translated name using i18n pattern
            text = get_translation(service, 'name', lang, fallback_languages=['en'])
            if text:
                embedding_tasks.append((service['id'], lang, text))

    logger.info(f"Generating {len(embedding_tasks)} embeddings for {len(services)} services...")

    # Generate embeddings in batch
    texts = [task[2] for task in embedding_tasks]
    embeddings = generator.generate_batch(texts)

    # Store embeddings
    success_count = 0
    error_count = 0

    for i, (service_id, lang, text) in enumerate(embedding_tasks):
        embedding = embeddings[i]

        if embedding.sum() == 0:
            logger.warning(f"Skipping zero embedding for service {service_id} lang {lang}")
            error_count += 1
            continue

        # Upsert into semantic index
        try:
            client.schema('healthcare').from_('service_semantic_index').upsert({
                'service_id': service_id,
                'language': lang,
                'embedding': embedding.tolist(),
                'embedded_text': text,
                'model_version': 'text-embedding-3-small'
            }, on_conflict='service_id,language').execute()

            logger.debug(f"Stored embedding for {service_id}/{lang}: '{text[:30]}...'")
            success_count += 1

        except Exception as e:
            logger.error(f"Failed to store embedding for {service_id}/{lang}: {e}")
            error_count += 1

    logger.info(f"Completed: {success_count} embeddings stored, {error_count} errors")


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description='Populate service embeddings for semantic search')
    parser.add_argument('--clinic-id', required=True, help='Clinic UUID')
    parser.add_argument('--dry-run', action='store_true', help='Only show what would be done')
    args = parser.parse_args()

    client = get_supabase_client()

    logger.info(f"Fetching services for clinic {args.clinic_id}")
    services = fetch_services(client, args.clinic_id)
    logger.info(f"Found {len(services)} active services")

    if args.dry_run:
        logger.info("DRY RUN - would generate embeddings for:")
        for service in services[:5]:
            for lang in LANGUAGES:
                text = get_translation(service, 'name', lang, fallback_languages=['en'])
                if text:
                    logger.info(f"  {service['id']}/{lang}: '{text}'")
        if len(services) > 5:
            logger.info(f"  ... and {len(services) - 5} more services")
        return

    if services:
        generate_and_store_embeddings(client, services, args.clinic_id)
    else:
        logger.warning("No services found for clinic")


if __name__ == '__main__':
    main()
