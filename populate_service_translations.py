"""
Populate i18n translations for healthcare services

This script adds Russian, Spanish, and Portuguese translations for common dental services.
Run this after applying the i18n migrations to populate translation data.

Usage:
    python3 populate_service_translations.py [--clinic-id CLINIC_ID] [--dry-run]
"""

import asyncio
import argparse
import logging
from typing import Dict, List
from app.database import create_supabase_client
from app.config import get_redis_client
from app.services.clinic_data_cache import ClinicDataCache

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Translation mappings for common dental services
SERVICE_TRANSLATIONS = {
    # Fillings
    "composite filling": {
        "name_ru": "Композитная пломба",
        "name_es": "Empaste de composite",
        "name_pt": "Obturação de resina composta",
        "description_ru": "Пломбирование зуба светоотверждаемым композитным материалом",
        "description_es": "Empaste dental con material composite fotopolimerizable",
        "description_pt": "Obturação dentária com material de resina composta fotopolimerizável"
    },
    "filling": {
        "name_ru": "Пломба",
        "name_es": "Empaste",
        "name_pt": "Obturação",
        "description_ru": "Пломбирование кариозной полости",
        "description_es": "Empaste de cavidad cariosa",
        "description_pt": "Obturação de cavidade cariosa"
    },
    "resin filling": {
        "name_ru": "Пломба из смолы",
        "name_es": "Empaste de resina",
        "name_pt": "Obturação de resina",
        "description_ru": "Пломбирование зуба композитной смолой",
        "description_es": "Empaste dental con resina compuesta",
        "description_pt": "Obturação dentária com resina composta"
    },

    # Cleaning
    "dental cleaning": {
        "name_ru": "Профессиональная чистка зубов",
        "name_es": "Limpieza dental profesional",
        "name_pt": "Limpeza dental profissional",
        "description_ru": "Профессиональная гигиеническая чистка зубов",
        "description_es": "Limpieza dental profesional e higiene oral",
        "description_pt": "Limpeza dental profissional e higiene oral"
    },
    "cleaning": {
        "name_ru": "Чистка зубов",
        "name_es": "Limpieza dental",
        "name_pt": "Limpeza dental",
        "description_ru": "Гигиеническая чистка зубов",
        "description_es": "Limpieza e higiene dental",
        "description_pt": "Limpeza e higiene dental"
    },
    "prophylaxis": {
        "name_ru": "Профилактическая чистка",
        "name_es": "Profilaxis dental",
        "name_pt": "Profilaxia dentária",
        "description_ru": "Профилактическая чистка и полировка зубов",
        "description_es": "Profilaxis y pulido dental profesional",
        "description_pt": "Profilaxia e polimento dental profissional"
    },

    # Whitening
    "teeth whitening": {
        "name_ru": "Отбеливание зубов",
        "name_es": "Blanqueamiento dental",
        "name_pt": "Clareamento dental",
        "description_ru": "Профессиональное отбеливание зубов",
        "description_es": "Blanqueamiento dental profesional",
        "description_pt": "Clareamento dental profissional"
    },
    "whitening": {
        "name_ru": "Отбеливание",
        "name_es": "Blanqueamiento",
        "name_pt": "Clareamento",
        "description_ru": "Косметическое отбеливание зубов",
        "description_es": "Blanqueamiento cosmético dental",
        "description_pt": "Clareamento cosmético dental"
    },

    # Consultation
    "consultation": {
        "name_ru": "Консультация",
        "name_es": "Consulta",
        "name_pt": "Consulta",
        "description_ru": "Первичная консультация и осмотр",
        "description_es": "Consulta inicial y examen",
        "description_pt": "Consulta inicial e exame"
    },
    "dental exam": {
        "name_ru": "Стоматологический осмотр",
        "name_es": "Examen dental",
        "name_pt": "Exame dentário",
        "description_ru": "Комплексный стоматологический осмотр",
        "description_es": "Examen dental completo",
        "description_pt": "Exame dentário completo"
    },

    # X-Ray
    "x-ray": {
        "name_ru": "Рентген",
        "name_es": "Radiografía",
        "name_pt": "Radiografia",
        "description_ru": "Рентгеновский снимок зубов",
        "description_es": "Radiografía dental",
        "description_pt": "Radiografia dentária"
    },
    "panoramic x-ray": {
        "name_ru": "Панорамный снимок",
        "name_es": "Radiografía panorámica",
        "name_pt": "Radiografia panorâmica",
        "description_ru": "Панорамный рентгеновский снимок челюсти",
        "description_es": "Radiografía panorámica de los maxilares",
        "description_pt": "Radiografia panorâmica dos maxilares"
    },

    # Root Canal
    "root canal": {
        "name_ru": "Лечение каналов",
        "name_es": "Endodoncia",
        "name_pt": "Tratamento de canal",
        "description_ru": "Эндодонтическое лечение корневых каналов",
        "description_es": "Tratamiento endodóntico de conductos radiculares",
        "description_pt": "Tratamento endodôntico de canais radiculares"
    },
    "endodontic treatment": {
        "name_ru": "Эндодонтическое лечение",
        "name_es": "Tratamiento endodóntico",
        "name_pt": "Tratamento endodôntico",
        "description_ru": "Лечение корневых каналов зуба",
        "description_es": "Tratamiento de los conductos radiculares",
        "description_pt": "Tratamento dos canais radiculares"
    },

    # Crown
    "crown": {
        "name_ru": "Коронка",
        "name_es": "Corona",
        "name_pt": "Coroa",
        "description_ru": "Зубная коронка",
        "description_es": "Corona dental",
        "description_pt": "Coroa dentária"
    },
    "dental crown": {
        "name_ru": "Зубная коронка",
        "name_es": "Corona dental",
        "name_pt": "Coroa dentária",
        "description_ru": "Установка зубной коронки",
        "description_es": "Colocación de corona dental",
        "description_pt": "Colocação de coroa dentária"
    },

    # Extraction
    "extraction": {
        "name_ru": "Удаление зуба",
        "name_es": "Extracción dental",
        "name_pt": "Extração dentária",
        "description_ru": "Удаление зуба",
        "description_es": "Extracción de diente",
        "description_pt": "Extração de dente"
    },
    "tooth extraction": {
        "name_ru": "Экстракция зуба",
        "name_es": "Extracción de diente",
        "name_pt": "Extração de dente",
        "description_ru": "Хирургическое удаление зуба",
        "description_es": "Extracción quirúrgica de diente",
        "description_pt": "Extração cirúrgica de dente"
    },

    # Implant
    "implant": {
        "name_ru": "Имплантация",
        "name_es": "Implante",
        "name_pt": "Implante",
        "description_ru": "Установка зубного импланта",
        "description_es": "Colocación de implante dental",
        "description_pt": "Colocação de implante dentário"
    },
    "dental implant": {
        "name_ru": "Зубной имплант",
        "name_es": "Implante dental",
        "name_pt": "Implante dentário",
        "description_ru": "Имплантация зуба",
        "description_es": "Implantología dental",
        "description_pt": "Implantodontia"
    }
}


