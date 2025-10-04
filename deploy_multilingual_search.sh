#!/bin/bash
# Deploy Multilingual Search to Production
# This script applies database migrations and deploys updated code

set -e  # Exit on error

echo "🚀 Deploying Multilingual Search Improvements"
echo "=============================================="
echo ""

# Step 1: Apply database migrations
echo "📦 Step 1: Applying database migrations..."
echo ""

echo "  Migration 1: Creating multilingual search infrastructure..."
python3 apply_migration.py /Users/dmitrymolchanov/Programs/livekit-voice-agent/migrations/enhance_services_multilingual_search.sql

echo ""
echo "  Migration 2: Seeding service aliases..."
python3 apply_migration.py /Users/dmitrymolchanov/Programs/livekit-voice-agent/migrations/seed_service_aliases_multilingual.sql

echo ""
echo "✅ Database migrations applied successfully!"
echo ""

# Step 2: Verify migrations
echo "🔍 Step 2: Verifying migrations..."
echo ""

python3 -c "
from app.db.supabase_client import get_supabase_client

supabase = get_supabase_client()

# Check RPC exists
try:
    result = supabase.rpc('search_services_multilingual', {
        'p_clinic_id': 'e0c84f56-235d-49f2-9a44-37c1be579afc',
        'p_query': 'пломба',
        'p_limit': 1,
        'p_min_score': 0.01,
        'p_session_id': None
    }).execute()
    print(f'  ✅ RPC search_services_multilingual exists and returned {len(result.data or [])} results')
except Exception as e:
    print(f'  ❌ RPC verification failed: {e}')
    exit(1)

# Check aliases table
try:
    result = supabase.table('service_aliases').select('count', count='exact').execute()
    count = result.count
    print(f'  ✅ service_aliases table has {count} rows')
    if count == 0:
        print('  ⚠️  Warning: No aliases found. Migration may have failed.')
except Exception as e:
    print(f'  ❌ Alias table verification failed: {e}')
    exit(1)

print('')
print('✅ All verifications passed!')
"

echo ""

# Step 3: Deploy to Fly.io
echo "🚢 Step 3: Deploying to Fly.io..."
echo ""
echo "  This will deploy the updated intent_router with multilingual RPC calls."
echo ""

read -p "  Ready to deploy? (y/n) " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]
then
    fly deploy --app healthcare-clinic-backend
    echo ""
    echo "✅ Deployment complete!"
else
    echo ""
    echo "⏸️  Deployment skipped. Run 'fly deploy' manually when ready."
fi

echo ""
echo "=============================================="
echo "🎉 Multilingual Search Deployment Complete!"
echo "=============================================="
echo ""
echo "Next steps:"
echo "1. Test with: 'а сколько стоит пломба зуба?'"
echo "2. Check logs: fly logs --app healthcare-clinic-backend"
echo "3. Monitor telemetry: SELECT * FROM healthcare.search_telemetry ORDER BY created_at DESC LIMIT 10;"
echo ""
