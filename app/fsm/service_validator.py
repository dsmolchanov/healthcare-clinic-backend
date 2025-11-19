"""
Service Name Validation

Validates user-provided service names against the services catalog,
handling multilingual names and fuzzy matching.
"""

import logging
from typing import Tuple, Optional
from supabase import Client

logger = logging.getLogger(__name__)


class ServiceValidator:
    """Validates service names and resolves to service IDs"""

    def __init__(self, supabase: Client):
        self.supabase = supabase

    async def validate_service_name(
        self,
        service_name: str,
        clinic_id: str
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Validate service name and return service UUID.

        Args:
            service_name: User-provided service name (e.g., "чистка зубов")
            clinic_id: Clinic UUID

        Returns:
            Tuple of (is_valid, error_message, service_id)
        """
        # Query services table with i18n support
        result = self.supabase.table('services') \
            .select('id, name, name_ru, name_en, name_es') \
            .eq('clinic_id', clinic_id) \
            .eq('is_active', True) \
            .execute()

        if not result.data:
            return False, "Каталог услуг пуст", None

        # Fuzzy match (case-insensitive, partial match across all languages)
        service_name_lower = service_name.lower()

        for service in result.data:
            # Check all i18n name fields
            names_to_check = [
                service.get('name', ''),
                service.get('name_ru', ''),
                service.get('name_en', ''),
                service.get('name_es', ''),
            ]

            for name in names_to_check:
                if not name:
                    continue
                name_lower = name.lower()

                # Partial match in either direction
                if service_name_lower in name_lower or name_lower in service_name_lower:
                    logger.info(
                        f"Service '{service_name}' validated successfully, "
                        f"id={service['id']}, matched_name={name}"
                    )
                    return True, None, service['id']

        # Not found - return list of available services
        available = ", ".join([
            s.get('name_ru') or s.get('name', '')
            for s in result.data[:5]
        ])
        error_msg = f"Услуга '{service_name}' не найдена. Доступны: {available}"

        logger.warning(f"Service '{service_name}' not found in catalog")
        return False, error_msg, None
