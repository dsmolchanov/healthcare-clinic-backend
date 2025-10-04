#!/usr/bin/env python3
"""
Test script to verify calendar endpoint is accessible
"""

import requests
import json

# Test endpoint URLs
endpoints = [
    "https://healthcare-clinic-backend.fly.dev/api/onboarding/test-clinic/calendar",
    "https://healthcare-clinic-backend.fly.dev/api/onboarding/test-clinic/calendar/quick-setup"
]

test_data = {
    "provider": "google"
}

print("=" * 60)
print("TESTING CALENDAR ENDPOINTS")
print("=" * 60)

for url in endpoints:
    print(f"\nTesting: {url}")
    try:
        response = requests.post(url, json=test_data, timeout=10)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            result = response.json()
            if result.get('success'):
                print("✅ Endpoint working!")
                print(f"Auth URL generated: {result.get('auth_url', '')[:80]}...")
            else:
                print(f"❌ Error: {result.get('error')}")
        else:
            print(f"❌ HTTP Error: {response.status_code}")
            print(f"Response: {response.text[:200]}")
    except Exception as e:
        print(f"❌ Request failed: {e}")

print("\n" + "=" * 60)
