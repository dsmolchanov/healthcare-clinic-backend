#!/usr/bin/env python3
"""
Backfill patient records from existing WhatsApp conversation sessions
Creates patient records for all phone numbers that have had conversations
"""

import os
import asyncio
from supabase import create_client
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def backfill_patients():
    """Create patient records for all existing conversation sessions"""

    # Initialize Supabase client
    url = os.getenv('SUPABASE_URL', 'https://wojtrbcbezpfwksedjmy.supabase.co')
    key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

    if not key:
        raise ValueError("SUPABASE_SERVICE_ROLE_KEY environment variable not set")

    supabase = create_client(url, key)

    logger.info("🔍 Fetching clinic mapping...")

    # Get all clinics and map organization_id to clinic_id
    clinics_result = supabase.table('clinics').select('id, organization_id, name').execute()
    org_to_clinic = {}
    for clinic in clinics_result.data:
        org_id = clinic.get('organization_id')
        clinic_id = clinic.get('id')
        if org_id and clinic_id:
            org_to_clinic[org_id] = clinic_id
            logger.info(f"  Mapped org {org_id[:8]}... -> clinic {clinic_id[:8]}... ({clinic.get('name')})")

    if not org_to_clinic:
        logger.warning("⚠️  No clinic mappings found. Using first clinic as default.")
        # Fallback: use the first clinic
        first_clinic = clinics_result.data[0] if clinics_result.data else None
        if first_clinic:
            default_clinic_id = first_clinic['id']
            logger.info(f"  Using default clinic: {default_clinic_id} ({first_clinic.get('name')})")
        else:
            raise ValueError("No clinics found in database!")
    else:
        default_clinic_id = list(org_to_clinic.values())[0]

    logger.info(f"\n🔍 Fetching all conversation sessions with phone numbers...")

    # Get all conversation sessions with valid phone numbers (WhatsApp channel)
    sessions_result = supabase.table('conversation_sessions').select(
        'user_identifier, organization_id, channel_type, metadata, created_at'
    ).eq('channel_type', 'whatsapp').execute()

    sessions = sessions_result.data
    logger.info(f"Found {len(sessions)} WhatsApp conversation sessions")

    # Group by phone number and clinic (mapped from organization)
    phone_clinic_map = {}
    for session in sessions:
        phone = session.get('user_identifier', '').replace('@s.whatsapp.net', '')
        org_id = session.get('organization_id')

        # Skip invalid entries
        if not phone or phone == 'unknown':
            continue

        # Map organization_id to clinic_id
        clinic_id = org_to_clinic.get(org_id, default_clinic_id)

        key = (phone, clinic_id)
        if key not in phone_clinic_map:
            phone_clinic_map[key] = {
                'phone': phone,
                'clinic_id': clinic_id,
                'metadata': session.get('metadata', {}),
                'created_at': session.get('created_at')
            }

    logger.info(f"Found {len(phone_clinic_map)} unique phone/clinic combinations")

    # Process each unique phone number
    created_count = 0
    updated_count = 0
    error_count = 0

    for (phone, clinic_id), info in phone_clinic_map.items():
        try:
            # Extract profile name from metadata if available
            metadata = info.get('metadata', {})
            profile_name = metadata.get('profile_name') or metadata.get('from_name')

            logger.info(f"Processing: {phone} (clinic: {clinic_id[:8]}..., profile: {profile_name})")

            # Call the RPC function to upsert patient
            result = supabase.rpc('upsert_patient_from_whatsapp', {
                'p_clinic_id': clinic_id,
                'p_phone': phone,
                'p_first_name': None,
                'p_last_name': None,
                'p_profile_name': profile_name,
                'p_preferred_language': 'English'  # Default, will be updated on next message
            }).execute()

            if result.data and len(result.data) > 0:
                patient_info = result.data[0]
                if patient_info.get('is_new'):
                    created_count += 1
                    logger.info(f"  ✅ Created patient: {patient_info.get('patient_id')}")
                else:
                    updated_count += 1
                    updated_fields = patient_info.get('updated_fields', [])
                    logger.info(f"  ✅ Updated patient (fields: {', '.join(updated_fields)})")

        except Exception as e:
            error_count += 1
            logger.error(f"  ❌ Error processing {phone}: {e}")

    logger.info(f"""
╔══════════════════════════════════════════════╗
║         BACKFILL COMPLETE                     ║
╠══════════════════════════════════════════════╣
║  Total Sessions Processed: {len(phone_clinic_map):>15} ║
║  Patients Created:        {created_count:>15} ║
║  Patients Updated:        {updated_count:>15} ║
║  Errors:                  {error_count:>15} ║
╚══════════════════════════════════════════════╝
    """)

    # Show sample of created patients
    if created_count > 0:
        logger.info("\n📋 Sample of newly created patients:")
        patients = supabase.table('patients').select(
            'first_name, last_name, phone, preferred_language'
        ).order('created_at', desc=True).limit(5).execute()

        for p in patients.data:
            logger.info(f"  - {p.get('first_name')} {p.get('last_name')} | {p.get('phone')} | {p.get('preferred_language')}")

if __name__ == '__main__':
    asyncio.run(backfill_patients())
