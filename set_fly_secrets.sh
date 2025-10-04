#!/bin/bash

# Set Fly.io secrets for the healthcare-clinic-backend app
# This script reads from .env and sets the necessary secrets

echo "Setting Fly.io secrets for clinic-webhooks..."

# Read the .env file and extract the keys
OPENAI_API_KEY=$(grep "^OPENAI_API_KEY=" .env | cut -d '=' -f2-)
PINECONE_API_KEY=$(grep "^PINECONE_API_KEY=" .env | cut -d '=' -f2-)
SUPABASE_URL=$(grep "^SUPABASE_URL=" .env | cut -d '=' -f2-)
SUPABASE_ANON_KEY=$(grep "^SUPABASE_ANON_KEY=" .env | cut -d '=' -f2-)
SUPABASE_SERVICE_ROLE_KEY=$(grep "^SUPABASE_SERVICE_ROLE_KEY=" .env | cut -d '=' -f2-)
SUPABASE_DB_URL=$(grep "^SUPABASE_DB_URL=" .env | cut -d '=' -f2-)

# Set the secrets
fly secrets set \
  OPENAI_API_KEY="$OPENAI_API_KEY" \
  PINECONE_API_KEY="$PINECONE_API_KEY" \
  SUPABASE_URL="$SUPABASE_URL" \
  SUPABASE_ANON_KEY="$SUPABASE_ANON_KEY" \
  SUPABASE_SERVICE_ROLE_KEY="$SUPABASE_SERVICE_ROLE_KEY" \
  SUPABASE_DB_URL="$SUPABASE_DB_URL" \
  --app healthcare-clinic-backend

echo "Secrets have been set!"
echo "The app will restart automatically to use the new secrets."