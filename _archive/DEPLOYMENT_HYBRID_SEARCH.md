# Hybrid Multi-Stage Search Deployment Guide

## Overview

This guide covers deploying the hybrid multi-stage search system with i18n support implemented on October 17, 2025.

## What Was Changed

### Code Changes
1. **`app/services/clinic_data_cache.py`**
   - Extended to load 10 i18n fields (name_ru, name_en, name_es, name_pt, name_he + descriptions)
   - Added `search_cached_services()` method for language-aware search

2. **`app/services/hybrid_search_service.py`** (NEW)
   - 5-stage cascading search implementation
   - Language detection and routing
   - Comprehensive telemetry

3. **`app/services/reservation_tools.py`**
   - Integrated HybridSearchService into `_get_service_by_name()`

### Database Changes (Already Applied ✅)
1. `20251017_add_services_i18n_columns.sql` - Added i18n columns
2. `20251017_add_spanish_portuguese_fts_vectors.sql` - Added FTS vectors
3. `20251017_update_search_vector_trigger_i18n.sql` - Updated trigger
4. `20251017_populate_sample_translations.sql` - Populated sample translations

## Deployment Steps

### 1. Pre-Deployment Verification

Check that migrations were applied:
```bash
# SSH into Fly.io app
fly ssh console -a healthcare-clinic-backend

# In the container
python3 -c "
from app.database import create_supabase_client
supabase = create_supabase_client()
result = supabase.raw('SELECT column_name FROM information_schema.columns WHERE table_schema=\'healthcare\' AND table_name=\'services\' AND column_name LIKE \'name_%\'').execute()
print('i18n columns:', result.data)
"
```

Expected output: Should show `name_ru`, `name_en`, `name_es`, `name_pt`, `name_he`

### 2. Deploy Code Changes

```bash
# From project root
cd apps/healthcare-backend

# Deploy to Fly.io
fly deploy --strategy immediate

# Monitor deployment
fly logs -a healthcare-clinic-backend
```

### 3. Post-Deployment: Cache Invalidation

The cache will auto-refresh on first request, but you can force it:

```bash
# SSH into deployed app
fly ssh console -a healthcare-clinic-backend

# Inside container
python3 invalidate_service_cache.py

# Or for specific clinic
python3 invalidate_service_cache.py --clinic-id <CLINIC_UUID>
```

### 4. Verification

#### Test Russian Query
```bash
# Via Fly.io SSH
python3 -c "
import asyncio
from app.services.hybrid_search_service import HybridSearchService, EntityType
from app.config import get_redis_client
from app.database import create_supabase_client

async def test():
    redis = get_redis_client()
    supabase = create_supabase_client()
    clinic_id = '<YOUR_CLINIC_ID>'

    search = HybridSearchService(clinic_id, redis, supabase)
    result = await search.search(
        query='пломба',
        entity_type=EntityType.SERVICE,
        language='ru',
        limit=5
    )

    print('Success:', result['success'])
    print('Stage:', result['search_metadata']['search_stage'])
    print('Results:', len(result['results']))
    if result['results']:
        print('First result:', result['results'][0]['name'])
        print('Russian name:', result['results'][0].get('name_ru'))

asyncio.run(test())
"
```

Expected output:
```
Success: True
Stage: cache_exact
Results: 1
First result: Composite Filling
Russian name: Композитная пломба
```

#### Test via WhatsApp
1. Send message: "Сколько стоит пломба?" (How much is a filling?)
2. Agent should find the filling service using Russian search
3. Check logs for search stage: `fly logs | grep "Search completed"`

Expected log entry:
```
✅ Search completed: stage=cache_exact results=1 latency=0.8ms
```

### 5. Monitoring

Key metrics to watch:

```bash
# Monitor search performance
fly logs -a healthcare-clinic-backend | grep "🔍 Hybrid search"

# Check cache hit rates
fly logs -a healthcare-clinic-backend | grep "Cache HIT"

# Watch for fallback stage usage (should be < 5%)
fly logs -a healthcare-clinic-backend | grep "Fallback match"
```

## Rollback Plan

If issues arise:

### Quick Rollback (Code Only)
```bash
# Roll back to previous deployment
fly releases -a healthcare-clinic-backend
fly rollback <previous-version> -a healthcare-clinic-backend
```

### Full Rollback (Database + Code)
```sql
-- Rollback database changes (if needed)
BEGIN;

-- Remove i18n columns
ALTER TABLE healthcare.services
    DROP COLUMN IF EXISTS name_ru,
    DROP COLUMN IF EXISTS name_en,
    DROP COLUMN IF EXISTS name_es,
    DROP COLUMN IF EXISTS name_pt,
    DROP COLUMN IF EXISTS name_he,
    DROP COLUMN IF EXISTS description_ru,
    DROP COLUMN IF EXISTS description_en,
    DROP COLUMN IF EXISTS description_es,
    DROP COLUMN IF EXISTS description_pt,
    DROP COLUMN IF EXISTS description_he,
    DROP COLUMN IF EXISTS search_vector_es,
    DROP COLUMN IF EXISTS search_vector_pt;

COMMIT;
```

Then roll back code deployment.

## Common Issues

### Issue: Cache not loading i18n fields
**Symptom**: Russian queries still not working after deployment

**Solution**:
```bash
fly ssh console -a healthcare-clinic-backend
python3 invalidate_service_cache.py
```

### Issue: Translations missing
**Symptom**: Services return but without Russian names

**Solution**: Re-run translation population:
```bash
python3 apply_migration.py ../../migrations/20251017_populate_sample_translations.sql
python3 invalidate_service_cache.py
```

### Issue: High latency
**Symptom**: Search taking > 100ms

**Check**:
1. Cache hit rate: Should be > 85%
2. Fallback stage usage: Should be < 5%
3. Redis connection: Check `fly logs | grep Redis`

**Solution**: Warm up cache or investigate query patterns

## Performance Targets

- **P50 latency**: < 5ms (cache hits)
- **P95 latency**: < 100ms
- **P99 latency**: < 200ms
- **Cache hit rate**: > 85% (after warmup)
- **Fallback stage usage**: < 5%

## Adding More Translations

To add translations for additional services:

```sql
-- Add custom translations
UPDATE healthcare.services
SET
    name_ru = 'Ваше название',
    name_es = 'Su nombre',
    name_pt = 'Seu nome',
    description_ru = 'Описание',
    description_es = 'Descripción',
    description_pt = 'Descrição'
WHERE name = 'Your Service Name'
  AND clinic_id = '<YOUR_CLINIC_ID>';

-- Then invalidate cache
```

## Support

For issues or questions:
- Check logs: `fly logs -a healthcare-clinic-backend`
- Review implementation: `thoughts/shared/plans/hybrid-multistage-search-i18n-IMPLEMENTATION.md`
- See original plan: `thoughts/shared/plans/hybrid-multistage-search-i18n.md`
