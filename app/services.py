"""
Service layer for business logic
"""

from typing import Dict, Any, Optional
import time


async def check_service_availability(clinic: Dict[str, Any], service: str) -> bool:
    """
    Check if a service is available at a clinic

    Args:
        clinic: Clinic configuration
        service: Service name

    Returns:
        True if service is available
    """
    available_services = clinic.get('services', [])
    return service.lower() in [s.lower() for s in available_services]


async def get_clinic_info(clinic_id: str) -> Dict[str, Any]:
    """
    Get clinic information

    Args:
        clinic_id: Clinic identifier

    Returns:
        Clinic information dictionary
    """
    # Check cache first
    cache_key = f"clinic_info:{clinic_id}"
    # Mock cache check

    # If not in cache, fetch from database
    from .database import db

    result = await db.table('healthcare.clinics')\
        .select('*')\
        .eq('id', clinic_id)\
        .single()\
        .execute()

    return result.data if result else {}


class ServiceInfoProvider:
    """Provide service information with caching"""

    def __init__(self, cache=None):
        self.cache = cache or {}

    async def get_info(self, clinic_id: str, info_type: str) -> Dict[str, Any]:
        """Get information with caching"""
        cache_key = f"{clinic_id}:{info_type}"

        # Check cache
        if cache_key in self.cache:
            return self.cache[cache_key]

        # Fetch information
        info = await self._fetch_info(clinic_id, info_type)

        # Store in cache
        self.cache[cache_key] = info

        return info

    async def _fetch_info(self, clinic_id: str, info_type: str) -> Dict[str, Any]:
        """Fetch information from source"""
        # Mock implementation
        if info_type == 'business_hours':
            return {
                'monday': {'open': '09:00', 'close': '18:00'},
                'tuesday': {'open': '09:00', 'close': '18:00'},
                'wednesday': {'open': '09:00', 'close': '18:00'},
                'thursday': {'open': '09:00', 'close': '18:00'},
                'friday': {'open': '09:00', 'close': '18:00'},
                'saturday': {'open': '09:00', 'close': '14:00'},
                'sunday': 'closed'
            }
        elif info_type == 'services':
            return {
                'services': ['cleaning', 'filling', 'extraction', 'root_canal', 'crown']
            }
        elif info_type == 'prices':
            return {
                'cleaning': 600,
                'filling': 800,
                'extraction': 1000
            }

        return {}


# I18n support
async def get_message(key: str, lang: str = 'es') -> str:
    """
    Get localized message

    Args:
        key: Message key
        lang: Language code

    Returns:
        Localized message
    """
    messages = {
        'es': {
            'appointment_confirmed': 'Su cita ha sido confirmada',
            'appointment_cancelled': 'Su cita ha sido cancelada',
            'welcome': 'Bienvenido a nuestra clínica dental'
        },
        'en': {
            'appointment_confirmed': 'Your appointment has been confirmed',
            'appointment_cancelled': 'Your appointment has been cancelled',
            'welcome': 'Welcome to our dental clinic'
        }
    }

    return messages.get(lang, {}).get(key, key)
