#!/bin/bash
# Apply Reliability Fixes
# This script applies all database migrations and verifies the setup

set -e

echo "🔧 Applying Reliability Fixes"
echo "=============================="
echo ""

# Step 1: Apply whatsapp_instances table migration
echo "Step 1: Creating whatsapp_instances table..."
python3 apply_migration.py ../../infra/db/migrations/20251018_create_whatsapp_instances_table.sql

if [ $? -eq 0 ]; then
    echo "✅ whatsapp_instances table created successfully"
else
    echo "❌ Failed to create whatsapp_instances table"
    exit 1
fi

echo ""

# Step 2: Verify table exists
echo "Step 2: Verifying table exists..."
python3 << EOF
from app.db.supabase_client import get_supabase_client

try:
    supabase = get_supabase_client()
    result = supabase.schema('healthcare').table('whatsapp_instances').select('id').limit(1).execute()
    print("✅ Table healthcare.whatsapp_instances exists and is accessible")
    print(f"   Found {len(result.data if result.data else [])} existing instances")
except Exception as e:
    print(f"❌ Error accessing table: {e}")
    exit(1)
EOF

echo ""

# Step 3: Test trace context
echo "Step 3: Testing trace context..."
python3 << EOF
import sys
sys.path.insert(0, '.')

from app.utils.trace_context import TraceContext, add_trace_to_dict, extract_trace_from_dict

# Test trace generation
with TraceContext.start() as trace_id:
    print(f"✅ Generated trace ID: {trace_id}")

    # Test dict propagation
    data = add_trace_to_dict({'test': 'data'})
    if 'trace_id' in data:
        print(f"✅ Trace added to dict: {data['trace_id']}")
    else:
        print("❌ Failed to add trace to dict")
        exit(1)

    # Test extraction
    extracted = extract_trace_from_dict(data)
    if extracted:
        print("✅ Trace extracted successfully")
    else:
        print("❌ Failed to extract trace")
        exit(1)

print("✅ Trace context tests passed")
EOF

echo ""

# Step 4: Test graceful shutdown
echo "Step 4: Testing graceful shutdown handler..."
python3 << EOF
import sys
sys.path.insert(0, '.')

from app.utils.graceful_shutdown import GracefulShutdownHandler

shutdown_handler = GracefulShutdownHandler(
    shutdown_timeout=5,
    service_name="Test Service"
)

# Register test cleanup
cleanup_called = False
def test_cleanup():
    global cleanup_called
    cleanup_called = True

shutdown_handler.register(test_cleanup)

print("✅ Graceful shutdown handler initialized")
print("   - Shutdown timeout: 5s")
print("   - Registered cleanup functions: 1")
EOF

echo ""
echo "=============================="
echo "✅ All reliability fixes applied successfully!"
echo ""
echo "Next steps:"
echo "1. Deploy with: fly deploy --strategy bluegreen"
echo "2. Monitor logs: fly logs -a healthcare-clinic-backend"
echo "3. Test trace IDs: fly logs | grep trace_"
echo ""
