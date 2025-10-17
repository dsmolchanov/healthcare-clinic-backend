# Automatic Translation Deployment Guide

## Overview

This guide covers deploying the automatic translation system that uses Google Translate API to automatically translate service names and descriptions when new services are created.

## Architecture

```
New Service → Database Trigger → Edge Function → Google Translate API
                                        ↓
                            Update Translations in DB
                                        ↓
                            Search Vector Trigger (automatic)
                                        ↓
                            Cache Invalidates on Next Request
```

## Prerequisites

1. **Supabase CLI** installed
2. **Google Cloud** account with Translation API enabled
3. **API Key** for Google Translate API
4. **Supabase Service Role Key** (from dashboard)

## Step-by-Step Deployment

### 1. Enable Google Translate API

```bash
# Go to Google Cloud Console
# https://console.cloud.google.com/

# Enable Translation API
# https://console.cloud.google.com/apis/library/translate.googleapis.com

# Create API key
# https://console.cloud.google.com/apis/credentials
# Click "Create Credentials" → "API Key"
# Copy the API key
```

**Important**: Restrict API key to Translation API only for security.

### 2. Deploy Edge Function

```bash
# From project root
cd supabase/functions

# Login to Supabase
supabase login

# Link to your project
supabase link --project-ref your-project-ref

# Set Google Translate API key as secret
supabase secrets set GOOGLE_TRANSLATE_API_KEY=your-api-key-here

# Deploy the function
supabase functions deploy translate-service --no-verify-jwt

# Verify deployment
supabase functions list
```

Output should show:
```
┌─────────────────────┬──────────────┬─────────────────────────────────────┐
│ NAME                │ VERSION      │ URL                                 │
├─────────────────────┼──────────────┼─────────────────────────────────────┤
│ translate-service   │ v1           │ https://xxx.supabase.co/functions/… │
└─────────────────────┴──────────────┴─────────────────────────────────────┘
```

### 3. Configure Database

Get your edge function URL from step 2, then:

```sql
-- Connect to your Supabase database
-- (via Supabase Dashboard SQL Editor or psql)

-- Set edge function URL (replace with your actual URL)
ALTER DATABASE postgres SET app.edge_function_url =
    'https://your-project-ref.supabase.co/functions/v1/translate-service';

-- Set service role key (get from Supabase Dashboard > Settings > API > service_role)
ALTER DATABASE postgres SET app.service_role_key = 'eyJh...your-service-role-key';

-- Reload configuration
SELECT pg_reload_conf();

-- Verify configuration
SHOW app.edge_function_url;
SHOW app.service_role_key;
```

### 4. Apply Migration

```bash
cd apps/healthcare-backend
python3 apply_migration.py ../../migrations/20251017_add_auto_translation_trigger.sql
```

Expected output:
```
🚀 Starting migration application...
✅ Connected to database
🔧 Applying migration...
✅ Migration applied successfully!
```

### 5. Verify Setup

Test the translation system:

```sql
-- Insert a test service
INSERT INTO healthcare.services (
    clinic_id,
    name,
    description,
    category,
    base_price,
    duration_minutes,
    currency,
    active
)
VALUES (
    'your-clinic-id',
    'Test Cleaning Service',
    'Professional dental cleaning',
    'Preventive',
    100,
    30,
    'USD',
    true
);

-- Wait 2-3 seconds for translation to complete

-- Check translations were added
SELECT
    id,
    name,
    name_ru,
    name_es,
    name_pt,
    name_he
FROM healthcare.services
WHERE name = 'Test Cleaning Service';
```

Expected result:
```
name: Test Cleaning Service
name_ru: Тестовая услуга по чистке
name_es: Servicio de limpieza de prueba
name_pt: Serviço de limpeza de teste
name_he: שירות ניקוי בדיקה
```

### 6. Monitor Edge Function

```bash
# Watch logs in real-time
supabase functions logs translate-service --tail

# Check for errors
supabase functions logs translate-service | grep ERROR
```

## Backfill Existing Services

Translate services that already exist without translations:

```sql
-- Find services needing translation
SELECT COUNT(*)
FROM healthcare.services
WHERE active = true
  AND name_ru IS NULL;

-- Trigger translation for existing services (batch of 10)
DO $$
DECLARE
    service_record RECORD;
BEGIN
    FOR service_record IN
        SELECT id, name, description
        FROM healthcare.services
        WHERE active = true
          AND name_ru IS NULL
        LIMIT 10
    LOOP
        -- Trigger edge function by updating the service
        UPDATE healthcare.services
        SET updated_at = NOW()
        WHERE id = service_record.id;

        -- Wait 1 second between requests to avoid rate limiting
        PERFORM pg_sleep(1);
    END LOOP;
END $$;
```

Or use the edge function directly:

