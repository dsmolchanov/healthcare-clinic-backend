"""
Seed JSONB i18n translations for healthcare services

This script populates the name_i18n and description_i18n JSONB fields
with translations for common dental services.

Usage:
    python3 seed_jsonb_translations.py [--clinic-id CLINIC_ID] [--dry-run]
"""

import asyncio
import argparse
import logging
import json
import os
from typing import Dict, List
from dotenv import load_dotenv
from app.database import create_supabase_client
from app.config import get_redis_client
from app.services.clinic_data_cache import ClinicDataCache
from app.utils.i18n_helpers import merge_translations

# Load environment variables
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Translation mappings for common dental services
# Structure: {service_name_lower: {"name": {lang: text}, "description": {lang: text}}}
SERVICE_TRANSLATIONS = {
    # Fillings
    "composite filling": {
        "name": {"ru": "Композитная пломба", "es": "Empaste de composite", "pt": "Obturação de resina composta", "he": "סתימת קומפוזיט"},
        "description": {"ru": "Пломбирование зуба светоотверждаемым композитным материалом", "es": "Empaste dental con material composite fotopolimerizable", "pt": "Obturação dentária com material de resina composta fotopolimerizável"}
    },
    "filling": {
        "name": {"ru": "Пломба", "es": "Empaste", "pt": "Obturação", "he": "סתימה"},
        "description": {"ru": "Пломбирование кариозной полости", "es": "Empaste de cavidad cariosa", "pt": "Obturação de cavidade cariosa"}
    },
    "resin filling": {
        "name": {"ru": "Пломба из смолы", "es": "Empaste de resina", "pt": "Obturação de resina"},
        "description": {"ru": "Пломбирование зуба композитной смолой", "es": "Empaste dental con resina compuesta", "pt": "Obturação dentária com resina composta"}
    },

    # Cleaning
    "dental cleaning": {
        "name": {"ru": "Профессиональная чистка зубов", "es": "Limpieza dental profesional", "pt": "Limpeza dental profissional", "he": "ניקוי שיניים מקצועי"},
        "description": {"ru": "Профессиональная гигиеническая чистка зубов", "es": "Limpieza dental profesional e higiene oral", "pt": "Limpeza dental profissional e higiene oral"}
    },
    "cleaning": {
        "name": {"ru": "Чистка зубов", "es": "Limpieza dental", "pt": "Limpeza dental", "he": "ניקוי שיניים"},
        "description": {"ru": "Гигиеническая чистка зубов", "es": "Limpieza e higiene dental", "pt": "Limpeza e higiene dental"}
    },
    "prophylaxis": {
        "name": {"ru": "Профилактическая чистка", "es": "Profilaxis dental", "pt": "Profilaxia dentária"},
        "description": {"ru": "Профилактическая чистка и полировка зубов", "es": "Profilaxis y pulido dental profesional", "pt": "Profilaxia e polimento dental profissional"}
    },

    # Whitening
    "teeth whitening": {
        "name": {"ru": "Отбеливание зубов", "es": "Blanqueamiento dental", "pt": "Clareamento dental", "he": "הלבנת שיניים"},
        "description": {"ru": "Профессиональное отбеливание зубов", "es": "Blanqueamiento dental profesional", "pt": "Clareamento dental profissional"}
    },
    "whitening": {
        "name": {"ru": "Отбеливание", "es": "Blanqueamiento", "pt": "Clareamento"},
        "description": {"ru": "Косметическое отбеливание зубов", "es": "Blanqueamiento cosmético dental", "pt": "Clareamento cosmético dental"}
    },

    # Consultation
    "consultation": {
        "name": {"ru": "Консультация", "es": "Consulta", "pt": "Consulta", "he": "ייעוץ"},
        "description": {"ru": "Первичная консультация и осмотр", "es": "Consulta inicial y examen", "pt": "Consulta inicial e exame"}
    },
    "dental exam": {
        "name": {"ru": "Стоматологический осмотр", "es": "Examen dental", "pt": "Exame dentário"},
        "description": {"ru": "Комплексный стоматологический осмотр", "es": "Examen dental completo", "pt": "Exame dentário completo"}
    },

    # X-Ray
    "x-ray": {
        "name": {"ru": "Рентген", "es": "Radiografía", "pt": "Radiografia", "he": "צילום רנטגן"},
        "description": {"ru": "Рентгеновский снимок зубов", "es": "Radiografía dental", "pt": "Radiografia dentária"}
    },
    "panoramic x-ray": {
        "name": {"ru": "Панорамный снимок", "es": "Radiografía panorámica", "pt": "Radiografia panorâmica"},
        "description": {"ru": "Панорамный рентгеновский снимок челюсти", "es": "Radiografía panorámica de los maxilares", "pt": "Radiografia panorâmica dos maxilares"}
    },

    # Root Canal
    "root canal": {
        "name": {"ru": "Лечение каналов", "es": "Endodoncia", "pt": "Tratamento de canal", "he": "טיפול שורש"},
        "description": {"ru": "Эндодонтическое лечение корневых каналов", "es": "Tratamiento endodóntico de conductos radiculares", "pt": "Tratamento endodôntico de canais radiculares"}
    },
    "endodontic treatment": {
        "name": {"ru": "Эндодонтическое лечение", "es": "Tratamiento endodóntico", "pt": "Tratamento endodôntico"},
        "description": {"ru": "Лечение корневых каналов зуба", "es": "Tratamiento de los conductos radiculares", "pt": "Tratamento dos canais radiculares"}
    },

    # Crown
    "crown": {
        "name": {"ru": "Коронка", "es": "Corona", "pt": "Coroa", "he": "כתר"},
        "description": {"ru": "Зубная коронка", "es": "Corona dental", "pt": "Coroa dentária"}
    },
    "dental crown": {
        "name": {"ru": "Зубная коронка", "es": "Corona dental", "pt": "Coroa dentária"},
        "description": {"ru": "Установка зубной коронки", "es": "Colocación de corona dental", "pt": "Colocação de coroa dentária"}
    },

    # Extraction
    "extraction": {
        "name": {"ru": "Удаление зуба", "es": "Extracción dental", "pt": "Extração dentária", "he": "עקירת שן"},
        "description": {"ru": "Удаление зуба", "es": "Extracción de diente", "pt": "Extração de dente"}
    },
    "tooth extraction": {
        "name": {"ru": "Экстракция зуба", "es": "Extracción de diente", "pt": "Extração de dente"},
        "description": {"ru": "Хирургическое удаление зуба", "es": "Extracción quirúrgica de diente", "pt": "Extração cirúrgica de dente"}
    },

    # Implant
    "implant": {
        "name": {"ru": "Имплантация", "es": "Implante", "pt": "Implante", "he": "שתל"},
        "description": {"ru": "Установка зубного импланта", "es": "Colocación de implante dental", "pt": "Colocação de implante dentário"}
    },
    "dental implant": {
        "name": {"ru": "Зубной имплант", "es": "Implante dental", "pt": "Implante dentário"},
        "description": {"ru": "Имплантация зуба", "es": "Implantología dental", "pt": "Implantodontia"}
    },

    # Orthodontics
    "braces": {
        "name": {"ru": "Брекеты", "es": "Ortodoncia", "pt": "Aparelho ortodôntico", "he": "גשר"},
        "description": {"ru": "Установка и коррекция брекет-системы", "es": "Colocación y ajuste de ortodoncia", "pt": "Colocação e ajuste de aparelho ortodôntico"}
    },
}


