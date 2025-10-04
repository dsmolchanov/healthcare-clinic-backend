#!/usr/bin/env python3
"""
Test the simplified onboarding flow
Run: python test_onboarding.py
"""

import asyncio
import os
from dotenv import load_dotenv
import sys

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load environment variables
load_dotenv('../.env')

async def test_onboarding():
    """Test the complete onboarding flow"""

    print("🚀 Testing Simplified Onboarding Flow\n")

    # 1. Check environment variables
    print("1️⃣ Checking Environment Variables...")

    required_vars = [
        'SUPABASE_URL',
        'SUPABASE_SERVICE_ROLE_KEY',
        'TWILIO_ACCOUNT_SID',
        'TWILIO_AUTH_TOKEN',
        'TWILIO_WHATSAPP_NUMBER',
        'GOOGLE_CLIENT_ID',
        'GOOGLE_CLIENT_SECRET',
        'OPENAI_API_KEY'
    ]

    missing = []
    for var in required_vars:
        value = os.getenv(var)
        if not value or value.startswith('your-'):
            missing.append(var)
            print(f"   ❌ {var}: Missing or not configured")
        else:
            print(f"   ✅ {var}: Configured")

    if missing:
        print(f"\n⚠️  Please configure these variables in /clinics/.env: {', '.join(missing)}")
        if 'GOOGLE_CLIENT_ID' in missing:
            print("\n📝 To get Google OAuth credentials:")
            print("   1. Go to https://console.cloud.google.com")
            print("   2. Create a project and enable Google Calendar API")
            print("   3. Create OAuth 2.0 credentials")
            print("   4. Add the client ID and secret to your .env")
        return

    print("\n2️⃣ Testing Database Connection...")

    try:
        from supabase import create_client
        supabase = create_client(
            os.getenv('SUPABASE_URL'),
            os.getenv('SUPABASE_SERVICE_ROLE_KEY')
        )

        # Test query - Supabase client uses table name with schema prefix
        result = supabase.table('organizations').select('id').limit(1).execute()
        print("   ✅ Database connection successful")
    except Exception as e:
        print(f"   ❌ Database error: {e}")
        return

    print("\n3️⃣ Testing WhatsApp Configuration...")

    # Check if Twilio sandbox is configured
    if os.getenv('TWILIO_WHATSAPP_NUMBER') == '+14155238886':
        print("   ℹ️  Using Twilio Sandbox number")
        print("   📱 To test: Send 'join <your-sandbox-word>' to +14155238886")
    else:
        print(f"   ✅ Using production WhatsApp: {os.getenv('TWILIO_WHATSAPP_NUMBER')}")

    print("\n4️⃣ Testing Google Calendar OAuth...")

    client_id = os.getenv('GOOGLE_CLIENT_ID')
    if client_id and not client_id.startswith('your-'):
        oauth_url = (
            f"https://accounts.google.com/o/oauth2/v2/auth?"
            f"client_id={client_id}&"
            f"redirect_uri={os.getenv('GOOGLE_REDIRECT_URI', 'http://localhost:3000/calendar/callback')}&"
            f"response_type=code&"
            f"scope=https://www.googleapis.com/auth/calendar&"
            f"access_type=offline"
        )
        print(f"   ✅ OAuth URL ready: {oauth_url[:100]}...")

    print("\n5️⃣ Creating Test Clinic...")

    try:
        from app.api.quick_onboarding import QuickOnboardingService, QuickRegistration

        service = QuickOnboardingService()

        # Create test clinic
        test_data = QuickRegistration(
            name="Test Dental Clinic",
            phone="(555) 123-4567",
            email="test@dental.com",
            timezone="America/Los_Angeles"
        )

        result = await service.quick_register(test_data)

        if result['success']:
            print(f"   ✅ Test clinic created!")
            print(f"      Clinic ID: {result['clinic_id']}")
            print(f"      Organization ID: {result['organization_id']}")
            print(f"      Agent ID: {result['agent_id']}")

            # Cleanup
            supabase.schema('healthcare').table('clinics').delete().eq('id', result['clinic_id']).execute()
            supabase.schema('core').table('organizations').delete().eq('id', result['organization_id']).execute()
            print("   🧹 Test data cleaned up")
        else:
            print(f"   ❌ Failed to create test clinic")
    except ImportError as e:
        print(f"   ⚠️  Backend modules not found. Make sure you're in the /clinics/backend directory")
        print(f"      Error: {e}")
    except Exception as e:
        print(f"   ❌ Error: {e}")

    print("\n✨ Onboarding Test Complete!\n")

    print("📋 Next Steps:")
    print("1. Make sure all environment variables are configured")
    print("2. Deploy backend to Fly.io: cd clinics/backend && fly deploy")
    print("3. Run frontend: cd plaintalk/frontend && npm run dev")
    print("4. Visit http://localhost:5173/onboard to test the flow")

if __name__ == "__main__":
    asyncio.run(test_onboarding())
