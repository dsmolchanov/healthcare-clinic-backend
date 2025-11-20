from unittest.mock import MagicMock, AsyncMock
import uuid
from datetime import datetime

class MockSupabaseClient:
    def __init__(self):
        self.data = {}
        self.table = MagicMock()
        self.table.return_value.select.return_value.eq.return_value.execute = AsyncMock()
        self.table.return_value.insert.return_value.execute = AsyncMock()
        self.table.return_value.update.return_value.eq.return_value.execute = AsyncMock()
        self.table.return_value.delete.return_value.eq.return_value.execute = AsyncMock()
        
        # Setup default return values for common tables
        self.setup_default_responses()

    def setup_default_responses(self):
        # Default empty responses
        self.table.return_value.select.return_value.execute.return_value = {'data': []}
        self.table.return_value.select.return_value.eq.return_value.execute.return_value = {'data': []}

def create_test_clinic(clinic_id=None):
    return {
        'id': clinic_id or str(uuid.uuid4()),
        'name': 'Test Dental Clinic',
        'address': '123 Test St',
        'phone': '555-0123',
        'services': ['cleaning', 'extraction', 'whitening'],
        'hours': {
            'weekdays': '9:00 - 18:00',
            'saturday': '9:00 - 14:00',
            'sunday': 'Closed'
        }
    }

def create_test_patient(patient_id=None, phone='555-0000'):
    return {
        'id': patient_id or str(uuid.uuid4()),
        'first_name': 'Test',
        'last_name': 'Patient',
        'phone': phone
    }
