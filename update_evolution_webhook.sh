#!/bin/bash
# Update Evolution API webhook for existing instance

EVOLUTION_API_URL="https://evolution-api-prod.fly.dev"
OLD_INSTANCE="clinic-3e411ecb-3411-4add-91e2-8fa897310cb0-1757905315621"
NEW_WEBHOOK="https://healthcare-clinic-backend.fly.dev/webhooks/evolution/clinic-4e8ddba1-ad52-4613-9a03-ec64636b3f6c-1757905315621"

echo "Updating webhook for instance: $OLD_INSTANCE"
echo "New webhook URL: $NEW_WEBHOOK"

curl -X POST "$EVOLUTION_API_URL/webhook/set/$OLD_INSTANCE" \
  -H "Content-Type: application/json" \
  -d "{
    \"url\": \"$NEW_WEBHOOK\",
    \"webhook_by_events\": false,
    \"webhook_base64\": false,
    \"events\": [
      \"QRCODE_UPDATED\",
      \"MESSAGES_UPSERT\",
      \"MESSAGES_UPDATE\",
      \"SEND_MESSAGE\",
      \"CONNECTION_UPDATE\"
    ]
  }"

echo ""
echo "Webhook updated! The old instance will now send webhooks with the new organization ID in the URL."
