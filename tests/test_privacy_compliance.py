"""
Mexican privacy law (LFPDPPP) compliance tests
Tests consent management, privacy notices, data rights
"""

from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, AsyncMock
from .test_base import AsyncTestCase, MockSupabaseClient, MockTwilioClient


class TestPrivacyNotice(AsyncTestCase):
    """Test LFPDPPP privacy notice implementation"""

    @patch('clinics.backend.app.privacy.send_whatsapp_message')
    async def test_privacy_notice_sent_on_first_contact(self, mock_send):
        """Test that privacy notice is sent on first contact"""
        from clinics.backend.app.privacy import handle_first_contact

        mock_send.return_value = AsyncMock()

        # Mock no existing consent
        with patch('clinics.backend.app.privacy.check_consent') as mock_check:
            mock_check.return_value = None

            # First contact should trigger privacy notice
            result = await handle_first_contact(
                phone=self.test_phone,
                clinic_id=self.test_clinic_id
            )

            # Verify privacy notice was sent
            mock_send.assert_called_once()
            sent_message = mock_send.call_args[0][1]

            # Check privacy notice content
            self.assertIn('Aviso de Privacidad', sent_message)
            self.assertIn('LFPDPPP', sent_message)
            self.assertIn('ACEPTO', sent_message)
            self.assertIn('RECHAZAR', sent_message)

    def test_privacy_notice_contains_required_elements(self):
        """Test that privacy notice contains all LFPDPPP required elements"""
        from clinics.backend.app.privacy import generate_privacy_notice

        clinic_info = self.create_test_clinic()
        notice = generate_privacy_notice(clinic_info)

        # Required elements per LFPDPPP
        required_elements = [
            clinic_info['name'],  # Identity of data controller
            'datos personales',   # Type of data collected
            'agendar',           # Purpose of data collection
            'confirmar citas',   # Specific uses
            'privacidad',        # Privacy policy reference
            'ACEPTO',           # Consent mechanism
            'RECHAZAR'          # Opt-out mechanism
        ]

        for element in required_elements:
            self.assertIn(
                element, notice,
                f"Privacy notice missing required element: {element}"
            )

    @patch('clinics.backend.app.database.supabase')
    async def test_consent_recorded_with_timestamp(self, mock_supabase):
        """Test that consent is properly recorded with timestamp"""
        from clinics.backend.app.privacy import record_consent

        mock_table = MagicMock()
        mock_supabase.table.return_value = mock_table
        mock_table.insert.return_value.execute = AsyncMock()

        # Record consent
        await record_consent(
            phone=self.test_phone,
            clinic_id=self.test_clinic_id,
            accepted=True
        )

        # Verify consent was recorded
        mock_supabase.table.assert_called_with('core.consent_records')
        insert_data = mock_table.insert.call_args[0][0]

        self.assertEqual(insert_data['organization_id'], self.test_clinic_id)
        self.assertEqual(insert_data['consent_type'], 'lfpdppp_data_processing')
        self.assertTrue(insert_data['consent_given'])
        self.assertIn('timestamp', insert_data)
        self.assertIn('consent_text', insert_data)

    async def test_consent_rejection_handled(self):
        """Test that consent rejection is properly handled"""
        from clinics.backend.app.privacy import handle_consent_response

        with patch('clinics.backend.app.privacy.record_consent') as mock_record:
            mock_record.return_value = AsyncMock()

            response = await handle_consent_response(
                phone=self.test_phone,
                clinic_id=self.test_clinic_id,
                message='RECHAZAR'
            )

            # Verify rejection was recorded
            mock_record.assert_called_with(
                phone=self.test_phone,
                clinic_id=self.test_clinic_id,
                accepted=False
            )

            self.assertFalse(response['consent_given'])
            self.assertIn('rejected', response['status'])


