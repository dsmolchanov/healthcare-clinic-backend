#!/usr/bin/env python3
"""
Verify and fix the save_evolution_integration RPC function
"""
import os
from supabase import create_client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

# The corrected SQL
FIXED_FUNCTION_SQL = """
DROP FUNCTION IF EXISTS save_evolution_integration(UUID, TEXT, TEXT, TEXT);

CREATE OR REPLACE FUNCTION save_evolution_integration(
    p_organization_id UUID,
    p_instance_name TEXT,
    p_phone_number TEXT DEFAULT NULL,
    p_webhook_url TEXT DEFAULT NULL
)
RETURNS JSON
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    existing_integration healthcare.integrations;
    result_integration healthcare.integrations;
    integration_config JSONB;
    generated_webhook_token TEXT;
BEGIN
    -- Generate webhook token using secure random bytes
    generated_webhook_token := encode(gen_random_bytes(24), 'base64');
    -- Make it URL-safe
    generated_webhook_token := replace(generated_webhook_token, '/', '_');
    generated_webhook_token := replace(generated_webhook_token, '+', '-');
    generated_webhook_token := rtrim(generated_webhook_token, '=');

    -- Build config JSON
    integration_config = jsonb_build_object(
        'instance_name', p_instance_name
    );

    -- Add phone_number if provided
    IF p_phone_number IS NOT NULL THEN
        integration_config = integration_config || jsonb_build_object('phone_number', p_phone_number);
    END IF;

    -- Add webhook_url if provided
    IF p_webhook_url IS NOT NULL THEN
        integration_config = integration_config || jsonb_build_object('webhook_url', p_webhook_url);
    END IF;

    -- Check if integration already exists
    SELECT * INTO existing_integration
    FROM healthcare.integrations
    WHERE organization_id = p_organization_id
    AND type = 'whatsapp'
    AND provider = 'evolution'
    LIMIT 1;

    IF existing_integration.id IS NULL THEN
        -- Insert new integration WITH webhook_token
        INSERT INTO healthcare.integrations (
            organization_id,
            type,
            provider,
            status,
            config,
            enabled,
            webhook_token
        ) VALUES (
            p_organization_id,
            'whatsapp',
            'evolution',
            CASE WHEN p_phone_number IS NOT NULL THEN 'active' ELSE 'pending' END,
            integration_config,
            true,
            generated_webhook_token
        ) RETURNING * INTO result_integration;
    ELSE
        -- Update existing integration (keep existing webhook_token if it exists)
        UPDATE healthcare.integrations
        SET
            status = CASE WHEN p_phone_number IS NOT NULL THEN 'active' ELSE status END,
            config = config || integration_config,
            updated_at = NOW(),
            webhook_token = COALESCE(webhook_token, generated_webhook_token)
        WHERE id = existing_integration.id
        RETURNING * INTO result_integration;
    END IF;

    RETURN row_to_json(result_integration);
END;
$$;

GRANT EXECUTE ON FUNCTION save_evolution_integration(UUID, TEXT, TEXT, TEXT) TO authenticated;
GRANT EXECUTE ON FUNCTION save_evolution_integration(UUID, TEXT, TEXT, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION save_evolution_integration(UUID, TEXT, TEXT, TEXT) TO anon;
"""

def main():
    print("🔍 Checking current RPC function...\n")

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    # Execute the SQL directly via RPC
    try:
        print("📝 Applying fixed function SQL...")
        result = supabase.rpc('exec_sql', {'sql': FIXED_FUNCTION_SQL}).execute()
        print("✅ Function updated successfully!")
        print(f"Result: {result}")
    except Exception as e:
        print(f"⚠️  RPC exec_sql not available, trying direct SQL execution via PostgREST...")

        # Alternative: Use raw SQL execution
        import psycopg2
        from urllib.parse import urlparse

        # Parse Supabase URL to get connection details
        # You'll need to use the direct database connection
        print("\n💡 Please run this SQL manually in Supabase SQL Editor:")
        print("=" * 60)
        print(FIXED_FUNCTION_SQL)
        print("=" * 60)

if __name__ == "__main__":
    main()
