#!/usr/bin/env python3
"""
Test Google Calendar Integration Frontend Flow
Simulates the frontend integration flow for testing
"""

import os
import sys
import json
import asyncio
from pathlib import Path
from datetime import datetime, timezone
import requests

# Load environment
from dotenv import load_dotenv
env_path = Path(__file__).parent / '../.env'
load_dotenv(env_path)

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))

from supabase import create_client
from supabase.client import ClientOptions


def test_integration_api():
    """Test the integration API endpoints that the frontend uses"""

    print("=" * 60)
    print("🧪 Testing Google Calendar Integration Frontend Flow")
    print("=" * 60)

    # Shtern clinic details
    organization_id = "e0c84f56-235d-49f2-9a44-37c1be579afc"  # Shtern clinic ID

    # Backend URL
    BACKEND_URL = "https://healthcare-clinic-backend.fly.dev"
    LOCAL_BACKEND = "http://localhost:8000"

    # Use local if available, otherwise production
    backend_url = LOCAL_BACKEND

    print(f"\n📡 Testing against: {backend_url}")
    print(f"   Organization: Shtern Dental Clinic")
    print(f"   Organization ID: {organization_id}")

    # Test 1: Check if integrations endpoint works
    print("\n1. Testing GET /api/integrations endpoint...")
    try:
        response = requests.get(
            f"{backend_url}/api/integrations",
            params={"organization_id": organization_id}
        )

        if response.status_code == 200:
            integrations = response.json()
            print(f"  ✅ Found {len(integrations)} integrations")
            for integration in integrations:
                print(f"     - {integration.get('type', 'Unknown')}: {integration.get('display_name', 'Unnamed')}")
                print(f"       Status: {integration.get('status', 'unknown')}")
        elif response.status_code == 404:
            print(f"  ⚠️ Endpoint not found - this might be why the UI is empty")
        else:
            print(f"  ❌ Error {response.status_code}: {response.text[:200]}")
    except Exception as e:
        print(f"  ❌ Failed to connect: {e}")

    # Test 2: Check quick-setup endpoint for OAuth URL
    print("\n2. Testing Calendar Quick Setup endpoint...")
    try:
        response = requests.post(
            f"{backend_url}/api/onboarding/{organization_id}/calendar/quick-setup",
            json={"provider": "google"}
        )

        if response.status_code == 200:
            data = response.json()
            if data.get('auth_url'):
                print(f"  ✅ OAuth URL generated successfully")
                print(f"     URL (first 100 chars): {data['auth_url'][:100]}...")
            else:
                print(f"  ⚠️ Response missing auth_url: {data}")
        else:
            print(f"  ❌ Error {response.status_code}: {response.text[:200]}")
    except Exception as e:
        print(f"  ❌ Failed to connect: {e}")

    # Test 3: Check calendar status endpoint
    print("\n3. Testing Calendar Status endpoint...")
    try:
        response = requests.get(
            f"{backend_url}/api/onboarding/{organization_id}/calendar/status"
        )

        if response.status_code == 200:
            status = response.json()
            print(f"  ✅ Status endpoint works")
            print(f"     Connected: {status.get('connected', False)}")
            print(f"     Provider: {status.get('provider', 'none')}")
        else:
            print(f"  ❌ Error {response.status_code}: {response.text[:200]}")
    except Exception as e:
        print(f"  ❌ Failed to connect: {e}")

    # Test 4: Check if we can create a test integration
    print("\n4. Testing Integration Creation...")
    test_integration = {
        "organizationId": organization_id,
        "type": "google_calendar",
        "provider": "google",
        "config": {
            "display_name": "Test Google Calendar",
            "description": "Test integration for Shtern clinic",
            "provider": "google",
            "sync_enabled": True,
            "sync_direction": "bidirectional"
        },
        "enabled": True
    }

    try:
        response = requests.post(
            f"{backend_url}/api/integrations",
            json=test_integration
        )

        if response.status_code in [200, 201]:
            integration = response.json()
            print(f"  ✅ Integration created successfully")
            print(f"     ID: {integration.get('id')}")
            print(f"     Type: {integration.get('type')}")
            return integration.get('id')
        else:
            print(f"  ❌ Error {response.status_code}: {response.text[:200]}")
    except Exception as e:
        print(f"  ❌ Failed to create integration: {e}")

    return None