class TestDataRights(AsyncTestCase):
    """Test LFPDPPP data subject rights (ARCO)"""

    @patch('clinics.backend.app.database.supabase')
    async def test_right_to_access(self, mock_supabase):
        """Test right to access personal data (A in ARCO)"""
        from clinics.backend.app.privacy import handle_data_access_request

        # Mock stored data
        mock_data = {
            'appointments': [self.create_test_appointment()],
            'messages': ['Message 1', 'Message 2'],
            'consent_records': [{'timestamp': '2024-01-01', 'accepted': True}]
        }

        mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(
            return_value={'data': mock_data}
        )

        # Request data access
        result = await handle_data_access_request(
            phone=self.test_phone,
            clinic_id=self.test_clinic_id
        )

        # Should return all personal data
        self.assertIn('appointments', result)
        self.assertIn('messages', result)
        self.assertIn('consent_records', result)

    @patch('clinics.backend.app.database.supabase')
    async def test_right_to_rectification(self, mock_supabase):
        """Test right to correct personal data (R in ARCO)"""
        from clinics.backend.app.privacy import handle_data_rectification_request

        mock_supabase.table.return_value.update.return_value.eq.return_value.execute = AsyncMock()

        # Request data correction
        corrections = {
            'phone': '+529876543210',
            'name': 'Juan Carlos Pérez'
        }

        result = await handle_data_rectification_request(
            phone=self.test_phone,
            clinic_id=self.test_clinic_id,
            corrections=corrections
        )

        # Verify update was called
        mock_supabase.table.assert_called()
        self.assertTrue(result['success'])
        self.assertIn('updated', result['message'])

    @patch('clinics.backend.app.database.supabase')
    async def test_right_to_cancellation(self, mock_supabase):
        """Test right to cancel/delete data (C in ARCO)"""
        from clinics.backend.app.privacy import handle_data_deletion_request

        mock_supabase.table.return_value.delete.return_value.eq.return_value.execute = AsyncMock()

        # Request data deletion
        result = await handle_data_deletion_request(
            phone=self.test_phone,
            clinic_id=self.test_clinic_id
        )

        # Verify deletion was performed
        self.assertTrue(result['success'])
        self.assertIn('deleted', result['message'])

        # Verify audit log was created for deletion
        audit_calls = [call for call in mock_supabase.table.call_args_list
                      if 'audit_logs' in str(call)]
        self.assertGreater(len(audit_calls), 0, "Deletion should be audited")

    @patch('clinics.backend.app.database.supabase')
    async def test_right_to_opposition(self, mock_supabase):
        """Test right to oppose data processing (O in ARCO)"""
        from clinics.backend.app.privacy import handle_opposition_request

        mock_supabase.table.return_value.insert.return_value.execute = AsyncMock()

        # Request opposition to marketing
        result = await handle_opposition_request(
            phone=self.test_phone,
            clinic_id=self.test_clinic_id,
            opposition_type='marketing'
        )

        # Verify opposition was recorded
        self.assertTrue(result['success'])
        self.assertIn('opposition_records', str(mock_supabase.table.call_args_list))


class TestDataRetention(AsyncTestCase):
    """Test data retention policies per LFPDPPP"""

    @patch('clinics.backend.app.database.supabase')
    async def test_data_retention_period(self, mock_supabase):
        """Test that data is retained for legal minimum (5 years)"""
        from clinics.backend.app.privacy import check_retention_policy

        # Create old data (6 years old)
        old_date = datetime.now() - timedelta(days=365 * 6)

        result = await check_retention_policy(
            data_date=old_date,
            data_type='appointment',
            market='mexico'
        )

        # Should be marked for deletion (past 5 years)
        self.assertTrue(result['can_delete'])
        self.assertEqual(result['retention_years'], 5)

    @patch('clinics.backend.app.database.supabase')
    async def test_automatic_data_purge(self, mock_supabase):
        """Test automatic purging of old data"""
        from clinics.backend.app.privacy import purge_old_data

        # Mock old records
        old_records = [
            {'id': '1', 'created_at': '2018-01-01'},  # 6+ years old
            {'id': '2', 'created_at': '2019-01-01'},  # 5+ years old
            {'id': '3', 'created_at': '2023-01-01'},  # Recent
        ]

        mock_supabase.table.return_value.select.return_value.execute = AsyncMock(
            return_value={'data': old_records}
        )
        mock_supabase.table.return_value.delete.return_value.eq.return_value.execute = AsyncMock()

        # Run purge
        result = await purge_old_data(market='mexico')

        # Should delete records older than 5 years
        self.assertEqual(result['deleted_count'], 2)
        self.assertEqual(result['retained_count'], 1)


