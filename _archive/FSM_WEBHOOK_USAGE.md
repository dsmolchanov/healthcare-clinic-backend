# FSM Webhook Integration - Usage Guide

## Overview

The FSM (Finite State Machine) webhook integration provides intelligent conversation management for WhatsApp appointment booking. Messages are processed through a stateful pipeline that maintains context across multiple turns.

## Feature Flag

The FSM integration is controlled by an environment variable for safe rollout:

```bash
# In .env file
ENABLE_FSM=false  # Default: disabled, uses legacy handler
ENABLE_FSM=true   # Enabled: uses FSM processing
```

## Webhook Endpoints

### 1. Twilio WhatsApp Webhook

**Endpoint:** `POST /webhooks/whatsapp`

**Expected Payload (Twilio format):**
```json
{
  "MessageSid": "SM1234567890abcdef1234567890abcdef",
  "From": "whatsapp:+15551234567",
  "To": "whatsapp:+14155238886",
  "Body": "Здравствуйте, хочу записаться к врачу"
}
```

**Response:**
```json
{
  "response": "Здравствуйте! Я помогу вам записаться. К какому врачу вы хотите записаться?",
  "state": "collecting_slots",
  "cached": false
}
```

### 2. Evolution API WhatsApp Webhook

**Endpoint:** `POST /webhooks/evolution`

**Expected Payload (Evolution format):**
```json
{
  "key": {
    "remoteJid": "5511999999999@s.whatsapp.net",
    "fromMe": false,
    "id": "3EB0F2F8E4F8F8F8F8F8"
  },
  "message": {
    "conversation": "Здравствуйте, хочу записаться"
  },
  "messageTimestamp": 1629900000
}
```

**Response:** Same format as Twilio endpoint

## FSM Processing Flow

### 8-Step Pipeline

1. **Idempotency Check**
   - Checks if message was already processed
   - Returns cached response if duplicate

2. **Load State**
   - Loads conversation state from Redis
   - Creates new state if first message

3. **Detect Intent**
   - Analyzes user message to determine intent
   - Uses IntentRouter with LLM-based detection

4. **Handle State**
   - Routes to appropriate handler based on current state:
     - GREETING
     - COLLECTING_SLOTS
     - AWAITING_CONFIRMATION
     - DISAMBIGUATING
     - AWAITING_CLARIFICATION
     - BOOKING
     - COMPLETED/FAILED

5. **Execute Booking**
   - When state transitions to BOOKING
   - Extracts slots and creates appointment
   - Transitions to COMPLETED or FAILED

6. **Save State**
   - Saves updated state to Redis
   - Uses CAS (Compare-And-Set) for concurrency safety

7. **Cache Response**
   - Caches response for idempotency
   - TTL: 1 hour

8. **Return Response**
   - Returns user-facing message
   - Includes current state

## Conversation States

### GREETING
- Initial state for new conversations
- Bot greets user and asks what they need

### COLLECTING_SLOTS
- Collecting required information:
  - Doctor/specialist
  - Appointment date
  - Appointment time
- Validates slots as they're provided

### AWAITING_CONFIRMATION
- All slots collected
- Asks user to confirm booking details

### DISAMBIGUATING
- Multiple options available (e.g., multiple doctors named "Иванов")
- Presents choices and waits for selection

### AWAITING_CLARIFICATION
- Unclear or invalid input
- Asks user to clarify or provide valid value

### BOOKING
- Confirmed - executing booking
- Creates appointment in database
- Syncs with calendar

### COMPLETED
- Booking successful
- Provides confirmation number

### FAILED
- Booking failed after retries
- Provides error message and escalation

## Configuration

### Environment Variables

```bash
# FSM Feature Flag
ENABLE_FSM=false  # true to enable FSM processing

# Default clinic for development
DEFAULT_CLINIC_ID=default_clinic

# Redis for state storage
REDIS_URL=redis://localhost:6379/0

# Supabase (required)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key
```

## Testing

### Run Test Suite

```bash
cd apps/healthcare-backend
python3 test_webhook_fsm.py
```

### Manual Testing with curl

