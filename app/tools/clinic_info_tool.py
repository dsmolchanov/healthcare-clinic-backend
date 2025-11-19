"""
Clinic Information Tool

Provides basic information about the clinic including:
- Number of doctors and their specializations
- Clinic hours, location, contact info
- General FAQs
"""

from typing import Dict, Any, List
import logging
import json

logger = logging.getLogger(__name__)


class ClinicInfoTool:
    """Tool for retrieving clinic information"""

    def __init__(self, clinic_id: str, redis_client=None):
        """
        Initialize clinic info tool

        Args:
            clinic_id: Clinic ID
            redis_client: Optional Redis client for caching
        """
        self.clinic_id = clinic_id
        self.redis_client = redis_client
        self.cache = None
        if redis_client:
            from app.services.clinic_data_cache import ClinicDataCache
            self.cache = ClinicDataCache(redis_client, default_ttl=3600)

    async def get_doctor_count(self, supabase_client) -> Dict[str, Any]:
        """
        Get number of doctors and their specializations

        Uses Redis cache if available for faster response
        """
        try:
            # Try cache first
            if self.cache:
                doctors = await self.cache.get_doctors(self.clinic_id, supabase_client)
                logger.debug(f"✅ Using cached doctors for clinic {self.clinic_id}")
            else:
                # Fallback to direct query (prefer is_active, fall back to legacy active)
                try:
                    result = supabase_client.table('doctors').select(
                        'id,first_name,last_name,specialization'
                    ).eq('clinic_id', self.clinic_id).eq('is_active', True).execute()
                    doctors = result.data
                except Exception as e:
                    if 'is_active' in str(e):
                        result = supabase_client.table('doctors').select(
                            'id,first_name,last_name,specialization'
                        ).eq('clinic_id', self.clinic_id).eq('active', True).execute()
                        doctors = result.data
                    else:
                        raise

            total_count = len(doctors)

            # Group by specialization with doctor IDs
            specializations = {}
            doctor_details = []
            for doc in doctors:
                spec = doc.get('specialization', 'General Dentistry')
                if spec not in specializations:
                    specializations[spec] = []
                name = f"{doc.get('first_name', '')} {doc.get('last_name', '')}".strip()
                doctor_id = doc.get('id')

                # Store both name and ID
                specializations[spec].append({
                    'name': name,
                    'id': doctor_id
                })
                doctor_details.append({
                    'name': name,
                    'id': doctor_id,
                    'specialization': spec
                })

            return {
                'total_doctors': total_count,
                'specializations': specializations,
                'doctor_list': [
                    f"{d.get('first_name', '')} {d.get('last_name', '')}".strip()
                    for d in doctors
                ],
                'doctor_details': doctor_details  # NEW: Full doctor details with IDs
            }
        except Exception as e:
            logger.error(f"Error getting doctor count: {e}")
            return {'total_doctors': 0, 'specializations': {}, 'doctor_list': []}

    async def get_clinic_info(self, supabase_client) -> Dict[str, Any]:
        """Get general clinic information"""
        try:
            # Check Redis cache first
            if self.redis_client:
                cache_key = f"clinic:{self.clinic_id}:info:v2"  # v2 includes city/state/country
                try:
                    cached_data = self.redis_client.get(cache_key)
                    if cached_data:
                        clinic_info = json.loads(cached_data)
                        # Validate cache has required fields (city, state, country)
                        if 'city' in clinic_info and 'state' in clinic_info and 'country' in clinic_info:
                            logger.debug(f"✅ Using cached clinic info v2 for {self.clinic_id}")
                            return clinic_info
                        else:
                            logger.info(f"⚠️ Cache invalid (missing fields), refreshing for {self.clinic_id}")
                except Exception as cache_error:
                    logger.warning(f"Redis cache error, falling back to DB: {cache_error}")

            # Cache miss or no Redis - fetch from database
            result = (
                supabase_client
                .schema('healthcare')
                .table('clinics')
                .select('id,name,address,city,state,country,phone,email,timezone,business_hours,settings')
                .eq('id', self.clinic_id)
                .limit(1)
                .execute()
            )

            if not result.data:
                return {}

            clinic = result.data[0]
            city = clinic.get('city') or ''
            state = clinic.get('state') or ''
            country = clinic.get('country') or ''

            location_parts = [part for part in [city, state, country] if part]
            settings = clinic.get('settings') or {}
            if isinstance(settings, str):
                try:
                    settings = json.loads(settings)
                except Exception:
                    settings = {}
            supported_languages = (
                clinic.get('supported_languages')
                or settings.get('supported_languages')
                or ['en']
            )
            business_hours = clinic.get('business_hours') or {}
            if isinstance(business_hours, str):
                try:
                    business_hours = json.loads(business_hours)
                except Exception:
                    business_hours = {}

            clinic_info = {
                'name': clinic.get('name', ''),
                'address': clinic.get('address', ''),
                'city': city,
                'state': state,
                'country': country,
                'phone': clinic.get('phone', ''),
                'email': clinic.get('email', ''),
                'hours': business_hours,
                'timezone': clinic.get('timezone'),
                'languages': supported_languages,
                'location': ', '.join(location_parts) if location_parts else clinic.get('timezone', '')
            }

            # Cache the result for future use
            if self.redis_client:
                try:
                    cache_key = f"clinic:{self.clinic_id}:info:v2"  # v2 includes city/state/country
                    self.redis_client.setex(cache_key, 3600, json.dumps(clinic_info))
                    logger.debug(f"✅ Cached clinic info v2 for {self.clinic_id}")
                except Exception as cache_error:
                    logger.warning(f"Failed to cache clinic info: {cache_error}")

            return clinic_info
        except Exception as e:
            logger.error(f"Error getting clinic info: {e}")
            return {}


