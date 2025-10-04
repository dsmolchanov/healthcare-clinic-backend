#!/usr/bin/env python3
"""
Fix integrations table to match frontend's organization ID
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


def fix_integrations_for_frontend():
    """
    Create integrations for the organization ID that frontend is actually using:
    4e8ddba1-ad52-4613-9a03-ec64636b3f6c
    """
    
    print("=" * 60)
    print("🔧 Fixing Integrations for Frontend Organization ID")
    print("=" * 60)
    
    # Initialize Supabase
    supabase = create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    )
    
    # The organization ID that frontend is using
    frontend_org_id = "4e8ddba1-ad52-4613-9a03-ec64636b3f6c"
    print(f"\n📍 Frontend Organization ID: {frontend_org_id}")
    
    # Check for existing integrations
    print("\n1. Checking for existing integrations...")
    try:
        result = supabase.table('integrations').select('*').eq(
            'organization_id', frontend_org_id
        ).execute()
        
        if result.data:
            print(f"  ✅ Found {len(result.data)} existing integrations")
            for integration in result.data:
                print(f"     - {integration.get('type', 'Unknown')}: {integration.get('display_name', 'Unnamed')}")
                print(f"       Status: {integration.get('status', 'unknown')}")
        else:
            print("  ℹ️ No integrations found for this organization")
            print("  Creating integrations...")
    except Exception as e:
        print(f"  ❌ Error querying: {e}")
    
    # Create integrations for the frontend organization
    integrations = [
        {
            "organization_id": frontend_org_id,
            "type": "google_calendar",
            "status": "pending",  # Set to pending so user can connect
            "display_name": "Google Calendar",
            "description": "Sync appointments with Google Calendar",
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
            "organization_id": frontend_org_id,
            "type": "whatsapp",
            "status": "active",
            "display_name": "WhatsApp Business",
            "description": "Send and receive messages via WhatsApp",
            "enabled": True,
            "is_primary": True,
            "config": {
                "provider": "evolution",
                "instance_name": "plaintalk-prod",
                "webhook_url": "https://healthcare-clinic-backend.fly.dev/webhooks/whatsapp",
                "welcome_message": "Welcome! How can we help you today?",
                "business_hours": {
                    "monday": {"start": "09:00", "end": "17:00"},
                    "tuesday": {"start": "09:00", "end": "17:00"},
                    "wednesday": {"start": "09:00", "end": "17:00"},
                    "thursday": {"start": "09:00", "end": "17:00"},
                    "friday": {"start": "09:00", "end": "17:00"}
                }
            },
            "webhook_verified": True,
            "usage_count": 156
        },
        {
            "organization_id": frontend_org_id,
            "type": "email",
            "status": "pending",
            "display_name": "Email Integration",
            "description": "Send and receive emails",
            "enabled": False,
            "is_primary": False,
            "config": {
                "provider": "smtp",
                "smtp_host": "",
                "smtp_port": 587,
                "from_address": ""
            },
            "webhook_verified": False,
            "usage_count": 0
        },
        {
            "organization_id": frontend_org_id,
            "type": "sms",
            "status": "pending",
            "display_name": "SMS Integration",
            "description": "Send and receive SMS messages",
            "enabled": False,
            "is_primary": False,
            "config": {
                "provider": "twilio",
                "phone_number": ""
            },
            "webhook_verified": False,
            "usage_count": 0
        }
    ]
    
    created_count = 0
    for integration in integrations:
        try:
            # Check if this type already exists
            existing = supabase.table('integrations').select('*').eq(
                'organization_id', frontend_org_id
            ).eq('type', integration['type']).execute()
            
            if existing.data:
                print(f"  ⚠️ {integration['type']} already exists, skipping")
                continue
            
            # Create the integration
            result = supabase.table('integrations').insert(integration).execute()
            if result.data:
                created_count += 1
                print(f"  ✅ Created {integration['type']} integration")
                print(f"     ID: {result.data[0]['id']}")
                print(f"     Status: {integration['status']}")
        except Exception as e:
            print(f"  ❌ Failed to create {integration['type']}: {e}")
    
    if created_count > 0:
        print(f"\n✅ Created {created_count} integrations successfully!")
    
    # Now query to verify
    print("\n2. Verifying integrations are now visible...")
    try:
        result = supabase.table('integrations').select('*').eq(
            'organization_id', frontend_org_id
        ).execute()
        
        if result.data:
            print(f"  ✅ Found {len(result.data)} integrations for frontend org")
            for integration in result.data:
                print(f"     - {integration.get('type', 'Unknown')}: {integration.get('display_name', 'Unnamed')}")
                print(f"       Status: {integration.get('status', 'unknown')}")
                print(f"       Enabled: {integration.get('enabled', False)}")
        else:
            print("  ❌ Still no integrations found")
    except Exception as e:
        print(f"  ❌ Error: {e}")
    
    print("\n" + "=" * 60)
    print("🌐 Next Steps")
    print("=" * 60)
    print("""
1. REFRESH THE INTEGRATIONS PAGE:
   https://plaintalk.io/intelligence/integrations
   
2. YOU SHOULD NOW SEE:
   - Google Calendar card (status: pending)
   - WhatsApp card (status: active)
   - Email card (status: pending)
   - SMS card (status: pending)
   
3. TO CONNECT GOOGLE CALENDAR:
   - Click on the Google Calendar card
   - Click the "Connect" or "Test" button
   - Complete OAuth flow in popup window
   - Status should change from "pending" to "active"
   
4. CHECK BROWSER CONSOLE:
   - Press F12 to open Developer Tools
   - Go to Console tab
   - Look for any errors
   - Check Network tab for API calls
   
5. IF STILL EMPTY:
   - Check if you're logged in with the correct organization
   - Try logging out and logging back in
   - Clear browser cache and cookies
""")
    
    print("\n✅ Integration fix complete!")
    print(f"Organization ID: {frontend_org_id}")
    print("Go refresh: https://plaintalk.io/intelligence/integrations")
    print("=" * 60)


if __name__ == "__main__":
    fix_integrations_for_frontend()