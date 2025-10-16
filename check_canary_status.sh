#!/bin/bash
set -e

###############################################################################
# Canary Status Check Script
#
# Purpose: Automated monitoring for mem0-redis canary deployment
# Usage: ./check_canary_status.sh [--alert-only] [--json]
#
# Options:
#   --alert-only    Only output if alerts detected
#   --json         Output in JSON format
#   --help         Show this help message
###############################################################################

# Parse command line arguments
ALERT_ONLY=false
JSON_OUTPUT=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --alert-only)
      ALERT_ONLY=true
      shift
      ;;
    --json)
      JSON_OUTPUT=true
      shift
      ;;
    --help)
      echo "Usage: $0 [--alert-only] [--json] [--help]"
      echo ""
      echo "Options:"
      echo "  --alert-only    Only output if alerts detected"
      echo "  --json         Output in JSON format"
      echo "  --help         Show this help message"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

# Color codes
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Alert tracking
ALERTS=()

# Helper function to add alerts
add_alert() {
  local severity=$1
  local message=$2
  ALERTS+=("[$severity] $message")
}

# Helper function to check metric threshold
check_threshold() {
  local metric_name=$1
  local value=$2
  local threshold=$3
  local comparison=$4  # "gt" (greater than) or "lt" (less than)
  local severity=$5    # "warning" or "critical"

  if [ "$comparison" = "gt" ]; then
    if (( $(echo "$value > $threshold" | bc -l) )); then
      add_alert "$severity" "$metric_name: $value exceeds threshold $threshold"
      return 1
    fi
  elif [ "$comparison" = "lt" ]; then
    if (( $(echo "$value < $threshold" | bc -l) )); then
      add_alert "$severity" "$metric_name: $value below threshold $threshold"
      return 1
    fi
  fi
  return 0
}

###############################################################################
# Main Status Check
###############################################################################

if [ "$JSON_OUTPUT" = false ]; then
  echo "=== Canary Status Check - $(date -u '+%Y-%m-%d %H:%M:%S UTC') ==="
  echo
fi

# 1. Feature Flags
if [ "$JSON_OUTPUT" = false ]; then
  echo -e "${BLUE}📋 Feature Flags:${NC}"
fi

FEATURE_FLAGS=$(timeout 10s fly logs -a healthcare-clinic-backend --tail 100 2>/dev/null | grep "Feature flags loaded" | tail -1 || echo "No feature flags found")

if [ "$JSON_OUTPUT" = false ]; then
  echo "$FEATURE_FLAGS"
  echo
fi

# Extract values from feature flags (using sed instead of grep -P for macOS compatibility)
FAST_PATH=$(echo "$FEATURE_FLAGS" | sed -n 's/.*fast_path=\([^ ,]*\).*/\1/p' || echo "unknown")
MEM0_READS=$(echo "$FEATURE_FLAGS" | sed -n 's/.*mem0_reads=\([^ ,]*\).*/\1/p' || echo "unknown")
CANARY_RATE=$(echo "$FEATURE_FLAGS" | sed -n 's/.*canary_rate=\([0-9.]*\).*/\1/p' || echo "0.0")

# 2. Application Health
if [ "$JSON_OUTPUT" = false ]; then
  echo -e "${BLUE}🏥 Application Health:${NC}"
fi

