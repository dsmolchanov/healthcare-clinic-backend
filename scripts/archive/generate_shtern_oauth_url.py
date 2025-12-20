#!/usr/bin/env python3
"""
Generate Google Calendar OAuth URL for Shtern Clinic
"""

import os
import sys
from pathlib import Path

# Load environment FIRST before any imports
from dotenv import load_dotenv
env_path = Path(__file__).parent / '../.env'
load_dotenv(env_path)

# Verify Google credentials are loaded
print("Checking Google credentials...")
print(f"GOOGLE_CLIENT_ID: {os.getenv('GOOGLE_CLIENT_ID')[:30]}..." if os.getenv('GOOGLE_CLIENT_ID') else "NOT FOUND")
print(f"GOOGLE_CLIENT_SECRET: {'*' * 10}" if os.getenv('GOOGLE_CLIENT_SECRET') else "NOT FOUND")
print(f"GOOGLE_REDIRECT_URI: {os.getenv('GOOGLE_REDIRECT_URI')}")

# Now we can build the OAuth URL manually
import json
from urllib.parse import urlencode
import secrets

def generate_oauth_url():
    """Generate OAuth URL for Shtern clinic"""

    # Shtern clinic details from our test
    clinic_id = "e0c84f56-235d-49f2-9a44-37c1be579afc"
    doctor_id = "22da5539-1d99-43ba-85d2-24623981484a"

    # Generate state token
    state_token = secrets.token_urlsafe(32)

    # Build state data
    state_data = {
        'state': state_token,
        'clinic_id': clinic_id,
        'doctor_id': doctor_id,
        'user_id': None
    }

    # OAuth parameters
    params = {
        'client_id': os.getenv('GOOGLE_CLIENT_ID'),
        'redirect_uri': os.getenv('GOOGLE_REDIRECT_URI'),
        'response_type': 'code',
        'scope': 'https://www.googleapis.com/auth/calendar https://www.googleapis.com/auth/calendar.events',
        'state': json.dumps(state_data),
        'access_type': 'offline',
        'prompt': 'consent',
        'include_granted_scopes': 'true'
    }

    # Build URL
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"

    return auth_url

# Generate the URL
auth_url = generate_oauth_url()

print("\n" + "=" * 60)
print("🔗 Google Calendar OAuth URL for Shtern Dental Clinic")
print("=" * 60)
print("\nClinic Details:")
print("  Name: Shtern Dental Clinic")
print("  Clinic ID: e0c84f56-235d-49f2-9a44-37c1be579afc")
print("  Doctor ID: 22da5539-1d99-43ba-85d2-24623981484a")
print("\n" + "=" * 60)
print("\n📝 INSTRUCTIONS:")
print("=" * 60)
print("\n1. Copy and open this URL in your browser:\n")
print(auth_url)
print("\n2. Log in with your Google account")
print("3. Grant calendar permissions")
print("4. You'll be redirected to the callback URL")
print("5. The integration will be complete!")
print("\n" + "=" * 60)

# Save to file
with open("shtern_oauth_url_final.txt", "w") as f:
    f.write(auth_url)
print("\n✅ URL saved to: shtern_oauth_url_final.txt")
print("=" * 60)