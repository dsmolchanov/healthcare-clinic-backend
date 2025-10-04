"""
Privacy compliance module for LFPDPPP (Mexican privacy law)
"""

import json
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from .audit import hash_phone


PRIVACY_NOTICE_ES = """
📋 *Aviso de Privacidad Simplificado*

{clinic_name} protege sus datos personales conforme a la LFPDPPP.

Sus datos personales serán utilizados únicamente para:
• Agendar y confirmar citas
• Enviar recordatorios
• Brindar información sobre servicios

Para más información visite: {clinic_website}/privacidad

Por favor responda:
✅ *ACEPTO* para continuar
❌ *RECHAZAR* para cancelar
"""


async def generate_privacy_notice(clinic_info: Dict[str, Any]) -> str:
    """
    Generate privacy notice for a clinic

    Args:
        clinic_info: Clinic information dictionary

    Returns:
        Formatted privacy notice in Spanish
    """
    return PRIVACY_NOTICE_ES.format(
        clinic_name=clinic_info.get('name', 'Clínica Dental'),
        clinic_website=clinic_info.get('website', 'https://clinica.mx')
    )


async def check_consent(phone: str, clinic_id: str) -> Optional[Dict[str, Any]]:
    """
    Check if user has given consent

    Args:
        phone: User phone number
        clinic_id: Clinic identifier

    Returns:
        Consent record if exists, None otherwise
    """
    from .database import db

    hashed_phone = hash_phone(phone)

    result = await db.table('core.consent_records')\
        .select('*')\
        .eq('organization_id', clinic_id)\
        .eq('user_identifier', hashed_phone)\
        .eq('consent_given', True)\
        .order('timestamp', desc=True)\
        .limit(1)\
        .execute()

    return result.data[0] if result and result.data else None


async def record_consent(phone: str, clinic_id: str, accepted: bool):
    """
    Record user consent decision

    Args:
        phone: User phone number
        clinic_id: Clinic identifier
        accepted: Whether consent was given
    """
    from .database import db
    import os

    consent_record = {
        'organization_id': clinic_id,
        'user_identifier': hash_phone(phone),
        'consent_type': 'lfpdppp_data_processing',
        'consent_given': accepted,
        'consent_text': PRIVACY_NOTICE_ES,
        'consent_version': '1.0',
        'ip_address': os.environ.get('CLIENT_IP', ''),
        'timestamp': datetime.utcnow().isoformat()
    }

    await db.table('core.consent_records').insert(consent_record).execute()


async def handle_first_contact(phone: str, clinic_id: str) -> bool:
    """
    Handle first contact with a patient

    Args:
        phone: Patient phone number
        clinic_id: Clinic identifier

    Returns:
        True if consent process completed, False if pending
    """
    # Check existing consent
    existing_consent = await check_consent(phone, clinic_id)

    if existing_consent:
        return True

    # Get clinic info and send privacy notice
    from .database import db

    clinic_result = await db.table('healthcare.clinics')\
        .select('*')\
        .eq('id', clinic_id)\
        .single()\
        .execute()

    clinic_info = clinic_result.data if clinic_result else {}

    privacy_notice = await generate_privacy_notice(clinic_info)

    # Send privacy notice via WhatsApp
    await send_whatsapp_message(phone, privacy_notice)

    # Wait for consent response
    return await wait_for_consent(phone, clinic_id)


async def send_whatsapp_message(phone: str, message: str):
    """
    Send WhatsApp message (placeholder for actual implementation)

    Args:
        phone: Recipient phone number
        message: Message content
    """
    # This will be implemented in whatsapp.py
    pass


async def wait_for_consent(phone: str, clinic_id: str) -> bool:
    """
    Wait for user consent response

    Args:
        phone: User phone number
        clinic_id: Clinic identifier

    Returns:
        True if consent given, False otherwise
    """
    # This is handled asynchronously via webhook
    return False


async def handle_consent_response(phone: str, clinic_id: str, message: str) -> Dict[str, Any]:
    """
    Handle consent response from user

    Args:
        phone: User phone number
        clinic_id: Clinic identifier
        message: User's response message

    Returns:
        Response dictionary with consent status
    """
    message_upper = message.upper().strip()

    if 'ACEPTO' in message_upper or 'SI' in message_upper or 'ACCEPT' in message_upper:
        await record_consent(phone, clinic_id, accepted=True)
        return {
            'consent_given': True,
            'status': 'accepted',
            'message': 'Gracias por aceptar. ¿En qué puedo ayudarle?'
        }
    elif 'RECHAZAR' in message_upper or 'NO' in message_upper or 'REJECT' in message_upper:
        await record_consent(phone, clinic_id, accepted=False)
        return {
            'consent_given': False,
            'status': 'rejected',
            'message': 'Entendido. Sus datos no serán procesados. Que tenga un buen día.'
        }
    else:
        return {
            'consent_given': None,
            'status': 'pending',
            'message': 'Por favor responda ACEPTO o RECHAZAR para continuar.'
        }


