#!/usr/bin/env python3
"""
Check and fix integrations table structure
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


def check_and_fix_table():
    """
    Check the actual structure of integrations table and create records
    that match the existing schema
    """
    
    print("=" * 60)
    print("🔍 Checking Integrations Table Structure")
    print("=" * 60)
    
    # Initialize Supabase
    supabase = create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    )
    
    frontend_org_id = "4e8ddba1-ad52-4613-9a03-ec64636b3f6c"
    
    # First, let's see what columns the table actually has
    print("\n1. Fetching any existing integration to see table structure...")
    try:
        result = supabase.table('integrations').select('*').limit(1).execute()
        if result.data:
            print("  ✅ Found existing record, columns are:")
            for key in result.data[0].keys():
                print(f"     - {key}: {type(result.data[0][key]).__name__}")
            
            # Show sample data
            print("\n  Sample record:")
            print(json.dumps(result.data[0], indent=2, default=str))
        else:
            print("  ℹ️ Table is empty, trying to describe structure...")
    except Exception as e:
        print(f"  ❌ Error: {e}")
    
    # Try different column names based on what might exist
    print("\n2. Attempting to create integrations with correct schema...")
    
    # Based on the error, it seems the table might have different column names
    # Try with alternative column names
    integrations_to_try = [
        {
            # Try with integration_type instead of type
            "organization_id": frontend_org_id,
            "integration_type": "google_calendar",
            "status": "pending",
            "display_name": "Google Calendar",
            "description": "Sync appointments with Google Calendar",
            "is_enabled": True,
            "config": {
                "provider": "google",
                "sync_enabled": True
            }
        },
        {
            # Try with minimal fields
            "organization_id": frontend_org_id,
            "display_name": "Google Calendar",
            "description": "Calendar integration",
            "config": {"provider": "google"}
        },
        {
            # Try with tenant_id instead of organization_id
            "tenant_id": frontend_org_id,
            "integration_type": "google_calendar",
            "display_name": "Google Calendar",
            "config": {"provider": "google"}
        }
    ]
    
    for i, integration in enumerate(integrations_to_try, 1):
        print(f"\n  Attempt {i}: Trying with fields: {list(integration.keys())}")
        try:
            result = supabase.table('integrations').insert(integration).execute()
            if result.data:
                print(f"  ✅ Success! Created integration with ID: {result.data[0]['id']}")
                print(f"  Used schema: {json.dumps(integration, indent=2)}")
                
                # If successful, create more integrations with the same schema
                create_additional_integrations(supabase, integration, frontend_org_id)
                return True
        except Exception as e:
            error_msg = str(e)
            if 'column' in error_msg:
                # Extract the column that doesn't exist
                import re
                match = re.search(r'column integrations\.(\w+) does not exist', error_msg)
                if match:
                    bad_column = match.group(1)
                    print(f"  ❌ Column '{bad_column}' doesn't exist")
                else:
                    print(f"  ❌ Error: {error_msg[:100]}")
            else:
                print(f"  ❌ Error: {error_msg[:100]}")
    
    # If all attempts failed, try to get table info via RPC
    print("\n3. Checking table columns via information_schema...")
    try:
        # This might work to get column information
        query = """
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_schema = 'public' 
        AND table_name = 'integrations'
        ORDER BY ordinal_position;
        """
        # Note: Direct SQL queries might not work via Supabase client
        print("  ℹ️ Cannot query information_schema directly via Supabase client")
        print("  You may need to check the table structure in Supabase Dashboard")
    except:
        pass
    
    print("\n" + "=" * 60)
    print("📋 Manual Steps Required")
    print("=" * 60)
    print("""
1. GO TO SUPABASE DASHBOARD:
   - Navigate to your Supabase project
   - Go to Table Editor
   - Find the 'integrations' table
   - Check the actual column names
   
2. COMMON COLUMN NAME VARIATIONS:
   - 'type' vs 'integration_type'
   - 'organization_id' vs 'tenant_id' vs 'org_id'
   - 'enabled' vs 'is_enabled'
   - 'status' vs 'integration_status'
   
3. CREATE RECORDS MANUALLY:
   Once you know the correct column names, you can:
   - Use Supabase Dashboard to insert records
   - Or update this script with correct column names
   
4. ALTERNATIVE: CREATE NEW TABLE
   If the existing table has wrong schema, create a new one:
   - Run the migration from /migrations/create_integrations_table.sql
   - This will create the table with the expected schema
""")
    
    return False


def create_additional_integrations(supabase, template, org_id):
    """Create additional integrations using the successful schema"""
    print("\n  Creating additional integrations with working schema...")
    
    # Extract the working field names from the template
    org_field = 'organization_id' if 'organization_id' in template else 'tenant_id'
    type_field = 'type' if 'type' in template else 'integration_type'
    enabled_field = 'enabled' if 'enabled' in template else 'is_enabled'
    
    additional = [
        {
            org_field: org_id,
            type_field: "whatsapp",
            "display_name": "WhatsApp Business",
            "description": "Patient communication via WhatsApp",
            "status": "active",
            enabled_field: True,
            "config": {
                "provider": "evolution",
                "instance": "plaintalk-prod"
            }
        },
        {
            org_field: org_id,
            type_field: "email",
            "display_name": "Email Integration",
            "description": "Email communication",
            "status": "pending",
            enabled_field: False,
            "config": {
                "provider": "smtp"
            }
        }
    ]
    
    for integration in additional:
        try:
            result = supabase.table('integrations').insert(integration).execute()
            if result.data:
                print(f"  ✅ Created {integration[type_field]} integration")
        except Exception as e:
            print(f"  ❌ Failed to create {integration[type_field]}: {e}")


if __name__ == "__main__":
    success = check_and_fix_table()
    
    if success:
        print("\n✅ Integrations created successfully!")
        print("Go refresh: https://plaintalk.io/intelligence/integrations")
    else:
        print("\n⚠️ Could not create integrations automatically")
        print("Please check Supabase Dashboard for correct table structure")