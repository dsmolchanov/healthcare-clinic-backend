#!/bin/bash
# Cleanup invalid Evolution instances

EVOLUTION_URL="https://evolution-api-prod.fly.dev"
API_KEY="${EVOLUTION_API_KEY:-}"

# List of invalid instances to delete
INSTANCES=(
    "clinic-4e8ddba1-ad52-4613-9a03-ec64636b3f6c-1760994893945"
    "complete-test-1757901945"
    "final-rpc-test-1757903110"
    "frontend-format-test-1757903854"
    "frontend-test-camel"
    "test-debug-422"
    "test-final-1757902500"
    "test-from-curl"
    "test-instance"
    "test-snakecase"
)

echo "=================================================="
echo "Evolution Instance Cleanup"
echo "=================================================="
echo ""

# Check if API key is set
if [ -z "$API_KEY" ]; then
    echo "⚠️  Warning: EVOLUTION_API_KEY not set"
    echo "   Some operations may require authentication"
    echo ""
fi

# List current instances
echo "📋 Current instances:"
echo ""
if [ -n "$API_KEY" ]; then
    curl -s -X GET "$EVOLUTION_URL/instance/fetchInstances" \
        -H "apikey: $API_KEY" | jq -r '.[] | .instance.instanceName + " : " + .instance.status'
else
    curl -s -X GET "$EVOLUTION_URL/instance/fetchInstances" | jq -r '.[] | .instance.instanceName + " : " + .instance.status'
fi

echo ""
echo "=================================================="
echo "Deleting invalid instances..."
echo "=================================================="
echo ""

for instance in "${INSTANCES[@]}"; do
    echo "🗑️  Deleting: $instance"

    if [ -n "$API_KEY" ]; then
        response=$(curl -s -X DELETE "$EVOLUTION_URL/instance/delete/$instance" \
            -H "apikey: $API_KEY" \
            -w "\n%{http_code}")
    else
        response=$(curl -s -X DELETE "$EVOLUTION_URL/instance/delete/$instance" \
            -w "\n%{http_code}")
    fi

    http_code=$(echo "$response" | tail -n1)
    body=$(echo "$response" | head -n-1)

    if [ "$http_code" -eq 200 ] || [ "$http_code" -eq 204 ]; then
        echo "   ✅ Deleted successfully"
    elif [ "$http_code" -eq 404 ]; then
        echo "   ⏭️  Not found (already deleted)"
    else
        echo "   ❌ Failed (HTTP $http_code)"
        echo "   Response: $body"
    fi

    echo ""
done

echo "=================================================="
echo "Cleanup complete!"
echo "=================================================="
echo ""
echo "Next steps:"
echo "1. Create new instance via frontend or Python script"
echo "2. Scan QR code to authenticate"
echo "3. Test webhook connectivity"
echo ""