HEALTH_STATUS=$(curl -s https://healthcare-clinic-backend.fly.dev/health 2>/dev/null || echo '{"status":"error","message":"Health check failed"}')
HEALTH_OK=$(echo "$HEALTH_STATUS" | jq -r '.status' 2>/dev/null || echo "error")

if [ "$JSON_OUTPUT" = false ]; then
  echo "$HEALTH_STATUS" | jq '.' 2>/dev/null || echo "$HEALTH_STATUS"
  echo
fi

if [ "$HEALTH_OK" != "ok" ] && [ "$HEALTH_OK" != "healthy" ]; then
  add_alert "critical" "Health check failed: $HEALTH_OK"
fi

# 3. Recent Logs Analysis
if [ "$JSON_OUTPUT" = false ]; then
  echo -e "${BLUE}📊 Recent Activity (last 100 log lines):${NC}"
fi

RECENT_LOGS=$(timeout 10s fly logs -a healthcare-clinic-backend --tail 100 2>/dev/null || echo "")

# 3a. Latency Analysis
LATENCY_LOGS=$(echo "$RECENT_LOGS" | grep -i "processing_time\|latency\|P50\|P95\|P99" | tail -10)
if [ -n "$LATENCY_LOGS" ]; then
  if [ "$JSON_OUTPUT" = false ]; then
    echo -e "${GREEN}⏱️  Latency Samples:${NC}"
    echo "$LATENCY_LOGS" | head -5
    echo
  fi

  # Extract P50 if available (using sed for macOS compatibility)
  P50=$(echo "$LATENCY_LOGS" | sed -n 's/.*P50[=: ]*\([0-9.]*\).*/\1/p' | head -1)
  if [ -n "$P50" ]; then
    check_threshold "P50 Latency" "$P50" "500" "gt" "warning"
    check_threshold "P50 Latency" "$P50" "1000" "gt" "critical"
  fi
fi

# 3b. Error Analysis
ERROR_COUNT=$(echo "$RECENT_LOGS" | grep -ci "error\|exception\|failed" || echo "0")
TOTAL_REQUESTS=$(echo "$RECENT_LOGS" | grep -ci "request\|processing" || echo "1")
ERROR_RATE=$(echo "scale=2; ($ERROR_COUNT / $TOTAL_REQUESTS) * 100" | bc -l 2>/dev/null || echo "0")

if [ "$JSON_OUTPUT" = false ]; then
  echo -e "${GREEN}❌ Error Rate:${NC}"
  echo "Errors: $ERROR_COUNT / Requests: $TOTAL_REQUESTS = ${ERROR_RATE}%"
  echo
fi

check_threshold "Error Rate" "$ERROR_RATE" "1" "gt" "warning"
check_threshold "Error Rate" "$ERROR_RATE" "2" "gt" "critical"

# 3c. Cache Performance
CACHE_LOGS=$(echo "$RECENT_LOGS" | grep -i "cache_hit\|cache_miss" | tail -20)
if [ -n "$CACHE_LOGS" ]; then
  CACHE_HITS=$(echo "$CACHE_LOGS" | grep -ci "cache_hit.*true" || echo "0")
  CACHE_TOTAL=$(echo "$CACHE_LOGS" | wc -l)
  if [ "$CACHE_TOTAL" -gt 0 ]; then
    CACHE_HIT_RATE=$(echo "scale=2; ($CACHE_HITS / $CACHE_TOTAL) * 100" | bc -l 2>/dev/null || echo "0")

    if [ "$JSON_OUTPUT" = false ]; then
      echo -e "${GREEN}💾 Cache Hit Rate:${NC}"
      echo "Hits: $CACHE_HITS / Total: $CACHE_TOTAL = ${CACHE_HIT_RATE}%"
      echo
    fi

    check_threshold "Cache Hit Rate" "$CACHE_HIT_RATE" "95" "lt" "warning"
    check_threshold "Cache Hit Rate" "$CACHE_HIT_RATE" "90" "lt" "critical"
  fi
fi

# 3d. mem0 Status
MEM0_LOGS=$(echo "$RECENT_LOGS" | grep -i "mem0" | tail -10)
if [ -n "$MEM0_LOGS" ]; then
  MEM0_SUCCESS=$(echo "$MEM0_LOGS" | grep -ci "success.*true" || echo "0")
  MEM0_TOTAL=$(echo "$MEM0_LOGS" | wc -l)
  if [ "$MEM0_TOTAL" -gt 0 ]; then
    MEM0_SUCCESS_RATE=$(echo "scale=2; ($MEM0_SUCCESS / $MEM0_TOTAL) * 100" | bc -l 2>/dev/null || echo "0")

    if [ "$JSON_OUTPUT" = false ]; then
      echo -e "${GREEN}🧠 mem0 Write Success:${NC}"
      echo "Success: $MEM0_SUCCESS / Total: $MEM0_TOTAL = ${MEM0_SUCCESS_RATE}%"
      echo
    fi

    check_threshold "mem0 Success Rate" "$MEM0_SUCCESS_RATE" "99" "lt" "warning"
    check_threshold "mem0 Success Rate" "$MEM0_SUCCESS_RATE" "95" "lt" "critical"
  fi
fi

# 3e. Circuit Breaker Status
CIRCUIT_BREAKER=$(echo "$RECENT_LOGS" | grep -i "circuit.breaker" | tail -3)
if [ -n "$CIRCUIT_BREAKER" ]; then
  if echo "$CIRCUIT_BREAKER" | grep -qi "open"; then
    add_alert "critical" "Circuit breaker is OPEN"
    if [ "$JSON_OUTPUT" = false ]; then
      echo -e "${RED}🔌 Circuit Breaker: OPEN ⚠️${NC}"
      echo "$CIRCUIT_BREAKER"
      echo
    fi
  elif [ "$JSON_OUTPUT" = false ]; then
    echo -e "${GREEN}🔌 Circuit Breaker: Closed ✓${NC}"
    echo
  fi
fi

# 4. Redis Health
if [ "$JSON_OUTPUT" = false ]; then
  echo -e "${BLUE}📦 Redis Health:${NC}"
fi

REDIS_MEMORY=$(timeout 10s fly redis info 2>/dev/null | grep "used_memory_human" || echo "unavailable")
REDIS_EVICTED=$(timeout 10s fly redis info 2>/dev/null | grep "evicted_keys" || echo "unavailable")

if [ "$JSON_OUTPUT" = false ]; then
  echo "$REDIS_MEMORY"
  echo "$REDIS_EVICTED"
  echo
fi

# Check for evictions (using sed for macOS compatibility)
EVICTED_COUNT=$(echo "$REDIS_EVICTED" | sed -n 's/.*evicted_keys:\([0-9]*\).*/\1/p' || echo "0")
EVICTED_COUNT=${EVICTED_COUNT:-0}
if [ "$EVICTED_COUNT" -gt 0 ]; then
  add_alert "warning" "Redis evicting keys: $EVICTED_COUNT"
fi

###############################################################################
# Output Results
###############################################################################

if [ "$JSON_OUTPUT" = true ]; then
  # JSON output
  cat <<EOF
{
  "timestamp": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "feature_flags": {
    "fast_path": "$FAST_PATH",
    "mem0_reads": "$MEM0_READS",
    "canary_rate": $CANARY_RATE
  },
  "health": {
    "status": "$HEALTH_OK"
  },
  "metrics": {
    "p50_latency_ms": ${P50:-null},
    "error_rate_pct": $ERROR_RATE,
    "cache_hit_rate_pct": ${CACHE_HIT_RATE:-null},
    "mem0_success_rate_pct": ${MEM0_SUCCESS_RATE:-null}
  },
  "redis": {
    "evicted_keys": $EVICTED_COUNT
  },
  "alerts": [
$(IFS=$'\n'; for alert in "${ALERTS[@]}"; do echo "    \"$alert\","; done | sed '$ s/,$//')
  ],
  "alert_count": ${#ALERTS[@]}
}
EOF
else
  # Human-readable output
  echo "=== Status Summary ==="
  echo

  if [ ${#ALERTS[@]} -eq 0 ]; then
    echo -e "${GREEN}✅ All metrics within normal range${NC}"
    echo -e "${GREEN}✅ No alerts detected${NC}"
  else
    echo -e "${RED}⚠️  ${#ALERTS[@]} Alert(s) Detected:${NC}"
    for alert in "${ALERTS[@]}"; do
      echo -e "${YELLOW}  - $alert${NC}"
    done
    echo
    echo -e "${YELLOW}📖 Refer to docs/OPERATIONAL_RUNBOOKS.md for remediation${NC}"
  fi

  echo
  echo "=== Check Complete ==="
fi

# Exit with error code if alerts present (for automation)
if [ ${#ALERTS[@]} -gt 0 ]; then
  exit 1
else
  exit 0
fi