async def update_service_translations(
    supabase,
    clinic_id: str = None,
    dry_run: bool = False
) -> Dict[str, int]:
    """
    Update service translations in the database

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

                # Check if already translated
                if service.get('name_ru') or service.get('name_es'):
                    logger.info(f"⏭️  Skipping '{service['name']}' - already has translations")
                    stats["skipped_services"] += 1
                    continue

                if dry_run:
                    logger.info(f"🔍 [DRY RUN] Would update '{service['name']}' with translations:")
                    for lang, text in translations.items():
                        if 'name' in lang:
                            logger.info(f"   {lang}: {text}")
                    stats["updated_services"] += 1
                else:
                    # Update the service with translations
                    logger.info(f"✅ Updating '{service['name']}' with translations")

                    update_data = {
                        **translations,
                        # Always set name_en from existing name if not in translations
                        'name_en': service['name']
                    }

                    supabase.schema('healthcare').table('services').update(
                        update_data
                    ).eq('id', service['id']).execute()

                    stats["updated_services"] += 1
                    logger.info(f"   ✓ Updated service ID: {service['id']}")

        logger.info(f"\n📈 Summary:")
        logger.info(f"   Total services: {stats['total_services']}")
        logger.info(f"   Matched for translation: {stats['matched_services']}")
        logger.info(f"   Updated: {stats['updated_services']}")
        logger.info(f"   Skipped (already translated): {stats['skipped_services']}")

        return stats

    except Exception as e:
        logger.error(f"❌ Error updating translations: {e}")
        raise


async def invalidate_service_cache(clinic_id: str = None):
    """
    Invalidate service cache to force reload with new i18n fields

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
        description="Populate i18n translations for healthcare services"
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

    logger.info("🚀 Starting service translation population...")
    if args.dry_run:
        logger.info("⚠️  DRY RUN MODE - No changes will be made")

    supabase = create_supabase_client()

    # Update translations
    stats = await update_service_translations(
        supabase,
        clinic_id=args.clinic_id,
        dry_run=args.dry_run
    )

    # Invalidate cache (unless skipped or dry run)
    if not args.dry_run and not args.skip_cache_invalidation:
        logger.info("\n🔄 Invalidating service cache...")
        await invalidate_service_cache(clinic_id=args.clinic_id)
        logger.info("✅ Cache invalidated - services will reload with i18n fields")

    logger.info("\n✅ Translation population complete!")

    if args.dry_run:
        logger.info("\n💡 To apply these changes, run without --dry-run flag")


if __name__ == "__main__":
    asyncio.run(main())
