# WhatsApp Appointment Scheduling - Configuration Guide

This document describes the environment variables needed for the WhatsApp appointment scheduling system with outbox processing and escalation workers.

## Required Environment Variables

### Outbox Processor Worker

```bash
# Poll interval for checking outbox table (in seconds)
# Default: 0.5 (500ms)
OUTBOX_POLL_INTERVAL=0.5

# Batch size for fetching messages per poll
# Default: 10
OUTBOX_BATCH_SIZE=10

# Maximum retry attempts before marking message as permanently failed
# Default: 5
OUTBOX_MAX_RETRIES=5
```

### Failed Confirmation Escalation Worker

```bash
# Interval between escalation checks (in minutes)
# Default: 10
ESCALATION_CHECK_INTERVAL=10

# Time threshold for considering message as "stuck" (in minutes)
# Messages failed for longer than this will trigger escalation
# Default: 60
ESCALATION_FAILURE_THRESHOLD=60

# Minimum number of retries before escalation
# Only messages that have been retried this many times will be escalated
# Default: 3
ESCALATION_MIN_RETRIES=3

# Slack webhook URL for ops team alerts (REQUIRED for escalation alerts)
# Get this from Slack > Apps > Incoming Webhooks
SLACK_OPS_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
```

## Setting Up in Fly.io

To configure these variables in your Fly.io deployment:

```bash
# Navigate to the backend directory
cd apps/healthcare-backend

# Set the Slack webhook URL (REQUIRED)
fly secrets set SLACK_OPS_WEBHOOK_URL="https://hooks.slack.com/services/YOUR/WEBHOOK/URL" -a healthcare-clinic-backend

# Optional: Tune worker performance
fly secrets set OUTBOX_POLL_INTERVAL="0.5" -a healthcare-clinic-backend
fly secrets set OUTBOX_BATCH_SIZE="10" -a healthcare-clinic-backend
fly secrets set OUTBOX_MAX_RETRIES="5" -a healthcare-clinic-backend

# Optional: Tune escalation thresholds
fly secrets set ESCALATION_CHECK_INTERVAL="10" -a healthcare-clinic-backend
fly secrets set ESCALATION_FAILURE_THRESHOLD="60" -a healthcare-clinic-backend
fly secrets set ESCALATION_MIN_RETRIES="3" -a healthcare-clinic-backend
```

## Creating a Slack Incoming Webhook

1. Go to your Slack workspace
2. Navigate to **Apps** > **Manage Apps**
3. Search for "Incoming Webhooks" and add it to your workspace
4. Choose the channel where alerts should be posted (e.g., `#ops-alerts`)
5. Copy the generated webhook URL
6. Set it as the `SLACK_OPS_WEBHOOK_URL` environment variable

## Local Development

For local development, create a `.env` file in `/apps/healthcare-backend/` with:

```bash
# Outbox Processor Settings
OUTBOX_POLL_INTERVAL=0.5
OUTBOX_BATCH_SIZE=10
OUTBOX_MAX_RETRIES=5

# Escalation Settings
ESCALATION_CHECK_INTERVAL=10
ESCALATION_FAILURE_THRESHOLD=60
ESCALATION_MIN_RETRIES=3
SLACK_OPS_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
```

## Monitoring

### Check Worker Status

The workers log their status on startup:

```bash
# Check logs for worker initialization
fly logs -a healthcare-clinic-backend | grep -E "OutboxProcessor|FailedConfirmationEscalation"

# Expected output:
# ✅ Outbox processor worker started
# ✅ Failed confirmation escalation worker started
```

### View Outbox Statistics

You can monitor the outbox table directly:

```sql
-- Check outbox message counts by status
SELECT delivery_status, COUNT(*)
FROM healthcare.outbound_messages
GROUP BY delivery_status;

-- View recent failed messages
SELECT id, to_number, message_text, retry_count, failed_at
FROM healthcare.outbound_messages
WHERE delivery_status = 'failed'
ORDER BY failed_at DESC
LIMIT 10;
```

## Performance Tuning

### High Message Volume

If you're processing many messages per second:

```bash
# Increase batch size and poll interval
OUTBOX_POLL_INTERVAL=0.2  # Poll every 200ms
OUTBOX_BATCH_SIZE=50      # Process 50 messages per batch
```

### Low Message Volume

If messages are infrequent:

```bash
# Reduce polling frequency to save resources
OUTBOX_POLL_INTERVAL=2.0  # Poll every 2 seconds
OUTBOX_BATCH_SIZE=10
```

### Stricter Escalation

For critical booking confirmations that need faster escalation:

```bash
ESCALATION_FAILURE_THRESHOLD=30  # Escalate after 30 minutes
ESCALATION_MIN_RETRIES=2         # Escalate after 2 failed retries
ESCALATION_CHECK_INTERVAL=5      # Check every 5 minutes
```

## Troubleshooting

### Workers Not Starting

Check the logs:

```bash
fly logs -a healthcare-clinic-backend | grep -E "Error.*worker"
```

Common issues:
- Missing Supabase credentials
- Database connection issues
- Invalid environment variable values

### Messages Stuck in "pending"

Check the outbox processor logs:

```bash
fly logs -a healthcare-clinic-backend | grep "OutboxProcessor"
```

Possible causes:
- Worker crashed (check for exceptions)
- Database connection lost
- Evolution API unavailable

### No Escalation Alerts

Verify:
1. `SLACK_OPS_WEBHOOK_URL` is set correctly
2. Messages meet escalation criteria (retries >= min_retries, age > threshold)
3. Worker is running (check logs)
4. Slack webhook is valid (test it manually)

Test the webhook:

```bash
curl -X POST https://hooks.slack.com/services/YOUR/WEBHOOK/URL \
  -H 'Content-Type: application/json' \
  -d '{"text":"Test alert from healthcare backend"}'
```

## Next Steps

After configuration:
1. ✅ Set all environment variables
2. ✅ Deploy the application
3. ✅ Verify workers are running
4. ✅ Send a test message through the system
5. ✅ Monitor logs and Slack for any issues