async def format_doctor_info_for_prompt(clinic_id: str, supabase_client, redis_client=None) -> str:
    """
    Format doctor information for injection into LLM prompt context.
    Returns a concise summary suitable for system prompt or context.

    Uses Redis caching to avoid repeated database queries.
    Cache TTL: 1 hour (doctors don't change frequently)
    """
    cache_key = f"clinic_doctors:{clinic_id}"

    # Try to get from Redis cache first
    if redis_client:
        try:
            cached_data = redis_client.get(cache_key)
            if cached_data:
                doctor_info = json.loads(cached_data)
                logger.info(f"✅ Using cached doctor info for clinic {clinic_id}: {doctor_info}")
            else:
                # Fetch from database and cache
                tool = ClinicInfoTool(clinic_id)
                doctor_info = await tool.get_doctor_count(supabase_client)
                logger.info(f"📊 Fetched doctor info for clinic {clinic_id}: {doctor_info}")
                # Only cache if we have doctors (don't cache empty results)
                if doctor_info.get('total_doctors', 0) > 0:
                    redis_client.setex(cache_key, 3600, json.dumps(doctor_info))
                    logger.info(f"✅ Cached doctor info for clinic {clinic_id}")
                else:
                    logger.warning(f"⚠️ Not caching empty doctor list for clinic {clinic_id}")
        except Exception as e:
            logger.warning(f"Redis cache error, falling back to direct query: {e}")
            tool = ClinicInfoTool(clinic_id)
            doctor_info = await tool.get_doctor_count(supabase_client)
            logger.info(f"📊 Fetched doctor info (no cache) for clinic {clinic_id}: {doctor_info}")
    else:
        # No Redis client, fetch directly
        tool = ClinicInfoTool(clinic_id)
        doctor_info = await tool.get_doctor_count(supabase_client)
        logger.info(f"📊 Fetched doctor info (no redis) for clinic {clinic_id}: {doctor_info}")

    total = doctor_info['total_doctors']
    specs = doctor_info['specializations']

    logger.info(f"Doctor count: {total}, Specializations: {list(specs.keys()) if specs else 'none'}")

    if total == 0:
        logger.warning(f"⚠️ No doctors found for clinic {clinic_id}, returning empty string")
        return ""

    # Create detailed summary with doctor names
    summary_parts = [f"The clinic has {total} doctor{'s' if total > 1 else ''}"]

    # Add doctor names by specialization for better context
    if specs:
        summary_parts.append(":\n")
        for spec, names in specs.items():
            if names:
                summary_parts.append(f"  • {spec}: {', '.join(names)}\n")
    else:
        # Fallback: just list all doctors if no specialization grouping
        if doctor_info['doctor_list']:
            summary_parts.append(f": {', '.join(doctor_info['doctor_list'])}")
        summary_parts.append(".")

    return "".join(summary_parts)
