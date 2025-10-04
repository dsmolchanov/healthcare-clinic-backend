"""
Base test utilities and fixtures for dental clinic testing
"""

import asyncio
import os
import json
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import unittest
from unittest.mock import Mock, AsyncMock, patch
import redis
from fastapi.testclient import TestClient
import httpx


class BaseTestCase(unittest.TestCase):
    """Base test case with common utilities"""

    @classmethod
    def setUpClass(cls):
        """Set up test environment variables"""
        cls.test_env = {
            'TWILIO_ACCOUNT_SID': 'test_account_sid',
            'TWILIO_AUTH_TOKEN': 'test_auth_token',
            'WHATSAPP_NUMBER': '+14155238886',
            'REDIS_HOST': 'localhost',
            'REDIS_PORT': '6379',
            'SUPABASE_URL': 'http://localhost:54321',
            'SUPABASE_ANON_KEY': 'test_anon_key',
            'OPENAI_API_KEY': 'test_openai_key',
            'MARKET': 'mexico',
            'ENCRYPTION_LEVEL': 'basic'
        }

        for key, value in cls.test_env.items():
            os.environ[key] = value

    def setUp(self):
        """Set up each test"""
        self.test_clinic_id = str(uuid.uuid4())
        self.test_phone = '+521234567890'  # Mexican phone format
        self.test_session_id = str(uuid.uuid4())

        # Mock Redis client
        self.redis_mock = Mock(spec=redis.Redis)

        # Mock database
        self.db_mock = Mock()

    def tearDown(self):
        """Clean up after each test"""
        pass

    def create_test_clinic(self, **kwargs) -> Dict[str, Any]:
        """Create a test clinic configuration"""
        return {
            'id': self.test_clinic_id,
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
            ]),
            'created_at': datetime.utcnow().isoformat()
        }

    def create_test_appointment(self, **kwargs) -> Dict[str, Any]:
        """Create a test appointment"""
        return {
            'id': str(uuid.uuid4()),
            'clinic_id': kwargs.get('clinic_id', self.test_clinic_id),
            'patient_phone': kwargs.get('patient_phone', self.test_phone),
            'appointment_date': kwargs.get('appointment_date',
                                          (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%d')),
            'start_time': kwargs.get('start_time', '14:00'),
            'end_time': kwargs.get('end_time', '15:00'),
            'status': kwargs.get('status', 'scheduled'),
            'service': kwargs.get('service', 'cleaning'),
            'created_via': kwargs.get('created_via', 'whatsapp'),
            'created_at': datetime.utcnow().isoformat()
        }

    def create_whatsapp_webhook_payload(self, **kwargs) -> Dict[str, Any]:
        """Create a test WhatsApp webhook payload"""
        return {
            'MessageSid': kwargs.get('message_sid', f'SM{uuid.uuid4().hex}'),
            'From': kwargs.get('from', f'whatsapp:{self.test_phone}'),
            'To': kwargs.get('to', 'whatsapp:+14155238886'),
            'Body': kwargs.get('body', 'Hola, quiero agendar una cita'),
            'AccountSid': self.test_env['TWILIO_ACCOUNT_SID'],
            'NumMedia': kwargs.get('num_media', '0'),
            'ProfileName': kwargs.get('profile_name', 'Test User')
        }


class AsyncTestCase(BaseTestCase):
    """Base test case for async tests"""

    def setUp(self):
        super().setUp()
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()
        super().tearDown()

    def run_async(self, coro):
        """Helper to run async functions in tests"""
        return self.loop.run_until_complete(coro)

    async def async_setup(self):
        """Async setup that can be called from tests"""
        pass

    async def async_teardown(self):
        """Async teardown that can be called from tests"""
        pass


class MockSupabaseClient:
    """Mock Supabase client for testing"""

    def __init__(self):
        self.data = {}

    def table(self, table_name: str):
        return MockTable(table_name, self.data)


class MockTable:
    """Mock Supabase table for testing"""

    def __init__(self, table_name: str, data_store: dict):
        self.table_name = table_name
        self.data_store = data_store
        if table_name not in self.data_store:
            self.data_store[table_name] = []
        self._filters = []

    def select(self, *columns):
        self._columns = columns
        return self

    def eq(self, column: str, value: Any):
        self._filters.append(('eq', column, value))
        return self

    def insert(self, data: Dict[str, Any]):
        if 'id' not in data:
            data['id'] = str(uuid.uuid4())
        self.data_store[self.table_name].append(data)
        return self

    async def execute(self):
        if hasattr(self, '_filters'):
            # Apply filters
            result = self.data_store[self.table_name]
            for filter_type, column, value in self._filters:
                if filter_type == 'eq':
                    result = [r for r in result if r.get(column) == value]
            return {'data': result, 'count': len(result)}
        return {'data': self.data_store[self.table_name]}


class MockTwilioClient:
    """Mock Twilio client for testing"""

    def __init__(self):
        self.messages = MockMessages()


class MockMessages:
    """Mock Twilio messages resource"""

    def __init__(self):
        self.sent_messages = []

    def create(self, body: str, from_: str, to: str, **kwargs):
        message = {
            'sid': f'SM{uuid.uuid4().hex}',
            'body': body,
            'from': from_,
            'to': to,
            'status': 'sent',
            'timestamp': datetime.utcnow().isoformat()
        }
        message.update(kwargs)
        self.sent_messages.append(message)
        return Mock(sid=message['sid'])