```bash
# Create a script to call edge function for all services
cd apps/healthcare-backend

python3 << 'EOF'
import asyncio
import os
from app.database import create_supabase_client

async def backfill_translations():
    supabase = create_supabase_client()

    # Get services without translations
    result = supabase.schema('healthcare').table('services').select(
        'id,name,description'
    ).eq('active', True).is_('name_ru', None).limit(100).execute()

    services = result.data if result.data else []
    print(f"Found {len(services)} services to translate")

    edge_url = "https://your-project-ref.supabase.co/functions/v1/translate-service"
    anon_key = os.getenv('SUPABASE_ANON_KEY')

    for service in services:
        print(f"Translating: {service['name']}")

        # Call edge function
        import requests
        response = requests.post(
            edge_url,
            headers={
                'Authorization': f'Bearer {anon_key}',
                'Content-Type': 'application/json'
            },
            json={
                'service_id': service['id'],
                'name': service['name'],
                'description': service['description']
            }
        )

        if response.ok:
            print(f"  ✅ Translated")
        else:
            print(f"  ❌ Failed: {response.text}")

        await asyncio.sleep(1)  # Rate limiting

    print(f"✅ Backfill complete")

asyncio.run(backfill_translations())
EOF
```

## Cost Management

### Google Translate API Costs

- **Pricing**: $20 per 1 million characters
- **Typical service**: ~500 characters (name + description × 4 languages)
- **Cost per service**: ~$0.01 (1 cent)
- **Monthly estimate for 1000 new services**: ~$10

### Monitoring Costs

Check usage in Google Cloud Console:
- APIs & Services → Dashboard
- Select Translation API
- View usage metrics

### Cost Optimization

1. **Cache translations**: Translations are stored, not re-generated
2. **Batch updates**: Trigger only updates services without translations
3. **Rate limiting**: Built-in delays in backfill scripts
4. **Disable for bulk imports**: Disable trigger during bulk operations

```sql
-- Disable trigger temporarily
ALTER TABLE healthcare.services DISABLE TRIGGER trigger_translate_service;

-- Bulk import services
-- ... your import ...

-- Re-enable trigger
ALTER TABLE healthcare.services ENABLE TRIGGER trigger_translate_service;
```

## Troubleshooting

### Problem: Translations not appearing

**Check 1**: Verify edge function is called
```bash
supabase functions logs translate-service --tail
# Should see: "Translating service: ..."
```

**Check 2**: Test edge function directly
```bash
curl -X POST https://your-project-ref.supabase.co/functions/v1/translate-service \
  -H 'Authorization: Bearer your-anon-key' \
  -H 'Content-Type: application/json' \
  -d '{
    "service_id": "test-id",
    "name": "Test Service",
    "description": "Test description"
  }'
```

**Check 3**: Verify database configuration
```sql
SHOW app.edge_function_url;
SHOW app.service_role_key;
```

### Problem: Edge function timeout

**Solution**: Increase timeout in Supabase dashboard
- Functions → translate-service → Settings
- Increase timeout to 120 seconds

### Problem: Google Translate API quota exceeded

**Solution**:
1. Check quota in Google Cloud Console
2. Request quota increase if needed
3. Implement retry logic with exponential backoff

### Problem: Invalid API key

**Solution**:
```bash
# Update secret
supabase secrets set GOOGLE_TRANSLATE_API_KEY=new-key

# Redeploy function
supabase functions deploy translate-service --no-verify-jwt
```

## Testing

### Unit Test Edge Function

```bash
# Local testing with Supabase CLI
supabase functions serve translate-service

# Test with curl
curl -X POST http://localhost:54321/functions/v1/translate-service \
  -H 'Authorization: Bearer eyJh...' \
  -d '{"service_id": "test", "name": "Dental Cleaning"}'
```

### Integration Test

```sql
-- Insert test service
INSERT INTO healthcare.services (clinic_id, name, active)
VALUES ('test-clinic', 'Integration Test Service', true)
RETURNING id;

-- Wait 3 seconds

-- Verify translations
SELECT name_ru, name_es, name_pt FROM healthcare.services
WHERE name = 'Integration Test Service';

-- Clean up
DELETE FROM healthcare.services WHERE name = 'Integration Test Service';
```

## Rollback

If needed, remove auto-translation:

```sql
-- Remove trigger
DROP TRIGGER IF EXISTS trigger_translate_service ON healthcare.services;

-- Remove function
DROP FUNCTION IF EXISTS healthcare.trigger_service_translation();
```

Edge function will remain deployed but won't be called.

## Next Steps

1. ✅ Deploy edge function
2. ✅ Configure database
3. ✅ Test with new service
4. ✅ Backfill existing services (optional)
5. ✅ Monitor costs and usage
6. ✅ Deploy backend code changes (if not already done)

After completing these steps, new services will automatically have multi-language support!
