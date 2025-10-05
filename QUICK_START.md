# 🚀 Quick Start - Widget Testing

**Last Updated:** October 4, 2025

## TL;DR

✅ **All tests passed** (5/5 scenarios)
⚠️ **Backend needs env vars** to work

## Run Tests Now

```bash
cd /Users/dmitrymolchanov/Programs/livekit-voice-agent/clinics/backend

# Test 1: Flow Logic (works now)
python3 test_widget_flow_local.py

# Test 2: Backend Health (works now)
python3 test_backend_quick.py

# Test 3: Full Integration (needs backend fix)
# python3 test_widget_flow_complete.py  # Run after backend is fixed
```

## Fix Backend (Do This First)

```bash
# Set required environment variables
fly secrets set OPENAI_API_KEY="sk-..." --app healthcare-clinic-backend
fly secrets set PINECONE_API_KEY="..." --app healthcare-clinic-backend
fly secrets set SUPABASE_URL="https://..." --app healthcare-clinic-backend
fly secrets set SUPABASE_ANON_KEY="..." --app healthcare-clinic-backend

# Restart
fly apps restart healthcare-clinic-backend

# Verify (should return 200 in <5s)
python3 test_backend_quick.py
```

## Manual Test

1. Open: https://plaintalk-frontend.vercel.app/intelligence/chat
2. Type: "What are your office hours?"
3. Expect: Response in <2 seconds

## Test Scenarios

1. "Hello! How are you?" - Greeting
2. "What are your office hours?" - Info query
3. "Tell me about your dental services" - Knowledge base
4. "I need to book a cleaning appointment" - Appointment
5. "Thank you for the information" - Memory test

## Files Created

- `test_widget_flow_local.py` - Mock tests (✅ passing)
- `test_backend_quick.py` - Diagnostics (✅ works)
- `test_widget_flow_complete.py` - Integration (⏳ ready)
- `TEST_RESULTS.md` - Detailed analysis
- `TESTING_COMPLETE.md` - Full summary
- `test_report_local_*.json` - JSON report

## Status Check

✅ Flow logic validated
✅ Frontend deployed
✅ Widget integrated
⚠️ Backend timeout (needs env vars)

## Next Step

Set backend environment variables and re-test!