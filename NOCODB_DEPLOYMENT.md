# NocoDB Deployment on Fly.io

This guide explains how to deploy NocoDB alongside the FastAPI backend on the same Fly.io machine.

## Architecture

The deployment runs three services on a single Fly.io machine:

1. **Nginx** (Port 8080) - Reverse proxy that routes requests
2. **FastAPI** (Port 8001) - Main backend API
3. **NocoDB** (Port 8081) - Database management interface

All services are managed by Supervisor for process control.

## URL Structure

After deployment, your services will be available at:

- **FastAPI API**: `https://clinic-webhooks-nocodb.fly.dev/`
- **NocoDB Interface**: `https://clinic-webhooks-nocodb.fly.dev/nocodb`
- **Health Check**: `https://clinic-webhooks-nocodb.fly.dev/health`

## Deployment Steps

### 1. Prepare Environment

Make sure you have a `.env` file with all required secrets:

```bash
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_ANON_KEY=xxx
DATABASE_URL=postgresql://user:pass@host/db
TWILIO_ACCOUNT_SID=xxx
TWILIO_AUTH_TOKEN=xxx
OPENAI_API_KEY=sk-xxx
```

### 2. Deploy to Fly.io

Run the deployment script:

```bash
cd clinics/backend
./deploy-with-nocodb.sh
```

This script will:
- Create a new Fly.io app (or use existing)
- Create a persistent volume for NocoDB data
- Set all secrets from your `.env` file
- Deploy the multi-service container
- Show you the URLs for accessing both services

### 3. Configure NocoDB

After deployment, visit `https://clinic-webhooks-nocodb.fly.dev/nocodb` and:

1. **First-time setup**:
   - Create an admin account
   - Set up your first project

2. **Connect to existing database**:
   - Click "New Project"
   - Choose "Connect to existing database"
   - Use your `DATABASE_URL` connection string
   - NocoDB will automatically detect all tables

3. **Create views for frontend**:
   - For each table (patients, appointments, etc.)
   - Create a Grid or Gallery view
   - Set appropriate filters and permissions
   - Generate a shared link if needed

## Routing Configuration

The Nginx configuration routes requests as follows:

- `/nocodb/*` → NocoDB (port 8081)
- `/health` → FastAPI health check
- `/*` → FastAPI (port 8001)

## Persistent Storage

NocoDB data is stored in a persistent Fly.io volume mounted at `/app/nocodb_data`. This ensures:
- User accounts persist across deployments
- Project configurations are maintained
- Custom views and settings are preserved

## Monitoring

View logs for all services:

```bash
fly logs --app clinic-webhooks-nocodb
```

Check service status:

```bash
fly status --app clinic-webhooks-nocodb
```

SSH into the container:

```bash
fly ssh console --app clinic-webhooks-nocodb
```

## Scaling

To increase resources:

```bash
# Scale up the VM
fly scale vm shared-cpu-2x --app clinic-webhooks-nocodb

# Add more memory
fly scale memory 2048 --app clinic-webhooks-nocodb
```

## Troubleshooting

### NocoDB not loading

1. Check if all services are running:
   ```bash
   fly ssh console --app clinic-webhooks-nocodb
   supervisorctl status
   ```

2. Restart NocoDB service:
   ```bash
   fly ssh console --app clinic-webhooks-nocodb
   supervisorctl restart nocodb
   ```

### Database connection issues

1. Verify DATABASE_URL is set:
   ```bash
   fly secrets list --app clinic-webhooks-nocodb
   ```

2. Test database connection:
   ```bash
   fly ssh console --app clinic-webhooks-nocodb
   python -c "import psycopg2; psycopg2.connect('$DATABASE_URL')"
   ```

### Permission errors

Ensure the database user has appropriate permissions:

```sql
GRANT ALL PRIVILEGES ON SCHEMA public TO your_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO your_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO your_user;
```

## Security Considerations

1. **JWT Secret**: Automatically generated if not provided
2. **Database Access**: NocoDB uses read-only views where possible
3. **Authentication**: NocoDB has its own authentication system
4. **HTTPS**: All traffic is encrypted via Fly.io's automatic TLS

## Frontend Integration

Update your frontend environment variables:

```bash
VITE_NOCODB_URL=https://clinic-webhooks-nocodb.fly.dev/nocodb
```

The ResourceDashboardNative component will automatically use this URL for embedding NocoDB views.

## Backup and Recovery

### Backup NocoDB configuration

```bash
# Create a backup
fly ssh console --app clinic-webhooks-nocodb
tar -czf /tmp/nocodb_backup.tar.gz /app/nocodb_data
fly ssh sftp get /tmp/nocodb_backup.tar.gz ./nocodb_backup.tar.gz
```

### Restore from backup

```bash
# Upload and restore
fly ssh sftp put ./nocodb_backup.tar.gz /tmp/nocodb_backup.tar.gz
fly ssh console --app clinic-webhooks-nocodb
tar -xzf /tmp/nocodb_backup.tar.gz -C /
supervisorctl restart nocodb
```

## Alternative Deployment Options

If you prefer to run NocoDB separately:

### Option 1: Dedicated NocoDB instance

```bash
fly launch --image nocodb/nocodb:latest --name clinic-nocodb
fly secrets set NC_DB=$DATABASE_URL --app clinic-nocodb
```

### Option 2: Local development

```bash
docker run -d \
  --name nocodb \
  -p 8080:8080 \
  -e NC_DB=$DATABASE_URL \
  nocodb/nocodb:latest
```

### Option 3: NocoDB Cloud

Use the managed service at https://app.nocodb.com for zero maintenance.

## Support

For issues with:
- **NocoDB**: Check https://docs.nocodb.com
- **Fly.io**: Visit https://community.fly.io
- **This deployment**: Create an issue in the repository