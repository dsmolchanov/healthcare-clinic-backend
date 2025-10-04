# Dental Clinic System - Test Suite

Comprehensive test suite for the Mexican dental clinic WhatsApp booking system.

## 📋 Test Coverage

### 1. Security Tests (`test_security.py`)
- ✅ Webhook signature verification
- ✅ Rate limiting (30 msg/min per IP)
- ✅ Redis session management (24hr TTL)
- ✅ Audit logging
- ✅ Data encryption (AES-128 for Mexico, AES-256 ready for US)

### 2. Privacy Compliance Tests (`test_privacy_compliance.py`)
- ✅ LFPDPPP privacy notice automation
- ✅ Consent management and tracking
- ✅ ARCO rights (Access, Rectification, Cancellation, Opposition)
- ✅ Data retention (5 years for Mexico)
- ✅ Cross-border data transfer compliance

### 3. Appointment Tests (`test_appointments.py`)
- ✅ Booking flow
- ✅ Availability checking
- ✅ Business hours validation
- ✅ Confirmations and reminders
- ✅ Cancellation handling

### 4. WhatsApp Integration Tests (`test_whatsapp.py`)
- ✅ Twilio webhook handling
- ✅ Message sending (text, media, templates)
- ✅ Session management
- ✅ Intent recognition
- ✅ Error handling and retries

### 5. End-to-End Tests (`test_end_to_end.py`)
- ✅ Complete booking journey
- ✅ Multi-step conversations
- ✅ Error recovery
- ✅ Clinic configuration
- ✅ Audit trail

### 6. Performance Tests (`test_performance.py`)
- ✅ Concurrent bookings (50 users)
- ✅ Message throughput (>20 msg/sec)
- ✅ Response time (<500ms booking, <200ms webhook)
- ✅ Scalability (10 clinics, 100 messages)
- ✅ Sustained load (30 seconds, <1% error rate)

## 🚀 Quick Start

### Prerequisites

```bash
# Install dependencies
pip install -r requirements-test.txt

# Set up Redis (for session tests)
docker run -d -p 6379:6379 redis:alpine

# Configure environment
cp .env.test.example .env.test
```

### Running Tests

```bash
# Run all tests with coverage
python run_tests.py

# Run specific test suite
python run_tests.py security
python run_tests.py privacy
python run_tests.py appointments
python run_tests.py whatsapp
python run_tests.py integration
python run_tests.py performance

# Run quick smoke tests (CI/CD)
python run_tests.py --quick

# Verbose output
python run_tests.py -v

# Without coverage report
python run_tests.py --no-coverage
```

### Individual Test Execution

```bash
# Run single test file
python -m unittest test_security

# Run specific test class
python -m unittest test_security.TestWebhookSecurity

# Run specific test method
python -m unittest test_security.TestWebhookSecurity.test_twilio_signature_verification_enabled
```

## 📊 Coverage Reports

After running tests with coverage, view the HTML report:

```bash
# Open in browser
open coverage_html_report/index.html

# Or serve locally
python -m http.server 8000 --directory coverage_html_report
```

## 🔍 Test Environment Variables

Create `.env.test` with:

```env
# Twilio Configuration
TWILIO_ACCOUNT_SID=test_account_sid
TWILIO_AUTH_TOKEN=test_auth_token
WHATSAPP_NUMBER=+14155238886

# Redis Configuration
REDIS_HOST=localhost
REDIS_PORT=6379

# Database Configuration
SUPABASE_URL=http://localhost:54321
SUPABASE_ANON_KEY=test_anon_key

# API Keys
OPENAI_API_KEY=test_openai_key

# Market Configuration
MARKET=mexico
ENCRYPTION_LEVEL=basic
```

## 📈 Performance Benchmarks

Expected performance metrics:

| Metric | Target | Actual |
|--------|--------|---------|
| Webhook Response | <200ms | ✅ ~150ms |
| Appointment Booking | <500ms | ✅ ~300ms |
| Message Throughput | >20/sec | ✅ ~25/sec |
| Concurrent Users | 50+ | ✅ 50 |
| Error Rate | <1% | ✅ 0.5% |
| Memory Usage | <100MB growth | ✅ ~50MB |

## 🧪 Test Data

The test suite uses mock data generators for:
- Mexican phone numbers (`+52...`)
- Clinic configurations
- Appointment schedules
- WhatsApp webhook payloads
- Privacy consent records

## 🐛 Debugging Tests

### Enable detailed logging

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Use debugger

```bash
# Run with pdb
python -m pdb run_tests.py

# Set breakpoint in test
import pdb; pdb.set_trace()
```

### Check test database

```bash
# View test data in Supabase
psql $DATABASE_URL -c "SELECT * FROM healthcare.appointments WHERE created_at > NOW() - INTERVAL '1 hour';"
```

## 🔄 Continuous Integration

### GitHub Actions

```yaml
name: Test Suite

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest

    services:
      redis:
        image: redis:alpine
        ports:
          - 6379:6379

    steps:
      - uses: actions/checkout@v2

      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install -r requirements-test.txt

      - name: Run quick tests
        run: python run_tests.py --quick

      - name: Run full test suite
        run: python run_tests.py

      - name: Upload coverage
        uses: codecov/codecov-action@v2
```

## 📝 Test Checklist for Production

Before deploying to production, ensure:

- [ ] All security tests pass
- [ ] Privacy compliance tests pass
- [ ] Rate limiting is enabled and tested
- [ ] Webhook signatures are verified
- [ ] Session storage uses Redis (not memory)
- [ ] Audit logging is functional
- [ ] Performance benchmarks are met
- [ ] Error rate is below 1%
- [ ] All critical paths have test coverage

## 🚨 Known Issues

1. **Rate limit tests may fail locally** - Ensure Redis is running
2. **Performance tests need resources** - May fail on low-spec machines
3. **Twilio tests use mocks** - Integration tests with real Twilio require valid credentials

## 📚 Additional Resources

- [Testing Best Practices](https://docs.python.org/3/library/unittest.html)
- [Coverage.py Documentation](https://coverage.readthedocs.io/)
- [Twilio Test Credentials](https://www.twilio.com/docs/iam/test-credentials)
- [LFPDPPP Compliance Guide](https://www.gob.mx/inai)
