#!/usr/bin/env python3
"""
Comprehensive test for all RPC endpoints in the clinic system.
Tests the complete onboarding flow with all endpoints.
"""

import os
import json
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any
from supabase import create_client, Client
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize Supabase client
supabase: Client = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_ANON_KEY")
)

def print_test_header(test_name: str):
    """Print a formatted test header"""
    print(f"\n{'='*60}")
    print(f"🧪 Testing: {test_name}")
    print('='*60)

def print_result(success: bool, message: str, details: Any = None):
    """Print test result with formatting"""
    icon = "✅" if success else "❌"
    print(f"{icon} {message}")
    if details:
        print(f"   Details: {json.dumps(details, indent=2)}")

async def test_quick_register_clinic():
    """Test the quick_register_clinic RPC function"""
    print_test_header("quick_register_clinic")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    test_data = {
        "p_name": f"Test Clinic {timestamp}",
        "p_email": f"test_{timestamp}@clinic.com",
        "p_phone": "+1-555-0199",
        "p_address": "123 Test Street",
        "p_city": "Test City",
        "p_state": "CA",
        "p_zip_code": "90210",
        "p_timezone": "America/Los_Angeles"
    }

    try:
        result = supabase.rpc("quick_register_clinic", test_data).execute()
        if result.data:
            # Check if the response contains success flag or clinic_id
            if isinstance(result.data, dict):
                # The RPC returns success and clinic_id when it works
                if result.data.get('success') and result.data.get('clinic_id'):
                    print_result(True, "Successfully registered clinic", {
                        "clinic_id": result.data.get('clinic_id'),
                        "name": result.data.get('clinic_data', {}).get('name')
                    })
                    return result.data.get('clinic_id')
                else:
                    print_result(False, "Registration returned but without success", result.data)
                    return None
            print_result(False, "Unexpected response format", result.data)
            return None
        else:
            print_result(False, "No data returned", None)
            return None
    except Exception as e:
        # Check if exception contains successful response data
        error_str = str(e)
        if "'success': True" in error_str and "'clinic_id':" in error_str:
            # Extract clinic_id from error string using regex or string parsing
            import re
            clinic_id_match = re.search(r"'clinic_id':\s*'([^']+)'", error_str)
            if clinic_id_match:
                clinic_id = clinic_id_match.group(1)
                print_result(True, "Successfully registered clinic (from exception)", {
                    "clinic_id": clinic_id,
                    "note": "Response returned via exception but operation succeeded"
                })
                return clinic_id
        print_result(False, f"Error: {str(e)[:200]}..." if len(str(e)) > 200 else f"Error: {str(e)}")
        return None

async def test_update_clinic(clinic_id: str):
    """Test the update_clinic RPC function"""
    print_test_header("update_clinic")

    if not clinic_id:
        print_result(False, "No clinic_id provided")
        return False

    update_data = {
        "clinic_id": clinic_id,
        "website": "https://testclinic.com",
        "description": "Updated test clinic description",
        "business_hours": {
            "monday": "9:00 AM - 5:00 PM",
            "tuesday": "9:00 AM - 5:00 PM",
            "wednesday": "9:00 AM - 5:00 PM",
            "thursday": "9:00 AM - 5:00 PM",
            "friday": "9:00 AM - 5:00 PM"
        }
    }

    try:
        result = supabase.rpc("update_clinic", update_data).execute()
        if result.data:
            print_result(True, "Successfully updated clinic", result.data)
            return True
        else:
            print_result(False, "Failed to update clinic", result.data)
            return False
    except Exception as e:
        print_result(False, f"Error: {str(e)}")
        return False

async def test_setup_whatsapp(clinic_id: str):
    """Test the setup_whatsapp RPC function"""
    print_test_header("setup_whatsapp")

    if not clinic_id:
        print_result(False, "No clinic_id provided")
        return False

    whatsapp_data = {
        "clinic_id": clinic_id,
        "phone_number": "+1234567890",
        "business_name": "Test Clinic WhatsApp",
        "webhook_url": f"https://healthcare-clinic-backend.fly.dev/webhooks/whatsapp/{clinic_id}"
    }

    try:
        result = supabase.rpc("setup_whatsapp", whatsapp_data).execute()
        if result.data:
            print_result(True, "Successfully setup WhatsApp", result.data)
            return True
        else:
            print_result(False, "Failed to setup WhatsApp", result.data)
            return False
    except Exception as e:
        print_result(False, f"Error: {str(e)}")
        return False

async def test_setup_calendar(clinic_id: str):
    """Test the setup_calendar RPC function"""
    print_test_header("setup_calendar")

    if not clinic_id:
        print_result(False, "No clinic_id provided")
        return False

    calendar_data = {
        "clinic_id": clinic_id,
        "provider": "google",
        "calendar_name": "Test Clinic Calendar"
    }

    try:
        result = supabase.rpc("setup_calendar", calendar_data).execute()
        if result.data:
            print_result(True, "Successfully setup calendar", result.data)
            return True
        else:
            print_result(False, "Failed to setup calendar", result.data)
            return False
    except Exception as e:
        print_result(False, f"Error: {str(e)}")
        return False

