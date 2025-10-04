#!/bin/bash
set -e

echo "Starting multi-service container with FastAPI and NocoDB..."

# Ensure NocoDB data directory exists
mkdir -p /app/nocodb_data

# Set default values if not provided
export JWT_SECRET=${JWT_SECRET:-$(openssl rand -hex 32)}
export NC_AUTH_JWT_SECRET=${NC_AUTH_JWT_SECRET:-$JWT_SECRET}

# Configure NocoDB environment
export NC_DB="${DATABASE_URL}"
export NC_PUBLIC_URL="${NC_PUBLIC_URL:-https://healthcare-clinic-backend.fly.dev/nocodb}"
export NC_DISABLE_TELE=true
export NC_MIN=true

# Wait for database to be ready
echo "Waiting for database connection..."
python -c "
import os
import time
import psycopg2
from urllib.parse import urlparse

db_url = os.environ.get('DATABASE_URL', '')
if db_url:
    result = urlparse(db_url)
    max_retries = 30
    retry = 0
    while retry < max_retries:
        try:
            conn = psycopg2.connect(
                database=result.path[1:],
                user=result.username,
                password=result.password,
                host=result.hostname,
                port=result.port
            )
            conn.close()
            print('Database is ready!')
            break
        except Exception as e:
            retry += 1
            print(f'Waiting for database... attempt {retry}/{max_retries}')
            time.sleep(2)
    else:
        print('Database connection timeout')
"

# Initialize NocoDB if first run
if [ ! -f "/app/nocodb_data/.initialized" ]; then
    echo "First run detected, initializing NocoDB..."
    touch /app/nocodb_data/.initialized
fi

echo "Starting supervisor to manage all services..."
exec "$@"