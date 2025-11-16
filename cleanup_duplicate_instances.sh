#!/bin/bash
# Cleanup duplicate Evolution instances for a clinic

set -e

CLINIC_ID="$1"

if [ -z "$CLINIC_ID" ]; then
    echo "Usage: $0 <clinic_id>"
    exit 1
fi

EVOLUTION_API_URL="https://evolution-api-prod.fly.dev"
EVOLUTION_GLOBAL_KEY="plaintalk-global-key"

echo "🔍 Finding instances for clinic: $CLINIC_ID"

# Get all instances
INSTANCES=$(curl -s "$EVOLUTION_API_URL/instance/fetchInstances" \
    -H "apikey: $EVOLUTION_GLOBAL_KEY" | \
    python3 -c "import sys, json; instances = json.load(sys.stdin); print('\n'.join([i['instance']['instanceName'] for i in instances if i['instance']['instanceName'].startswith('clinic-$CLINIC_ID')]))" || echo "")

if [ -z "$INSTANCES" ]; then
    echo "✅ No instances found for clinic $CLINIC_ID"
    exit 0
fi

echo "Found instances:"
echo "$INSTANCES" | sed 's/^/  - /'

# Count instances
INSTANCE_COUNT=$(echo "$INSTANCES" | wc -l | tr -d ' ')
echo ""
echo "📊 Total: $INSTANCE_COUNT instances"

# Delete each instance
echo ""
echo "🗑️  Deleting instances..."
DELETED=0

while IFS= read -r INSTANCE_NAME; do
    if [ -n "$INSTANCE_NAME" ]; then
        echo "  Deleting: $INSTANCE_NAME"

        HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE \
            "$EVOLUTION_API_URL/instance/delete/$INSTANCE_NAME" \
            -H "apikey: $EVOLUTION_GLOBAL_KEY")

        if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "404" ]; then
            echo "    ✅ Deleted (HTTP $HTTP_CODE)"
            DELETED=$((DELETED + 1))
        else
            echo "    ⚠️  Failed (HTTP $HTTP_CODE)"
        fi
    fi
done <<< "$INSTANCES"

echo ""
echo "✅ Cleanup complete: Deleted $DELETED/$INSTANCE_COUNT instances"
echo ""
echo "📋 Next steps:"
echo "1. Delete database record from whatsapp_integrations table"
echo "2. Create NEW integration in the UI"
echo "3. Scan QR code"
echo "4. Verify 'connected' status"