async def test_activate_clinic(clinic_id: str):
    """Test the activate_clinic RPC function"""
    print_test_header("activate_clinic")

    if not clinic_id:
        print_result(False, "No clinic_id provided")
        return False

    try:
        result = supabase.rpc("activate_clinic", {"clinic_id": clinic_id}).execute()
        if result.data:
            print_result(True, "Successfully activated clinic", result.data)
            return True
        else:
            print_result(False, "Failed to activate clinic", result.data)
            return False
    except Exception as e:
        print_result(False, f"Error: {str(e)}")
        return False

async def test_list_clinics():
    """Test the list_clinics RPC function"""
    print_test_header("list_clinics")

    try:
        result = supabase.rpc("list_clinics").execute()
        if result.data:
            # Handle wrapped response format
            if isinstance(result.data, dict) and 'data' in result.data:
                clinics = result.data.get('data', [])
                clinic_count = len(clinics) if isinstance(clinics, list) else 0
                print_result(True, f"Found {clinic_count} clinics")
                # Show first 3 clinics
                if isinstance(clinics, list) and clinic_count > 0:
                    for i, clinic in enumerate(clinics[:min(3, clinic_count)], 1):
                        if isinstance(clinic, dict):
                            print(f"   {i}. {clinic.get('name', 'Unknown')} - {clinic.get('city', '')}, {clinic.get('state', '')}")
                return True
            # Handle unwrapped response format
            elif isinstance(result.data, list):
                clinic_count = len(result.data)
                print_result(True, f"Found {clinic_count} clinics")
                # Show first 3 clinics
                if clinic_count > 0:
                    for i, clinic in enumerate(result.data[:min(3, clinic_count)], 1):
                        if isinstance(clinic, dict):
                            print(f"   {i}. {clinic.get('name', 'Unknown')} - {clinic.get('city', '')}, {clinic.get('state', '')}")
                return True
            else:
                print_result(False, f"Unexpected response format: {type(result.data)}")
                return False
        else:
            print_result(False, "No clinics found")
            return False
    except Exception as e:
        print_result(False, f"Error: {str(e)}")
        return False

async def test_get_clinic_details(clinic_id: str):
    """Test the get_clinic_details RPC function"""
    print_test_header("get_clinic_details")

    if not clinic_id:
        print_result(False, "No clinic_id provided")
        return False

    try:
        result = supabase.rpc("get_clinic_details", {"clinic_id": clinic_id}).execute()
        if result.data:
            print_result(True, "Successfully retrieved clinic details", {
                "name": result.data.get('name'),
                "status": result.data.get('status'),
                "email": result.data.get('email')
            })
            return True
        else:
            print_result(False, "Failed to get clinic details", result.data)
            return False
    except Exception as e:
        print_result(False, f"Error: {str(e)}")
        return False

async def main():
    """Run all tests"""
    print("\n" + "="*60)
    print("🏥 COMPREHENSIVE RPC ENDPOINT TEST SUITE")
    print("="*60)

    # Track results
    results = {
        "passed": 0,
        "failed": 0
    }

    # Test 1: Register a new clinic
    clinic_id = await test_quick_register_clinic()
    if clinic_id:
        results["passed"] += 1
    else:
        results["failed"] += 1

    # Only proceed with other tests if we have a clinic_id
    if clinic_id:
        # Test 2: Update clinic
        if await test_update_clinic(clinic_id):
            results["passed"] += 1
        else:
            results["failed"] += 1

        # Test 3: Setup WhatsApp
        if await test_setup_whatsapp(clinic_id):
            results["passed"] += 1
        else:
            results["failed"] += 1

        # Test 4: Setup Calendar
        if await test_setup_calendar(clinic_id):
            results["passed"] += 1
        else:
            results["failed"] += 1

        # Test 5: Get clinic details
        if await test_get_clinic_details(clinic_id):
            results["passed"] += 1
        else:
            results["failed"] += 1

        # Test 6: Activate clinic
        if await test_activate_clinic(clinic_id):
            results["passed"] += 1
        else:
            results["failed"] += 1

    # Test 7: List all clinics
    if await test_list_clinics():
        results["passed"] += 1
    else:
        results["failed"] += 1

    # Print summary
    print("\n" + "="*60)
    print("📊 TEST SUMMARY")
    print("="*60)
    print(f"✅ Passed: {results['passed']}")
    print(f"❌ Failed: {results['failed']}")
    print(f"📈 Success Rate: {results['passed']/(results['passed']+results['failed'])*100:.1f}%")
    print("="*60)

    return results["failed"] == 0

if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)
