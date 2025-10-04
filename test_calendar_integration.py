#!/usr/bin/env python3
"""
Test script for calendar integration OAuth flow
"""

import asyncio
import os
import sys
from dotenv import load_dotenv

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Load environment variables
load_dotenv('../.env')

from app.api.quick_onboarding_rpc import setup_calendar, QuickCalendar

async def test_calendar_oauth():
    """Test the calendar OAuth URL generation"""

    print("=" * 60)
    print("TESTING GOOGLE CALENDAR OAUTH INTEGRATION")
    print("=" * 60)

    # Check environment variables
    client_id = os.environ.get('GOOGLE_CLIENT_ID')
    client_secret = os.environ.get('GOOGLE_CLIENT_SECRET')
    redirect_uri = os.environ.get('GOOGLE_REDIRECT_URI')

    print("\n1. Environment Configuration:")
    print(f"   CLIENT_ID: {'✅ Set' if client_id else '❌ Missing'}")
    print(f"   CLIENT_SECRET: {'✅ Set' if client_secret else '❌ Missing'}")
    print(f"   REDIRECT_URI: {redirect_uri or '❌ Missing'}")

    # Test OAuth URL generation
    print("\n2. Testing OAuth URL Generation:")

    test_clinic_id = "test-clinic-123"
    calendar_data = QuickCalendar(provider="google")

    try:
        result = await setup_calendar(test_clinic_id, calendar_data)

        if result['success']:
            print("   ✅ OAuth URL generated successfully!")
            print(f"\n3. OAuth URL Details:")
            print(f"   Provider: {result['provider']}")
            print(f"   Clinic ID: {result['clinic_id']}")
            print(f"   Instructions: {result['instructions']}")
            print(f"\n4. Generated OAuth URL:")
            print(f"   {result['auth_url'][:100]}...")

            # Parse the URL to check parameters
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(result['auth_url'])
            params = parse_qs(parsed.query)

            print(f"\n5. OAuth Parameters:")
            print(f"   Client ID: {params.get('client_id', ['Not found'])[0][:20]}...")
            print(f"   Redirect URI: {params.get('redirect_uri', ['Not found'])[0]}")
            print(f"   Scopes: {params.get('scope', ['Not found'])[0]}")
            print(f"   Access Type: {params.get('access_type', ['Not found'])[0]}")
            print(f"   State: {'✅ Present' if params.get('state') else '❌ Missing'}")

            print("\n6. Next Steps:")
            print("   1. Deploy this code to production")
            print("   2. Ensure redirect URI is registered in Google Console")
            print("   3. Test the complete flow in browser")

        else:
            print(f"   ❌ Failed to generate OAuth URL: {result.get('error')}")

    except Exception as e:
        print(f"   ❌ Error during testing: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 60)

if __name__ == "__main__":
    asyncio.run(test_calendar_oauth())
