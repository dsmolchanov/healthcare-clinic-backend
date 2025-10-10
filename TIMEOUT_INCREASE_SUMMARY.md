# WhatsApp Routing Timeout Increase

## Change Summary

**File**: `clinics/backend/app/apps/voice-api/evolution_webhook.py`

**Change**: Increased AI routing timeout from 15 to 30 seconds

### Before:
```python
timeout=15.0  # 15 second max for AI processing
# ...
print(f"[Background] ⏰ Routing timed out after 15s - using fallback response")
latency_ms = 15000
```

### After:
```python
timeout=30.0  # 30 second max for AI processing
# ...
print(f"[Background] ⏰ Routing timed out after 30s - using fallback response")
latency_ms = 30000
```

## Why This Change

The AI orchestrator (LangGraph) was consistently timing out after 15 seconds when processing complex requests, causing users to receive fallback responses like:

> "Thank you for your message. We're processing your request and will respond shortly...."

## Impact

✅ **Positive**:
- More time for AI to process complex requests
- Fewer timeout fallbacks
- Better user experience with actual AI responses

⚠️ **Trade-off**:
- Slightly longer wait times if AI still times out
- Users wait up to 30s instead of 15s before receiving fallback

## Testing

The change is now live. Monitor logs for:
- Reduced frequency of timeout messages
- More successful routing completions
- Actual AI responses instead of fallbacks

## Monitoring

Watch for these log patterns:
```
# Success (good)
[Background] ✅ Message routed successfully

# Timeout (still happening but should be less frequent)
[Background] ⏰ Routing timed out after 30s - using fallback response
```

## Deployment

✅ **Deployed**: 2025-09-30 23:35 UTC  
✅ **Status**: Live on all machines  
✅ **App**: https://healthcare-clinic-backend.fly.dev/
