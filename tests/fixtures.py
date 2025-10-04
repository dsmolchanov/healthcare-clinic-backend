"""
Test fixtures for dental clinic system
"""

import uuid
from datetime import datetime, timedelta

# Sample test data
TEST_CLINIC_ID = 'test-clinic-001'
TEST_PHONE = '+521234567890'
TEST_APPOINTMENT_ID = str(uuid.uuid4())

# Fixture functions
def create_test_clinic(**kwargs):
    """Create a test clinic"""
    return {
        'id': kwargs.get('id', TEST_CLINIC_ID),
        'name': kwargs.get('name', 'Test Dental Clinic'),
        'website': kwargs.get('website', 'https://testclinic.mx'),
        'timezone': kwargs.get('timezone', 'America/Mexico_City'),
        'language': kwargs.get('language', 'es'),
        'max_appointments_per_slot': kwargs.get('max_appointments_per_slot', 2),
        'business_hours': kwargs.get('business_hours', {
            'monday': {'open': '09:00', 'close': '18:00'},
            'tuesday': {'open': '09:00', 'close': '18:00'},
            'wednesday': {'open': '09:00', 'close': '18:00'},
            'thursday': {'open': '09:00', 'close': '18:00'},
            'friday': {'open': '09:00', 'close': '18:00'},
            'saturday': {'open': '09:00', 'close': '14:00'},
            'sunday': 'closed'
        }),
        'services': kwargs.get('services', [
            'cleaning', 'filling', 'extraction', 'root_canal', 'crown'
        ])
    }

def create_test_appointment(**kwargs):
    """Create a test appointment"""
    return {
        'id': kwargs.get('id', str(uuid.uuid4())),
        'clinic_id': kwargs.get('clinic_id', TEST_CLINIC_ID),
        'patient_phone': kwargs.get('patient_phone', TEST_PHONE),
        'appointment_date': kwargs.get('appointment_date',
                                      (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%d')),
        'start_time': kwargs.get('start_time', '14:00'),
        'end_time': kwargs.get('end_time', '15:00'),
        'status': kwargs.get('status', 'scheduled'),
        'service': kwargs.get('service', 'cleaning'),
        'created_via': kwargs.get('created_via', 'whatsapp'),
        'created_at': kwargs.get('created_at', datetime.utcnow().isoformat())
    }
