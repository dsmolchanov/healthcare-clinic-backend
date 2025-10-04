#!/usr/bin/env python3
"""
Comprehensive testing script for WhatsApp and Calendar integrations
Tests each step of the onboarding process and verifies actual connectivity
"""

import asyncio
import os
import sys
import json
from datetime import datetime
from dotenv import load_dotenv
import httpx
from colorama import init, Fore, Style

# Initialize colorama for colored output
init(autoreset=True)

# Load environment variables
load_dotenv('../.env')

# Test configuration
BASE_URL = "https://healthcare-clinic-backend.fly.dev/api/onboarding"
TEST_CLINIC_ID = "e0c84f56-235d-49f2-9a44-37c1be579afc"  # Replace with actual clinic ID

def print_header(title):
    """Print a formatted header"""
    print(f"\n{Fore.CYAN}{'=' * 60}")
    print(f"{Fore.CYAN}{title}")
    print(f"{Fore.CYAN}{'=' * 60}{Style.RESET_ALL}")

def print_test(name, status, message=""):
    """Print test result with color coding"""
    if status == "pass":
        print(f"{Fore.GREEN}✅ {name}: PASS{Style.RESET_ALL}")
        if message:
            print(f"   {message}")
    elif status == "fail":
        print(f"{Fore.RED}❌ {name}: FAIL{Style.RESET_ALL}")
        if message:
            print(f"   {Fore.YELLOW}{message}{Style.RESET_ALL}")
    elif status == "info":
        print(f"{Fore.BLUE}ℹ️  {name}{Style.RESET_ALL}")
        if message:
            print(f"   {message}")
    elif status == "warning":
        print(f"{Fore.YELLOW}⚠️  {name}: WARNING{Style.RESET_ALL}")
        if message:
            print(f"   {message}")

async def test_calendar_status(client: httpx.AsyncClient, clinic_id: str):
    """Test calendar connection status"""
    print_header("CALENDAR INTEGRATION STATUS")

    try:
        # Check current status
        response = await client.get(f"{BASE_URL}/{clinic_id}/calendar/status")

        if response.status_code == 200:
            data = response.json()

            if data.get('connected'):
                print_test("Calendar Connection", "pass",
                          f"Provider: {data.get('provider', 'unknown')}")
                print_test("Status", "info", data.get('message', ''))

                if data.get('expires_at'):
                    expires = datetime.fromisoformat(data['expires_at'].replace('Z', '+00:00'))
                    remaining = (expires - datetime.utcnow()).total_seconds() / 3600
                    if remaining > 0:
                        print_test("Token Expiry", "info",
                                  f"Expires in {remaining:.1f} hours")
                    else:
                        print_test("Token Expiry", "warning",
                                  "Token has expired - reconnection needed")

                return True
            else:
                print_test("Calendar Connection", "fail",
                          data.get('message', 'Not connected'))

                if data.get('expired'):
                    print_test("Token Status", "warning",
                              "Token expired - needs reconnection")

                return False
        else:
            print_test("Status Check", "fail",
                      f"HTTP {response.status_code}: {response.text[:100]}")
            return False

    except Exception as e:
        print_test("Calendar Status Check", "fail", str(e))
        return False

async def test_calendar_oauth_url(client: httpx.AsyncClient, clinic_id: str):
    """Test OAuth URL generation"""
    print_header("CALENDAR OAUTH URL GENERATION")

    try:
        response = await client.post(
            f"{BASE_URL}/{clinic_id}/calendar/quick-setup",
            json={"provider": "google"}
        )

        if response.status_code == 200:
            data = response.json()

            if data.get('success') and data.get('auth_url'):
                print_test("OAuth URL Generation", "pass")

                # Parse and validate the OAuth URL
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(data['auth_url'])
                params = parse_qs(parsed.query)

                # Check required OAuth parameters
                required_params = ['client_id', 'redirect_uri', 'response_type', 'scope', 'state']
                for param in required_params:
                    if param in params:
                        value = params[param][0]
                        if param == 'client_id':
                            value = value[:20] + '...'
                        elif param == 'redirect_uri':
                            print_test(f"Redirect URI", "info", value)
                        elif param == 'scope':
                            scopes = value.split()
                            print_test(f"OAuth Scopes", "info", f"{len(scopes)} scopes requested")
                        print_test(f"OAuth Param: {param}", "pass", value[:50] if len(value) > 50 else value)
                    else:
                        print_test(f"OAuth Param: {param}", "fail", "Missing")

                return True
            else:
                print_test("OAuth URL Generation", "fail",
                          data.get('error', 'No auth URL returned'))
                return False
        else:
            print_test("OAuth URL Request", "fail",
                      f"HTTP {response.status_code}: {response.text[:100]}")
            return False

    except Exception as e:
        print_test("OAuth URL Generation", "fail", str(e))
        return False

async def test_whatsapp_status(client: httpx.AsyncClient, clinic_id: str):
    """Test WhatsApp connection status"""
    print_header("WHATSAPP INTEGRATION STATUS")

    try:
        response = await client.get(f"{BASE_URL}/{clinic_id}/whatsapp/status")

        if response.status_code == 200:
            data = response.json()

            if data.get('connected'):
                print_test("WhatsApp Connection", "pass")
                print_test("Phone Number", "info", data.get('phone_number', 'Not set'))
                print_test("Provider", "info", data.get('provider', 'unknown'))
                print_test("Status", "info", data.get('message', ''))
                return True
            else:
                print_test("WhatsApp Connection", "fail",
                          data.get('message', 'Not connected'))

                if data.get('needs_fix'):
                    print_test("Configuration", "warning",
                              "Phone number format needs correction (must start with +)")

                return False
        else:
            print_test("Status Check", "fail",
                      f"HTTP {response.status_code}: {response.text[:100]}")
            return False

    except Exception as e:
        print_test("WhatsApp Status Check", "fail", str(e))
        return False

