# Simple NocoDB Deployment Options

Since the integrated deployment is having issues with NocoDB's npm package, here are simpler alternatives:

## Option 1: Deploy NocoDB Separately on Fly.io (Recommended)

This is the simplest and most reliable approach:

```bash
# 1. Create a separate NocoDB app
fly launch --image nocodb/nocodb:latest \
  --name clinic-nocodb \
  --region sea \
  --no-deploy

# 2. Create a volume for data persistence
fly volumes create nocodb_data \
  --app clinic-nocodb \
  --region sea \
  --size 3 \
  --yes

# 3. Set environment variables
fly secrets set \
  NC_DB="$DATABASE_URL" \
  NC_AUTH_JWT_SECRET="$(openssl rand -hex 32)" \
  NC_DISABLE_TELE=true \
  --app clinic-nocodb

# 4. Deploy
fly deploy --app clinic-nocodb

# Your NocoDB will be available at: https://clinic-nocodb.fly.dev
```

## Option 2: Use NocoDB Cloud (Easiest)

1. Go to https://app.nocodb.com
2. Sign up for a free account
3. Create a new workspace
4. Add your database connection:
   - Click "New Base"
   - Choose "Connect to external database"
   - Enter your Supabase/PostgreSQL connection string
5. NocoDB will automatically sync with your existing tables

## Option 3: Local Docker Development

For local testing before deploying:

```bash
# Run NocoDB with your existing database
docker run -d \
  --name nocodb \
  -p 8080:8080 \
  -e NC_DB="$DATABASE_URL" \
  -e NC_AUTH_JWT_SECRET="test-secret" \
  -e NC_DISABLE_TELE=true \
  -v nocodb-data:/usr/app/data \
  nocodb/nocodb:latest

# Access at http://localhost:8080
```

## Option 4: Deploy on Supabase Edge Functions

Since you're already using Supabase, you can use their Edge Functions:

```typescript
// supabase/functions/nocodb-proxy/index.ts
import { serve } from 'https://deno.land/std@0.168.0/http/server.ts'

serve(async (req) => {
  // Proxy requests to NocoDB Cloud API
  const url = new URL(req.url)
  const targetUrl = `https://app.nocodb.com/apps/voice-api/v1${url.pathname}${url.search}`
  
  const response = await fetch(targetUrl, {
    method: req.method,
    headers: {
      ...Object.fromEntries(req.headers),
      'xc-auth': Deno.env.get('NOCODB_API_TOKEN')
    },
    body: req.body
  })
  
  return response
})
```

## Frontend Configuration

Once NocoDB is deployed, update your frontend:

### For Separate Deployment (Option 1):
```typescript
// .env
VITE_NOCODB_URL=https://clinic-nocodb.fly.dev
```

### For NocoDB Cloud (Option 2):
```typescript
// .env
VITE_NOCODB_URL=https://app.nocodb.com
VITE_NOCODB_API_TOKEN=your-api-token
```

### For Local Docker (Option 3):
```typescript
// .env
VITE_NOCODB_URL=http://localhost:8080
```

## Quick Setup Script

Here's a complete script to deploy NocoDB separately:

```bash
#!/bin/bash
# deploy-nocodb-standalone.sh

# Load environment variables
source .env

# Create and deploy NocoDB
echo "Creating NocoDB app..."
fly launch --image nocodb/nocodb:latest \
  --name clinic-nocodb \
  --region sea \
  --no-deploy \
  --yes

# Create volume
echo "Creating persistent volume..."
fly volumes create nocodb_data \
  --app clinic-nocodb \
  --region sea \
  --size 3 \
  --yes

# Configure secrets
echo "Setting up database connection..."
fly secrets set \
  NC_DB="$DATABASE_URL" \
  NC_AUTH_JWT_SECRET="$(openssl rand -hex 32)" \
  NC_DISABLE_TELE=true \
  NC_PUBLIC_URL="https://clinic-nocodb.fly.dev" \
  --app clinic-nocodb

# Deploy
echo "Deploying NocoDB..."
fly deploy --app clinic-nocodb

echo "NocoDB deployed successfully!"
echo "Access at: https://clinic-nocodb.fly.dev"
echo ""
echo "Next steps:"
echo "1. Visit https://clinic-nocodb.fly.dev"
echo "2. Create your admin account"
echo "3. Your database tables will be automatically available"
```

## Connecting to Your Database

NocoDB will automatically detect and display all your existing tables from Supabase:

1. **Patients** - Full CRUD operations
2. **Appointments** - Calendar view available
3. **Doctors** - Staff management
4. **Schedules** - Timeline view
5. **Treatments** - Form view for data entry

## Security Considerations

1. **Row Level Security**: NocoDB respects Supabase RLS policies
2. **API Tokens**: Generate project-specific tokens for API access
3. **Webhooks**: Configure webhooks for real-time sync
4. **Audit Logs**: Enable NocoDB's built-in audit logging

## API Integration

Once deployed, you can use NocoDB's REST API:

```javascript
// Example: Fetch patients
const response = await fetch('https://clinic-nocodb.fly.dev/apps/voice-api/v1/db/data/noco/project/patients', {
  headers: {
    'xc-auth': 'your-api-token'
  }
});
const patients = await response.json();

// Example: Create appointment
const appointment = await fetch('https://clinic-nocodb.fly.dev/apps/voice-api/v1/db/data/noco/project/appointments', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'xc-auth': 'your-api-token'
  },
  body: JSON.stringify({
    patient_id: '123',
    doctor_id: '456',
    date_time: '2025-01-20T10:00:00Z',
    type: 'consultation'
  })
});
```

## Recommended Approach

For production use, we recommend **Option 1** (Separate Fly.io deployment) because:

1. **Isolation**: NocoDB runs independently from your API
2. **Scalability**: Can scale NocoDB separately from your backend
3. **Reliability**: No dependency conflicts with your Python backend
4. **Simplicity**: One command deployment
5. **Cost-effective**: Minimal resource usage on Fly.io free tier

## Troubleshooting

### Database Connection Issues
```bash
# Test connection from NocoDB container
fly ssh console --app clinic-nocodb
nc -zv your-database-host 5432
```

### Permission Errors
```sql
-- Grant necessary permissions
GRANT ALL ON SCHEMA public TO your_db_user;
GRANT ALL ON ALL TABLES IN SCHEMA public TO your_db_user;
```

### View Logs
```bash
fly logs --app clinic-nocodb
```

## Next Steps

1. Choose your deployment option
2. Deploy NocoDB using the provided commands
3. Update frontend environment variables
4. Test the integration

The separate deployment approach is much simpler and more reliable than trying to bundle everything in a single container.