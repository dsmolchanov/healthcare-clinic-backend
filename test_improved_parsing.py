#!/usr/bin/env python3
"""Test the improved quick onboarding API locally"""

import asyncio
import os
import sys
from dotenv import load_dotenv

# Add app to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load environment variables
load_dotenv()

from app.api.quick_onboarding_improved import QuickOnboardingService, QuickRegistration

async def test_registration():
    """Test the registration process"""

    # Create service instance
    service = QuickOnboardingService()

    # Test data
    test_clinic = QuickRegistration(
        name="Sunshine Dental Care",
        phone="(555) 123-4567",
        email="info@sunshinedental.com",
        address="789 Oak Street",
        city="San Francisco",
        state="CA",
        zip_code="94102",
        timezone="America/Los_Angeles"
    )

    print(f"Testing registration for: {test_clinic.name}")
    print("-" * 50)

    try:
        result = await service.quick_register(test_clinic)
        print("✅ Registration successful!")
        print(f"Organization ID: {result['organization_id']}")
        print(f"Clinic ID: {result['clinic_id']}")
        print(f"Agent ID: {result['agent_id']}")
        print(f"Login URL: {result['login_url']}")
        print(f"Message: {result['message']}")
        return result
    except Exception as e:
        print(f"❌ Registration failed: {e}")
        import traceback
        traceback.print_exc()
        return None

async def test_parse_website():
    """Test website parsing"""
    from app.api.quick_onboarding_improved import WebsiteParseRequest

    service = QuickOnboardingService()

    # Test with a real website
    request = WebsiteParseRequest(url="https://www.mayoclinic.org/")

    print("\nTesting website parsing...")
    print("-" * 50)

    try:
        result = await service.parse_website(request)
        if result['success']:
            print("✅ Website parsed successfully!")
            print("Extracted data:", result['data'])
        else:
            print(f"❌ Parsing failed: {result['error']}")
        return result
    except Exception as e:
        print(f"❌ Parsing error: {e}")
        return None

async def main():
    """Run all tests"""
    print("=" * 50)
    print("TESTING IMPROVED QUICK ONBOARDING API")
    print("=" * 50)

    # Test registration
    registration_result = await test_registration()

    # Test website parsing
    # await test_parse_website()

    print("\n" + "=" * 50)
    print("TESTS COMPLETE")
    print("=" * 50)

if __name__ == "__main__":
    asyncio.run(main())
