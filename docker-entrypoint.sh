#!/bin/bash
set -e

# Start NocoDB in the background on port 8081
if [ "${ENABLE_NOCODB}" = "true" ]; then
    echo "Starting NocoDB..."
    
    # Create data directory
    mkdir -p /app/nocodb_data
    
    # Configure NocoDB
    export NC_DB="${DATABASE_URL}"
    export NC_PUBLIC_URL="https://healthcare-clinic-backend.fly.dev/nocodb"
    export NC_DISABLE_TELE=true
    export NC_AUTH_JWT_SECRET="${JWT_SECRET:-$(openssl rand -hex 32)}"
    export NC_MIN=true  # Minimal UI
    
    # Start NocoDB on port 8081
    npx nocodb --port 8081 &
    
    echo "NocoDB started on port 8081"
    sleep 5  # Give NocoDB time to start
fi

# Start the main FastAPI application
echo "Starting FastAPI application..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8080