**Test Twilio Webhook:**
```bash
curl -X POST http://localhost:8000/webhooks/whatsapp \
  -H "Content-Type: application/json" \
  -d '{
    "MessageSid": "SM123456",
    "From": "whatsapp:+15551234567",
    "Body": "Здравствуйте"
  }'
```

**Test Evolution Webhook:**
```bash
curl -X POST http://localhost:8000/webhooks/evolution \
  -H "Content-Type: application/json" \
  -d '{
    "key": {
      "id": "ABC123",
      "remoteJid": "5511999999999@s.whatsapp.net"
    },
    "message": {
      "conversation": "Здравствуйте"
    }
  }'
```

### Test with FSM Disabled

```bash
# In .env
ENABLE_FSM=false

# Will route to legacy handler
curl -X POST http://localhost:8000/webhooks/whatsapp \
  -H "Content-Type: application/json" \
  -d '{"MessageSid": "SM123", "From": "whatsapp:+15551234567", "Body": "test"}'

# Response:
# {
#   "response": "Спасибо за сообщение. Наш администратор скоро с вами свяжется.",
#   "legacy": true
# }
```

## Deployment

### Staging Deployment

1. Deploy with FSM disabled:
   ```bash
   ENABLE_FSM=false fly deploy
   ```

2. Verify legacy handler works

3. Enable FSM for testing:
   ```bash
   fly secrets set ENABLE_FSM=true
   ```

4. Test with real WhatsApp messages

### Production Rollout

1. Start with FSM disabled
2. Enable for test users/numbers
3. Monitor metrics and error rates
4. Gradually increase rollout percentage
5. Full rollout when stable

### Rollback

If issues occur:
```bash
# Instant rollback - no code changes needed
fly secrets set ENABLE_FSM=false
```

## Monitoring

### Logs to Watch

```bash
# FSM initialization
"FSM feature flag: ENABLED"

# Message processing
"Processing message SM123 for conversation whatsapp:+15551234567"
"Loaded state: collecting_slots, version: 3"
"Detected intent: provide_slot"
"Executing booking..."
"Booking successful: APT_12345"
"State saved successfully: completed, version: 5"
```

### Key Metrics

- FSM processing time (Step 1 → Step 8)
- State transition counts
- Booking success rate
- Idempotency cache hit rate
- CAS conflict rate

## Error Handling

### Common Errors

1. **"Произошла ошибка. Пожалуйста, повторите запрос."**
   - CAS conflict (state modified by another request)
   - User should retry - state is safe

2. **"Произошла ошибка. Пожалуйста, попробуйте позже."**
   - Unexpected exception in FSM pipeline
   - Check logs for details

3. **"❌ Ошибка бронирования: [error]"**
   - Booking execution failed
   - State transitions to FAILED
   - User can start new conversation

## Architecture

### Components

- **FSMManager:** State loading, saving, transitions
- **IntentRouter:** LLM-based intent detection
- **SlotManager:** Slot validation and DB lookups
- **StateHandler:** Business logic for each state
- **RedisClient:** State persistence

### Data Flow

```
WhatsApp → Webhook → process_with_fsm() → FSMManager
                                        → IntentRouter
                                        → StateHandler
                                        → SlotManager
                                        → BookingService
                                        → Redis (state)
                                        → Response
```

## FAQ

### Q: What happens if Redis is down?

A: New conversations will fail (can't load state). Existing conversations will fail on save (CAS error). Users see error message and can retry when Redis is back.

### Q: Can I use FSM without Redis?

A: No, Redis is required for state persistence. FSM uses CAS operations for concurrency safety.

### Q: How long does state persist?

A: State persists for FSM_STATE_TTL (default: 24 hours). After that, conversation starts fresh.

### Q: What if user sends same message twice?

A: Idempotency check catches duplicates within 1 hour. Same response returned without reprocessing.

### Q: Can I test FSM locally?

A: Yes! Run Redis locally (`redis-server`) and set `ENABLE_FSM=true` in `.env`. Use test script or curl.

## Support

For issues or questions:
1. Check logs for detailed error messages
2. Review state in Redis: `redis-cli GET fsm:state:{conversation_id}`
3. Run test suite to verify components
4. Toggle feature flag to isolate FSM vs legacy issues
