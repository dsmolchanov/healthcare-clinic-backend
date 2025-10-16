#!/bin/bash
# Rollback Script for mem0-redis Feature Flags
# Disables all feature flags in <2 minutes for production incidents
#
# Usage:
#   ./scripts/rollback-feature-flags.sh                  # Disable all features
#   ./scripts/rollback-feature-flags.sh --partial        # Disable fast-path only
#   ./scripts/rollback-feature-flags.sh --mem0-only      # Disable mem0 only

set -e  # Exit on error

APP_NAME="healthcare-clinic-backend"
TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")

echo "========================================="
echo "Feature Flag Rollback Script"
echo "App: $APP_NAME"
echo "Time: $TIMESTAMP"
echo "========================================="

# Parse command line arguments
MODE="full"
if [ "$1" == "--partial" ]; then
    MODE="partial"
    echo "Mode: Partial rollback (fast-path only)"
elif [ "$1" == "--mem0-only" ]; then
    MODE="mem0"
    echo "Mode: mem0 rollback only"
else
    echo "Mode: Full rollback (all features)"
fi

# Function to set a single secret
set_secret() {
    local key=$1
    local value=$2
    echo "Setting $key=$value..."
    fly secrets set "$key=$value" --app "$APP_NAME" 2>&1 | grep -v "^Secrets are"
}

# Function to check current deployment status
check_status() {
    echo "Checking deployment status..."
    fly status --app "$APP_NAME" | head -n 10
}

# Start rollback
echo ""
echo "Starting rollback..."
echo ""

if [ "$MODE" == "full" ]; then
    # Full rollback: Disable everything
    echo "Disabling all features..."
    fly secrets set \
        FAST_PATH_ENABLED=false \
        MEM0_READS_ENABLED=false \
        MEM0_SHADOW_MODE=false \
        CANARY_SAMPLE_RATE=0.0 \
        --app "$APP_NAME"

elif [ "$MODE" == "partial" ]; then
    # Partial rollback: Only disable fast-path
    echo "Disabling fast-path routing..."
    fly secrets set \
        FAST_PATH_ENABLED=false \
        CANARY_SAMPLE_RATE=0.0 \
        --app "$APP_NAME"

elif [ "$MODE" == "mem0" ]; then
    # mem0 rollback: Disable mem0 reads but keep writes in shadow mode
    echo "Disabling mem0 reads (keeping shadow mode writes)..."
    fly secrets set \
        MEM0_READS_ENABLED=false \
        MEM0_SHADOW_MODE=true \
        --app "$APP_NAME"
fi

echo ""
echo "✅ Feature flags updated successfully!"
echo ""

# Wait for deployment
echo "Waiting for deployment to complete (30 seconds)..."
sleep 30

# Check status
echo ""
check_status
echo ""

echo "========================================="
echo "Rollback completed at $(date +"%Y-%m-%d %H:%M:%S")"
echo "========================================="
echo ""
echo "Next steps:"
echo "1. Monitor logs: fly logs --app $APP_NAME"
echo "2. Check metrics in Grafana/Prometheus"
echo "3. Verify incident resolution"
echo ""
echo "To re-enable features, use the canary deployment script:"
echo "  ./scripts/canary-deploy.sh"