def create_manual_integration_in_db():
    """Manually create an integration in the database for testing"""

    print("\n" + "=" * 60)
    print("🔧 Creating Manual Integration in Database")
    print("=" * 60)

    # Initialize Supabase
    supabase = create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    )

    # Create a test integration
    test_integration = {
        "organization_id": "e0c84f56-235d-49f2-9a44-37c1be579afc",  # Shtern clinic
        "type": "google_calendar",
        "status": "active",
        "display_name": "Google Calendar - Shtern Dental",
        "description": "Calendar sync for appointment scheduling",
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
    }

    try:
        # Check if integrations table exists
        result = supabase.table('integrations').insert(test_integration).execute()
        if result.data:
            print(f"  ✅ Integration created in database")
            print(f"     ID: {result.data[0]['id']}")
            return result.data[0]['id']
    except Exception as e:
        print(f"  ❌ Failed to create integration: {e}")

    # Try alternative table names
    print("\n  🔍 Checking alternative table names...")
    tables_to_try = [
        'tenant_integrations',
        'organization_integrations',
        'clinic_integrations',
        'calendar_integrations'
    ]

    for table_name in tables_to_try:
        try:
            result = supabase.table(table_name).select('*').limit(1).execute()
            print(f"  ✅ Found table: {table_name}")
        except:
            print(f"  ❌ Table not found: {table_name}")

    return None


def print_manual_test_instructions():
    """Print instructions for manual testing"""

    print("\n" + "=" * 60)
    print("📝 Manual Testing Instructions")
    print("=" * 60)

    print("""
1. FRONTEND TESTING:
   a. Go to: https://plaintalk.io/intelligence/integrations
   b. Check if any integrations appear
   c. Click "Add Integration" button
   d. Select "Google Calendar"
   e. Fill in the form and submit
   f. Complete OAuth flow in popup window

2. DIRECT API TESTING (using curl):

   # Get existing integrations
   curl https://healthcare-clinic-backend.fly.dev/api/integrations?organization_id=e0c84f56-235d-49f2-9a44-37c1be579afc

   # Create new Google Calendar integration
   curl -X POST https://healthcare-clinic-backend.fly.dev/api/integrations \\
     -H "Content-Type: application/json" \\
     -d '{
       "organizationId": "e0c84f56-235d-49f2-9a44-37c1be579afc",
       "type": "google_calendar",
       "provider": "google",
       "config": {
         "display_name": "Google Calendar Test",
         "sync_enabled": true
       },
       "enabled": true
     }'

   # Get OAuth URL
   curl -X POST https://healthcare-clinic-backend.fly.dev/api/onboarding/e0c84f56-235d-49f2-9a44-37c1be579afc/calendar/quick-setup \\
     -H "Content-Type: application/json" \\
     -d '{"provider": "google"}'

3. DATABASE TESTING (using Supabase Dashboard):
   a. Go to your Supabase dashboard
   b. Check the 'integrations' table
   c. Manually insert a test integration record
   d. Refresh the frontend to see if it appears

4. OAUTH FLOW TESTING:
   Use the OAuth URL we generated earlier:
   - Open: shtern_oauth_url_final.txt
   - Copy the URL and open in browser
   - Complete Google authorization
   - Check if tokens are stored in database
""")

    print("\n" + "=" * 60)
    print("🔍 Debugging Tips")
    print("=" * 60)
    print("""
If the integrations page is empty:

1. Check browser console for errors (F12 > Console)
2. Check Network tab for failed API calls
3. Verify organization_id is being passed correctly
4. Check if authentication token is valid
5. Verify backend endpoints are accessible
6. Check if the integrations table exists in database
7. Ensure user has permissions to view integrations

Common issues:
- Missing CORS headers
- Invalid organization ID
- Missing authentication
- Database table doesn't exist
- API endpoint not implemented
""")


if __name__ == "__main__":
    print("\n🚀 Starting Integration Test Suite\n")

    # Test API endpoints
    integration_id = test_integration_api()

    # Try to create in database
    if not integration_id:
        integration_id = create_manual_integration_in_db()

    # Print manual testing instructions
    print_manual_test_instructions()

    print("\n✅ Test suite complete!")
    print("\nNext steps:")
    print("1. Check if integrations appear at: https://plaintalk.io/intelligence/integrations")
    print("2. If not, check browser console for errors")
    print("3. Try the curl commands above to test API directly")
    print("=" * 60)