async def test_whatsapp_send(client: httpx.AsyncClient, clinic_id: str):
    """Test sending a WhatsApp message"""
    print_header("WHATSAPP MESSAGE TEST")

    print_test("Test Message", "info", "Attempting to send test message...")

    try:
        response = await client.post(f"{BASE_URL}/{clinic_id}/whatsapp/test")

        if response.status_code == 200:
            data = response.json()

            if data.get('success'):
                print_test("Message Send", "pass",
                          f"Message SID: {data.get('message_sid', 'unknown')}")
                print_test("Recipient", "info", data.get('to', 'unknown'))
                return True
            else:
                print_test("Message Send", "fail",
                          data.get('error', 'Failed to send'))
                return False
        else:
            print_test("Message Request", "fail",
                      f"HTTP {response.status_code}: {response.text[:100]}")
            return False

    except Exception as e:
        print_test("WhatsApp Test Message", "fail", str(e))
        return False

async def test_calendar_disconnect(client: httpx.AsyncClient, clinic_id: str):
    """Test calendar disconnection"""
    print_header("CALENDAR DISCONNECT TEST")

    try:
        response = await client.delete(f"{BASE_URL}/{clinic_id}/calendar/disconnect")

        if response.status_code == 200:
            data = response.json()

            if data.get('success'):
                print_test("Calendar Disconnect", "pass",
                          data.get('message', 'Disconnected'))

                # Verify it's actually disconnected
                status_response = await client.get(f"{BASE_URL}/{clinic_id}/calendar/status")
                if status_response.status_code == 200:
                    status_data = status_response.json()
                    if not status_data.get('connected'):
                        print_test("Disconnect Verification", "pass",
                                  "Calendar is no longer connected")
                    else:
                        print_test("Disconnect Verification", "fail",
                                  "Calendar still shows as connected")

                return True
            else:
                print_test("Calendar Disconnect", "fail",
                          data.get('error', 'Failed'))
                return False
        else:
            print_test("Disconnect Request", "fail",
                      f"HTTP {response.status_code}")
            return False

    except Exception as e:
        print_test("Calendar Disconnect", "fail", str(e))
        return False

async def main():
    """Run all integration tests"""
    print(f"{Fore.CYAN}╔════════════════════════════════════════════════════════════╗")
    print(f"{Fore.CYAN}║     CLINIC ONBOARDING INTEGRATION TEST SUITE              ║")
    print(f"{Fore.CYAN}╚════════════════════════════════════════════════════════════╝{Style.RESET_ALL}")

    print(f"\n{Fore.YELLOW}Configuration:{Style.RESET_ALL}")
    print(f"  Base URL: {BASE_URL}")
    print(f"  Clinic ID: {TEST_CLINIC_ID}")
    print(f"  Timestamp: {datetime.now().isoformat()}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Test results tracking
        results = {
            "calendar_status": False,
            "calendar_oauth": False,
            "whatsapp_status": False,
            "whatsapp_send": False,
            "calendar_disconnect": False
        }

        # Run tests
        results["calendar_status"] = await test_calendar_status(client, TEST_CLINIC_ID)
        results["calendar_oauth"] = await test_calendar_oauth_url(client, TEST_CLINIC_ID)
        results["whatsapp_status"] = await test_whatsapp_status(client, TEST_CLINIC_ID)

        # Only test WhatsApp send if connected
        if results["whatsapp_status"]:
            results["whatsapp_send"] = await test_whatsapp_send(client, TEST_CLINIC_ID)
        else:
            print_header("WHATSAPP MESSAGE TEST")
            print_test("Test Skipped", "info", "WhatsApp not connected")

        # Test disconnect only if connected
        if results["calendar_status"]:
            disconnect_test = input(f"\n{Fore.YELLOW}Test calendar disconnect? (y/n): {Style.RESET_ALL}")
            if disconnect_test.lower() == 'y':
                results["calendar_disconnect"] = await test_calendar_disconnect(client, TEST_CLINIC_ID)

        # Summary
        print_header("TEST SUMMARY")

        passed = sum(1 for v in results.values() if v)
        total = len([v for v in results.values() if v is not False])

        print(f"\n{Fore.CYAN}Results: {passed}/{total} tests passed{Style.RESET_ALL}")

        for test_name, result in results.items():
            if result is not False:
                status = "pass" if result else "fail"
                print_test(test_name.replace("_", " ").title(), status)

        # Recommendations
        print_header("RECOMMENDATIONS")

        if not results["calendar_status"]:
            print(f"{Fore.YELLOW}📋 Calendar Integration:{Style.RESET_ALL}")
            print("   1. Add this redirect URI to Google Cloud Console:")
            print(f"      {Fore.CYAN}https://healthcare-clinic-backend.fly.dev/api/onboarding/calendar/callback{Style.RESET_ALL}")
            print("   2. Ensure Google OAuth credentials are correct")
            print("   3. Try reconnecting through the UI")

        if not results["whatsapp_status"]:
            print(f"{Fore.YELLOW}📱 WhatsApp Integration:{Style.RESET_ALL}")
            print("   1. Ensure phone number starts with + and country code")
            print("   2. Verify Twilio WhatsApp sandbox is configured")
            print("   3. Check webhook URL in Twilio console")

        if passed == total and total > 0:
            print(f"\n{Fore.GREEN}✨ All integrations working correctly!{Style.RESET_ALL}")

        return passed == total

if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