async def seed_jsonb_translations(
    supabase,
    clinic_id: str = None,
    dry_run: bool = False
) -> Dict[str, int]:
    """
    Seed JSONB i18n fields with translations

    Args:
        supabase: Supabase client
        clinic_id: Optional clinic ID to filter services
        dry_run: If True, don't actually update database

    Returns:
        Dict with counts of updated services
    """
    stats = {
        "total_services": 0,
        "matched_services": 0,
        "updated_services": 0,
        "skipped_services": 0
    }

    try:
        # Fetch all active services
        query = supabase.schema('healthcare').table('services').select('*').eq('active', True)
        if clinic_id:
            query = query.eq('clinic_id', clinic_id)

        result = query.execute()
        services = result.data if result.data else []
        stats["total_services"] = len(services)

        logger.info(f"📊 Found {len(services)} active services to process")

        for service in services:
            service_name = service['name'].lower().strip()

            # Check if we have translations for this service
            if service_name in SERVICE_TRANSLATIONS:
                translations = SERVICE_TRANSLATIONS[service_name]
                stats["matched_services"] += 1

                # Check if already has JSONB translations
                existing_name_i18n = service.get('name_i18n') or {}
                existing_desc_i18n = service.get('description_i18n') or {}

                # Skip if already has translations in JSONB
                if existing_name_i18n and len(existing_name_i18n) > 0:
                    logger.info(f"⏭️  Skipping '{service['name']}' - already has JSONB translations")
                    stats["skipped_services"] += 1
                    continue

                # Build JSONB translations
                name_i18n = translations.get('name', {})
                description_i18n = translations.get('description', {})

                # Add English from existing name/description
                name_i18n['en'] = service['name']
                if service.get('description'):
                    description_i18n['en'] = service['description']

                if dry_run:
                    logger.info(f"🔍 [DRY RUN] Would update '{service['name']}' with JSONB translations:")
                    logger.info(f"   name_i18n: {json.dumps(name_i18n, ensure_ascii=False)}")
                    logger.info(f"   description_i18n: {json.dumps(description_i18n, ensure_ascii=False)}")
                    stats["updated_services"] += 1
                else:
                    # Update the service with JSONB translations
                    logger.info(f"✅ Updating '{service['name']}' with JSONB translations")

                    update_data = {
                        'name_i18n': name_i18n,
                        'description_i18n': description_i18n
                    }

                    supabase.schema('healthcare').table('services').update(
                        update_data
                    ).eq('id', service['id']).execute()

                    stats["updated_services"] += 1
                    logger.info(f"   ✓ Updated service ID: {service['id']}")
                    logger.info(f"   ✓ Languages: {list(name_i18n.keys())}")

        logger.info(f"\n📈 Summary:")
        logger.info(f"   Total services: {stats['total_services']}")
        logger.info(f"   Matched for translation: {stats['matched_services']}")
        logger.info(f"   Updated: {stats['updated_services']}")
        logger.info(f"   Skipped (already translated): {stats['skipped_services']}")

        return stats

    except Exception as e:
        logger.error(f"❌ Error seeding translations: {e}")
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
        description="Seed JSONB i18n translations for healthcare services"
    )
    parser.add_argument(
        '--clinic-id',
        type=str,
        help='Specific clinic ID to process (default: all clinics)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview changes without updating database'
    )
    parser.add_argument(
        '--skip-cache-invalidation',
        action='store_true',
        help='Skip cache invalidation step'
    )

    args = parser.parse_args()

    logger.info("🚀 Starting JSONB translation seeding...")
    if args.dry_run:
        logger.info("⚠️  DRY RUN MODE - No changes will be made")

    supabase = create_supabase_client()

    # Seed translations
    stats = await seed_jsonb_translations(
        supabase,
        clinic_id=args.clinic_id,
        dry_run=args.dry_run
    )

    # Invalidate cache (unless skipped or dry run)
    if not args.dry_run and not args.skip_cache_invalidation:
        logger.info("\n🔄 Invalidating service cache...")
        await invalidate_service_cache(clinic_id=args.clinic_id)
        logger.info("✅ Cache invalidated - services will reload with JSONB i18n fields")

    logger.info("\n✅ JSONB translation seeding complete!")

    if args.dry_run:
        logger.info("\n💡 To apply these changes, run without --dry-run flag")


if __name__ == "__main__":
    asyncio.run(main())
