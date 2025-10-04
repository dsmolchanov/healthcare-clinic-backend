#!/bin/bash

# Run tests for dental clinic system

echo "=========================================="
echo "Running Dental Clinic System Tests"
echo "=========================================="

# Set up environment
export PYTHONPATH=/Users/dmitrymolchanov/Programs/livekit-voice-agent/clinics/backend:$PYTHONPATH
export TWILIO_ACCOUNT_SID=test_account_sid
export TWILIO_AUTH_TOKEN=test_auth_token
export WHATSAPP_NUMBER=+14155238886
export REDIS_HOST=localhost
export REDIS_PORT=6379
export SUPABASE_URL=http://localhost:54321
export SUPABASE_ANON_KEY=test_anon_key
export OPENAI_API_KEY=test_openai_key
export MARKET=mexico
export ENCRYPTION_LEVEL=basic
export HASH_SALT=test_salt

# Change to test directory
cd /Users/dmitrymolchanov/Programs/livekit-voice-agent/clinics/backend/tests

# Run tests with Python
echo ""
echo "Running tests..."
python3 -m pytest -xvs test_security.py::TestWebhookSecurity::test_twilio_signature_verification_enabled 2>&1 | head -50
echo ""
echo "Running more tests..."
python3 -m pytest -xvs test_privacy_compliance.py::TestPrivacyNotice::test_privacy_notice_sent_on_first_contact 2>&1 | head -50
echo ""
echo "Running appointment tests..."
python3 -m pytest -xvs test_appointments.py::TestAppointmentBooking::test_successful_appointment_booking 2>&1 | head -50