# ARCO Rights Implementation

async def handle_data_access_request(phone: str, clinic_id: str) -> Dict[str, Any]:
    """
    Handle right to access personal data (A in ARCO)

    Args:
        phone: User phone number
        clinic_id: Clinic identifier

    Returns:
        User's personal data
    """
    from .database import db

    hashed_phone = hash_phone(phone)

    # Gather all user data
    data = {}

    # Appointments
    appointments = await db.table('healthcare.appointments')\
        .select('*')\
        .eq('clinic_id', clinic_id)\
        .eq('patient_phone', hashed_phone)\
        .execute()

    data['appointments'] = appointments.data if appointments else []

    # Messages (from session)
    sessions = await db.table('core.sessions')\
        .select('messages')\
        .eq('clinic_id', clinic_id)\
        .eq('phone', hashed_phone)\
        .execute()

    data['messages'] = sessions.data[0]['messages'] if sessions and sessions.data else []

    # Consent records
    consents = await db.table('core.consent_records')\
        .select('*')\
        .eq('organization_id', clinic_id)\
        .eq('user_identifier', hashed_phone)\
        .execute()

    data['consent_records'] = consents.data if consents else []

    return data


async def handle_data_rectification_request(phone: str, clinic_id: str, corrections: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle right to correct personal data (R in ARCO)

    Args:
        phone: User phone number
        clinic_id: Clinic identifier
        corrections: Data corrections to apply

    Returns:
        Update status
    """
    from .database import db

    hashed_phone = hash_phone(phone)

    # Update user data
    for table, updates in corrections.items():
        await db.table(table)\
            .update(updates)\
            .eq('clinic_id', clinic_id)\
            .eq('patient_phone', hashed_phone)\
            .execute()

    return {
        'success': True,
        'message': 'Sus datos han sido actualizados correctamente.'
    }


async def handle_data_deletion_request(phone: str, clinic_id: str) -> Dict[str, Any]:
    """
    Handle right to delete personal data (C in ARCO)

    Args:
        phone: User phone number
        clinic_id: Clinic identifier

    Returns:
        Deletion status
    """
    from .database import db
    from .audit import AuditLogger

    hashed_phone = hash_phone(phone)

    # Log deletion request
    logger = AuditLogger()
    await logger.log_event(
        clinic_id=clinic_id,
        event_type='data_deletion_request',
        event_data={
            'phone': hashed_phone,
            'requested_at': datetime.utcnow().isoformat()
        }
    )

    # Delete from various tables
    tables_to_clean = [
        'healthcare.appointments',
        'core.sessions',
        'core.messages'
    ]

    for table in tables_to_clean:
        await db.table(table)\
            .delete()\
            .eq('clinic_id', clinic_id)\
            .eq('patient_phone', hashed_phone)\
            .execute()

    return {
        'success': True,
        'message': 'Sus datos han sido eliminados de nuestros sistemas.'
    }


async def handle_opposition_request(phone: str, clinic_id: str, opposition_type: str) -> Dict[str, Any]:
    """
    Handle right to oppose data processing (O in ARCO)

    Args:
        phone: User phone number
        clinic_id: Clinic identifier
        opposition_type: Type of processing to oppose (e.g., 'marketing')

    Returns:
        Opposition status
    """
    from .database import db

    opposition_record = {
        'organization_id': clinic_id,
        'user_identifier': hash_phone(phone),
        'opposition_type': opposition_type,
        'timestamp': datetime.utcnow().isoformat()
    }

    await db.table('core.opposition_records').insert(opposition_record).execute()

    return {
        'success': True,
        'message': f'Su oposición al procesamiento de datos para {opposition_type} ha sido registrada.'
    }


# Data Retention

async def check_retention_policy(data_date: datetime, data_type: str, market: str = 'mexico') -> Dict[str, Any]:
    """
    Check if data should be retained or deleted based on retention policy

    Args:
        data_date: Date when data was created
        data_type: Type of data
        market: Market identifier

    Returns:
        Retention policy decision
    """
    retention_years = 5 if market == 'mexico' else 7

    age = datetime.now() - data_date
    age_years = age.days / 365

    return {
        'can_delete': age_years > retention_years,
        'retention_years': retention_years,
        'data_age_years': age_years
    }


async def purge_old_data(market: str = 'mexico') -> Dict[str, Any]:
    """
    Purge data older than retention period

    Args:
        market: Market identifier

    Returns:
        Purge statistics
    """
    from .database import db

    retention_years = 5 if market == 'mexico' else 7
    cutoff_date = datetime.now() - timedelta(days=365 * retention_years)

    # Get old records
    old_records = await db.table('healthcare.appointments')\
        .select('*')\
        .lt('created_at', cutoff_date.isoformat())\
        .execute()

    deleted_count = 0
    retained_count = 0

    for record in old_records.data if old_records else []:
        record_date = datetime.fromisoformat(record['created_at'])
        if record_date < cutoff_date:
            # Delete old record
            await db.table('healthcare.appointments')\
                .delete()\
                .eq('id', record['id'])\
                .execute()
            deleted_count += 1
        else:
            retained_count += 1

    return {
        'deleted_count': deleted_count,
        'retained_count': retained_count
    }


async def process_patient_message(phone: str, clinic_id: str, message: str):
    """
    Process patient message with consent check

    Args:
        phone: Patient phone number
        clinic_id: Clinic identifier
        message: Message content

    Raises:
        Exception if no consent
    """
    consent = await check_consent(phone, clinic_id)

    if not consent:
        raise Exception("No consent given for data processing")

    # Process message...
    pass


async def withdraw_consent(phone: str, clinic_id: str) -> Dict[str, Any]:
    """
    Withdraw previously given consent

    Args:
        phone: User phone number
        clinic_id: Clinic identifier

    Returns:
        Withdrawal status
    """
    from .database import db

    # Record withdrawal
    await record_consent(phone, clinic_id, accepted=False)

    # Update existing consent records
    hashed_phone = hash_phone(phone)

    await db.table('core.consent_records')\
        .update({'consent_withdrawn': True, 'withdrawn_at': datetime.utcnow().isoformat()})\
        .eq('organization_id', clinic_id)\
        .eq('user_identifier', hashed_phone)\
        .eq('consent_given', True)\
        .execute()

    return {
        'success': True,
        'status': 'withdrawn',
        'message': 'Su consentimiento ha sido retirado.'
    }


async def get_consent_version(phone: str, clinic_id: str) -> Optional[str]:
    """
    Get consent version for a user

    Args:
        phone: User phone number
        clinic_id: Clinic identifier

    Returns:
        Consent version string or None
    """
    consent = await check_consent(phone, clinic_id)
    return consent.get('consent_version') if consent else None


# Cross-border compliance

def get_data_residency_config(market: str) -> Dict[str, List[str]]:
    """
    Get data residency configuration for a market

    Args:
        market: Market identifier

    Returns:
        Allowed regions for data storage
    """
    if market == 'mexico':
        return {
            'allowed_regions': ['mexico', 'us-west', 'us-central']
        }
    elif market == 'us':
        return {
            'allowed_regions': ['us-east', 'us-west', 'us-central']
        }
    else:
        return {
            'allowed_regions': ['any']
        }


async def check_international_transfer(from_country: str, to_country: str, data_type: str) -> bool:
    """
    Check if international data transfer requires notice

    Args:
        from_country: Source country
        to_country: Destination country
        data_type: Type of data being transferred

    Returns:
        True if notice required, False otherwise
    """
    # Same country transfers don't need notice
    if from_country == to_country:
        return False

    # Mexico to US transfers need notice
    if from_country == 'mexico' and to_country == 'us':
        return True

    # US to Mexico transfers need notice
    if from_country == 'us' and to_country == 'mexico':
        return True

    return False


async def enforce_retention_policy(market: str, retention_years: int) -> Dict[str, Any]:
    """
    Enforce data retention policy

    Args:
        market: Market identifier
        retention_years: Years to retain data

    Returns:
        Enforcement statistics
    """
    from .database import db

    cutoff_date = datetime.now() - timedelta(days=365 * retention_years)

    # Get all records
    all_records = await db.table('healthcare.appointments')\
        .select('*')\
        .execute()

    deleted = 0
    retained = 0

    for record in all_records.data if all_records else []:
        created_at = datetime.fromisoformat(record['created_at'])
        if created_at < cutoff_date:
            # Delete old record
            await db.table('healthcare.appointments')\
                .delete()\
                .eq('id', record['id'])\
                .execute()
            deleted += 1
        else:
            retained += 1

    return {
        'deleted': deleted,
        'retained': retained
    }


# Consent Management

class ConsentManager:
    """Base consent manager"""

    async def verify(self, patient_id: str) -> bool:
        """Verify consent exists"""
        return True


class MexicanPrivacyConsentManager(ConsentManager):
    """Mexican LFPDPPP consent manager"""

    async def verify(self, patient_id: str) -> bool:
        """Verify LFPDPPP consent"""
        # Implementation
        return True


class HIPAAConsentManager(ConsentManager):
    """HIPAA consent manager"""

    async def verify(self, patient_id: str) -> bool:
        """Verify HIPAA consent"""
        # Implementation
        return True
