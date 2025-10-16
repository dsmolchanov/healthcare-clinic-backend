#!/bin/bash
# Canary Deployment Script for mem0-redis Feature Flags
# Gradually enables features with incremental traffic sampling
#
# Usage:
#   ./scripts/canary-deploy.sh stage1    # 10% canary
#   ./scripts/canary-deploy.sh stage2    # 50% canary
#   ./scripts/canary-deploy.sh full      # 100% rollout
#   ./scripts/canary-deploy.sh custom 0.25   # Custom 25% canary

set -e  # Exit on error

APP_NAME="healthcare-clinic-backend"
TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")

echo "========================================="
echo "Feature Flag Canary Deployment"
echo "App: $APP_NAME"
echo "Time: $TIMESTAMP"
echo "========================================="

# Parse stage
STAGE=${1:-"stage1"}
SAMPLE_RATE=""
FAST_PATH=""
MEM0_READS=""
MEM0_SHADOW=""

case "$STAGE" in
    "stage1")
        echo "Stage 1: Shadow mode + 10% canary"
        SAMPLE_RATE="0.1"
        FAST_PATH="false"
        MEM0_READS="false"
        MEM0_SHADOW="true"
        ;;
    "stage2")
        echo "Stage 2: Fast-path enabled + 50% canary"
        SAMPLE_RATE="0.5"
        FAST_PATH="true"
        MEM0_READS="false"
        MEM0_SHADOW="true"
        ;;
    "stage3")
        echo "Stage 3: mem0 reads enabled + 50% canary"
        SAMPLE_RATE="0.5"
        FAST_PATH="true"
        MEM0_READS="true"
        MEM0_SHADOW="false"
        ;;
    "full")
        echo "Full rollout: All features enabled (100%)"
        SAMPLE_RATE="1.0"
        FAST_PATH="true"
        MEM0_READS="true"
        MEM0_SHADOW="false"
        ;;
    "custom")
        if [ -z "$2" ]; then
            echo "Error: Custom stage requires sample rate (0.0-1.0)"
            echo "Usage: ./scripts/canary-deploy.sh custom 0.25"
            exit 1
        fi
        SAMPLE_RATE="$2"
        FAST_PATH="true"
        MEM0_READS="true"
        MEM0_SHADOW="false"
        echo "Custom deployment: $SAMPLE_RATE sample rate"
        ;;
    *)
        echo "Error: Invalid stage '$STAGE'"
        echo "Valid stages: stage1, stage2, stage3, full, custom"
        exit 1
        ;;
esac

echo ""
echo "Configuration:"
echo "  FAST_PATH_ENABLED=$FAST_PATH"
echo "  MEM0_READS_ENABLED=$MEM0_READS"
echo "  MEM0_SHADOW_MODE=$MEM0_SHADOW"
echo "  CANARY_SAMPLE_RATE=$SAMPLE_RATE"
echo ""

# Confirm with user
read -p "Continue with deployment? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Deployment cancelled."
    exit 0
fi

echo ""
echo "Deploying feature flags..."
fly secrets set \
    FAST_PATH_ENABLED="$FAST_PATH" \
    MEM0_READS_ENABLED="$MEM0_READS" \
    MEM0_SHADOW_MODE="$MEM0_SHADOW" \
    CANARY_SAMPLE_RATE="$SAMPLE_RATE" \
    --app "$APP_NAME"

echo ""
echo "✅ Feature flags updated successfully!"
echo ""

# Wait for deployment
echo "Waiting for deployment to complete (30 seconds)..."
sleep 30

# Check status
echo ""
echo "Checking deployment status..."
fly status --app "$APP_NAME" | head -n 10
echo ""

echo "========================================="
echo "Deployment completed at $(date +"%Y-%m-%d %H:%M:%S")"
echo "========================================="
echo ""
echo "Monitoring checklist:"
echo "  [ ] Check logs: fly logs --app $APP_NAME"
echo "  [ ] Monitor Prometheus metrics (P50/P95 latency)"
echo "  [ ] Verify Grafana dashboards"
echo "  [ ] Check error rates and mem0 success rates"
echo "  [ ] Monitor for 15-30 minutes before next stage"
echo ""

if [ "$STAGE" == "stage1" ]; then
    echo "Next step: ./scripts/canary-deploy.sh stage2"
elif [ "$STAGE" == "stage2" ]; then
    echo "Next step: ./scripts/canary-deploy.sh stage3"
elif [ "$STAGE" == "stage3" ]; then
    echo "Next step: ./scripts/canary-deploy.sh full"
fi

echo ""
echo "To rollback: ./scripts/rollback-feature-flags.sh"