class TestConsentManagement(AsyncTestCase):
    """Test consent management system"""

    @patch('clinics.backend.app.database.supabase')
    async def test_no_processing_without_consent(self, mock_supabase):
        """Test that data processing requires valid consent"""
        from clinics.backend.app.privacy import process_patient_message

        # Mock no consent
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(
            return_value={'data': []}
        )

        # Try to process without consent
        with self.assertRaises(Exception) as context:
            await process_patient_message(
                phone=self.test_phone,
                clinic_id=self.test_clinic_id,
                message='Quiero una cita'
            )

        self.assertIn('consent', str(context.exception).lower())

    @patch('clinics.backend.app.database.supabase')
    async def test_consent_withdrawal(self, mock_supabase):
        """Test that consent can be withdrawn"""
        from clinics.backend.app.privacy import withdraw_consent

        mock_supabase.table.return_value.update.return_value.eq.return_value.execute = AsyncMock()

        result = await withdraw_consent(
            phone=self.test_phone,
            clinic_id=self.test_clinic_id
        )

        self.assertTrue(result['success'])
        self.assertIn('withdrawn', result['status'])

        # Verify new consent record was created
        insert_calls = [call for call in mock_supabase.table.call_args_list
                       if 'insert' in str(call)]
        self.assertGreater(len(insert_calls), 0)

    @patch('clinics.backend.app.database.supabase')
    async def test_consent_version_tracking(self, mock_supabase):
        """Test that different versions of privacy notices are tracked"""
        from clinics.backend.app.privacy import get_consent_version

        # Mock consent with version
        mock_consent = {
            'consent_version': '2.0',
            'consent_date': '2024-01-01',
            'consent_text': 'Updated privacy notice...'
        }

        mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(
            return_value={'data': [mock_consent]}
        )

        version = await get_consent_version(
            phone=self.test_phone,
            clinic_id=self.test_clinic_id
        )

        self.assertEqual(version, '2.0')


class TestCrossBorderCompliance(AsyncTestCase):
    """Test cross-border data transfer compliance"""

    async def test_data_localization(self):
        """Test that Mexican data stays in compliant regions"""
        from clinics.backend.app.privacy import get_data_residency_config

        config = get_data_residency_config(market='mexico')

        # Mexican data should stay in allowed regions
        self.assertIn('mexico', config['allowed_regions'])
        self.assertIn('us-west', config['allowed_regions'])  # Close region ok
        self.assertNotIn('eu-central', config['allowed_regions'])  # EU not needed

    @patch('clinics.backend.app.database.supabase')
    async def test_international_transfer_notice(self, mock_supabase):
        """Test notification for international data transfers"""
        from clinics.backend.app.privacy import check_international_transfer

        # Check if transfer to US requires notice
        requires_notice = await check_international_transfer(
            from_country='mexico',
            to_country='us',
            data_type='appointment'
        )

        # Should require notice for US transfer
        self.assertTrue(requires_notice)

        # Check domestic transfer
        requires_notice = await check_international_transfer(
            from_country='mexico',
            to_country='mexico',
            data_type='appointment'
        )

        # Should not require notice for domestic
        self.assertFalse(requires_notice)
