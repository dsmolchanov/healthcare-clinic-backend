#!/usr/bin/env python3
"""
Fix integrations table for frontend UI
"""

import os
import sys
from pathlib import Path
from datetime import datetime
import json

# Load environment
from dotenv import load_dotenv
env_path = Path(__file__).parent / '../.env'
load_dotenv(env_path)

from supabase import create_client


def fix_integrations_table():
    """Check and fix the integrations table"""

    print("=" * 60)
    print("🔧 Fixing Integrations Table")
    print("=" * 60)

    # Initialize Supabase
    supabase = create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    )

    # Try to query the integrations table
    print("\n1. Checking if integrations table exists...")
    try:
        result = supabase.table('integrations').select('*').limit(1).execute()
        print(f"  ✅ Table exists with {len(result.data)} records")

        if result.data:
            print("  Sample record:")
            print(json.dumps(result.data[0], indent=2, default=str))
    except Exception as e:
        error_msg = str(e)
        if 'relation "public.integrations" does not exist' in error_msg:
            print(f"  ❌ Table doesn't exist - need to create it")
            return create_integrations_table(supabase)
        else:
            print(f"  ❌ Error querying table: {e}")

    # Check for existing integrations for Shtern clinic
    print("\n2. Checking for Shtern Dental Clinic integrations...")
    try:
        # Try with organization_id
        result = supabase.table('integrations').select('*').eq(
            'organization_id', 'e0c84f56-235d-49f2-9a44-37c1be579afc'
        ).execute()

        if result.data:
            print(f"  ✅ Found {len(result.data)} integrations for Shtern clinic")
            for integration in result.data:
                print(f"     - {integration.get('type', 'Unknown')}: {integration.get('display_name', 'Unnamed')}")
                print(f"       Status: {integration.get('status', 'unknown')}")
                print(f"       Enabled: {integration.get('enabled', False)}")
        else:
            print("  ℹ️ No integrations found for Shtern clinic")
            print("  Creating sample integrations...")
            create_sample_integrations(supabase)
    except Exception as e:
        print(f"  ❌ Error: {e}")

    # Check the table structure
    print("\n3. Checking table structure...")
    try:
        # Get one record to see the columns
        result = supabase.table('integrations').select('*').limit(1).execute()
        if result.data:
            columns = list(result.data[0].keys())
            print(f"  ✅ Table columns: {', '.join(columns)}")
        else:
            print("  ℹ️ Table is empty, creating sample data...")
            create_sample_integrations(supabase)
    except Exception as e:
        print(f"  ❌ Error: {e}")


def create_integrations_table(supabase):
    """Create the integrations table using RPC or direct SQL"""
    print("\n🔨 Creating integrations table...")

    # Since we can't run CREATE TABLE directly via Supabase client,
    # we'll document what needs to be done
    print("""
    The integrations table needs to be created in Supabase Dashboard:

    1. Go to: https://supabase.com/dashboard
    2. Select your project
    3. Go to SQL Editor
    4. Run this SQL:

    CREATE TABLE IF NOT EXISTS public.integrations (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        organization_id UUID,
        type TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        display_name TEXT NOT NULL,
        description TEXT,
        enabled BOOLEAN DEFAULT true,
        is_primary BOOLEAN DEFAULT false,
        config JSONB DEFAULT '{}',
        credentials JSONB DEFAULT '{}',
        webhook_url TEXT,
        webhook_verified BOOLEAN DEFAULT false,
        usage_count INTEGER DEFAULT 0,
        usage_limit INTEGER,
        last_used_at TIMESTAMPTZ,
        last_error TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    );
    """)


def create_sample_integrations(supabase):
    """Create sample integrations for Shtern clinic"""

    integrations = [
        {
            "organization_id": "e0c84f56-235d-49f2-9a44-37c1be579afc",
            "type": "google_calendar",
            "status": "active",
            "display_name": "Google Calendar - Shtern Dental",
            "description": "Calendar synchronization for appointment scheduling",
            "enabled": True,
            "is_primary": True,
            "config": {
                "provider": "google",
                "calendar_name": "Primary Calendar",
                "sync_enabled": True,
                "sync_direction": "bidirectional",
                "buffer_time_minutes": 15,
                "working_hours": {
                    "monday": {"start": "09:00", "end": "17:00"},
                    "tuesday": {"start": "09:00", "end": "17:00"},
                    "wednesday": {"start": "09:00", "end": "17:00"},
                    "thursday": {"start": "09:00", "end": "17:00"},
                    "friday": {"start": "09:00", "end": "17:00"}
                }
            },
            "webhook_verified": False,
            "usage_count": 0
        },
        {
            "organization_id": "e0c84f56-235d-49f2-9a44-37c1be579afc",
            "type": "whatsapp",
            "status": "active",
            "display_name": "WhatsApp Business - Shtern Dental",
            "description": "WhatsApp messaging for patient communication",
            "enabled": True,
            "is_primary": True,
            "config": {
                "provider": "evolution",
                "instance_name": "shtern-dental",
                "webhook_url": "https://healthcare-clinic-backend.fly.dev/webhooks/whatsapp",
                "welcome_message": "Welcome to Shtern Dental Clinic! How can we help you today?",
                "business_hours": {
                    "monday": {"start": "09:00", "end": "17:00"},
                    "tuesday": {"start": "09:00", "end": "17:00"},
                    "wednesday": {"start": "09:00", "end": "17:00"},
                    "thursday": {"start": "09:00", "end": "17:00"},
                    "friday": {"start": "09:00", "end": "17:00"}
                }
            },
            "webhook_verified": True,
            "usage_count": 42
        }
    ]

    for integration in integrations:
        try:
            result = supabase.table('integrations').insert(integration).execute()
            if result.data:
                print(f"  ✅ Created {integration['type']} integration")
                print(f"     ID: {result.data[0]['id']}")
        except Exception as e:
            print(f"  ❌ Failed to create {integration['type']}: {e}")


def print_frontend_urls():
    """Print URLs for testing the frontend"""

    print("\n" + "=" * 60)
    print("🌐 Frontend Testing URLs")
    print("=" * 60)

    print("""
1. INTEGRATION PAGE (Should now show the cards):
   https://plaintalk.io/intelligence/integrations

2. DIRECT INTEGRATION URLS:
   - WhatsApp Setup: https://plaintalk.io/integrations/whatsapp/new/setup
   - Calendar Settings: https://plaintalk.io/settings/calendar/e0c84f56-235d-49f2-9a44-37c1be579afc

3. TESTING THE OAUTH FLOW:
   a. Go to integrations page
   b. Click on Google Calendar card
   c. Click "Test" or "Connect" button
   d. Complete OAuth in popup window

4. CHECK BROWSER CONSOLE:
   - Press F12 to open Developer Tools
   - Go to Console tab
   - Look for any errors when loading the page
   - Check Network tab for failed API calls

5. MANUAL OAUTH URL:
   If the button doesn't work, use the OAuth URL from:
   - File: shtern_oauth_url_final.txt
   - Open URL directly in browser
   - Complete authorization
   - Return to integrations page to see updated status
""")


if __name__ == "__main__":
    print("\n🚀 Starting Integrations Table Fix\n")

    # Fix the table
    fix_integrations_table()

    # Print testing instructions
    print_frontend_urls()

    print("\n✅ Setup complete!")
    print("\n🔄 Next steps:")
    print("1. Refresh: https://plaintalk.io/intelligence/integrations")
    print("2. You should now see integration cards")
    print("3. Click on Google Calendar to test/connect")
    print("=" * 60)