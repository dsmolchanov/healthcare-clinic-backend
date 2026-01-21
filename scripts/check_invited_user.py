#!/usr/bin/env python3
"""Check invited user status in Supabase auth."""

import os
import sys
from supabase import create_client

# Load environment
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://wojtrbcbezpfwksedjmy.supabase.co")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

if not SUPABASE_SERVICE_KEY:
    # Try to load from .env file
    env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                if line.startswith("SUPABASE_SERVICE_KEY="):
                    SUPABASE_SERVICE_KEY = line.split("=", 1)[1].strip().strip('"')
                    break

if not SUPABASE_SERVICE_KEY:
    print("Error: SUPABASE_SERVICE_KEY not found")
    sys.exit(1)

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

email = "dsmolchanov@gmail.com"

print(f"Checking user: {email}")
print("=" * 60)

# Check auth.users using admin API
try:
    # List users and find the one we're looking for
    response = supabase.auth.admin.list_users()
    users = response if isinstance(response, list) else getattr(response, 'users', [])
    
    target_user = None
    for user in users:
        user_email = getattr(user, 'email', None) or (user.get('email') if isinstance(user, dict) else None)
        if user_email == email:
            target_user = user
            break
    
    if target_user:
        print("\n=== Auth User Found ===")
        if hasattr(target_user, '__dict__'):
            for key, value in target_user.__dict__.items():
                if not key.startswith('_'):
                    print(f"  {key}: {value}")
        elif isinstance(target_user, dict):
            for key, value in target_user.items():
                print(f"  {key}: {value}")
        else:
            print(f"  User object: {target_user}")
    else:
        print(f"\nNo auth user found with email: {email}")
        print(f"Total users found: {len(users)}")
        
except Exception as e:
    print(f"Error checking auth users: {e}")

# Check invitations table
print("\n=== Checking Invitations ===")
try:
    result = supabase.table("invitations").select("*").eq("email", email).execute()
    if result.data:
        for inv in result.data:
            print(f"\nInvitation found:")
            for key, value in inv.items():
                print(f"  {key}: {value}")
    else:
        print(f"No invitation found for {email}")
except Exception as e:
    print(f"Error checking invitations: {e}")

# Check staff_members table
print("\n=== Checking Staff Members ===")
try:
    result = supabase.table("staff_members").select("*").eq("email", email).execute()
    if result.data:
        for staff in result.data:
            print(f"\nStaff member found:")
            for key, value in staff.items():
                print(f"  {key}: {value}")
    else:
        print(f"No staff member found for {email}")
except Exception as e:
    print(f"Error checking staff_members: {e}")

