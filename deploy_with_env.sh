#!/bin/bash

# Deploy with environment variables as build arguments
# This is a workaround for Fly.io secrets authentication issue

echo "Deploying healthcare-clinic-backend with environment variables..."

# Read environment variables from .env file
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
else
    echo "Error: .env file not found"
    exit 1
fi

# Check if required variables are set
if [ -z "$OPENAI_API_KEY" ] || [ -z "$PINECONE_API_KEY" ]; then
    echo "Error: Required environment variables not found in .env"
    echo "Please ensure OPENAI_API_KEY and PINECONE_API_KEY are set"
    exit 1
fi

# Deploy with build arguments
echo "Starting deployment to Fly.io..."
fly deploy \
    --build-arg OPENAI_API_KEY="$OPENAI_API_KEY" \
    --build-arg PINECONE_API_KEY="$PINECONE_API_KEY" \
    --app healthcare-clinic-backend \
    --region iad

echo "Deployment initiated. Check logs with: fly logs --app healthcare-clinic-backend"