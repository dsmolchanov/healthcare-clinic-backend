"""
Demo Clinic Helper Utilities

Provides functions to identify and work with the demo clinic system.
"""
from typing import Optional
import logging

logger = logging.getLogger(__name__)

DEMO_CLINIC_SLUG = "demo-healthcare"
DEMO_CLINIC_NAME = "Demo Dental Clinic"


async def get_demo_clinic_id(supabase_client) -> Optional[str]:
    """
    Get the demo clinic ID by looking up the demo organization's clinic.

    Args:
        supabase_client: Supabase client instance

    Returns:
        Demo clinic ID if found, None otherwise
    """
    try:
        # Get demo organization first
        org_result = supabase_client.table('organizations').select(
            'id'
        ).eq('slug', DEMO_CLINIC_SLUG).limit(1).execute()

        if not org_result.data or len(org_result.data) == 0:
            logger.warning(f"Demo organization '{DEMO_CLINIC_SLUG}' not found")
            return None

        org_id = org_result.data[0]['id']

        # Get clinic for this organization
        clinic_result = supabase_client.table('clinics').select(
            'id'
        ).eq('organization_id', org_id).eq('name', DEMO_CLINIC_NAME).limit(1).execute()

        if clinic_result.data and len(clinic_result.data) > 0:
            clinic_id = clinic_result.data[0]['id']
            logger.info(f"Found demo clinic: {clinic_id}")
            return clinic_id

    except Exception as e:
        logger.error(f"Error fetching demo clinic: {e}")

    return None


async def is_demo_clinic(clinic_id: str, supabase_client) -> bool:
    """
    Check if a clinic ID belongs to the demo clinic.

    Args:
        clinic_id: Clinic ID to check
        supabase_client: Supabase client instance

    Returns:
        True if this is the demo clinic, False otherwise
    """
    demo_id = await get_demo_clinic_id(supabase_client)
    return demo_id == clinic_id if demo_id else False


async def get_demo_organization_id(supabase_client) -> Optional[str]:
    """
    Get the demo organization ID.

    Args:
        supabase_client: Supabase client instance

    Returns:
        Demo organization ID if found, None otherwise
    """
    try:
        result = supabase_client.table('organizations').select(
            'id'
        ).eq('slug', DEMO_CLINIC_SLUG).limit(1).execute()

        if result.data and len(result.data) > 0:
            org_id = result.data[0]['id']
            logger.info(f"Found demo organization: {org_id}")
            return org_id

    except Exception as e:
        logger.error(f"Error fetching demo organization: {e}")

    return None


async def is_demo_organization(organization_id: str, supabase_client) -> bool:
    """
    Check if an organization ID belongs to the demo organization.

    Args:
        organization_id: Organization ID to check
        supabase_client: Supabase client instance

    Returns:
        True if this is the demo organization, False otherwise
    """
    demo_id = await get_demo_organization_id(supabase_client)
    return demo_id == organization_id if demo_id else False
