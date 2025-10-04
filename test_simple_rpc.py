#!/usr/bin/env python3
"""
Simple test to debug RPC functions
"""

import os
from supabase import create_client, Client
from dotenv import load_dotenv
import json

# Load environment variables
load_dotenv()

# Initialize Supabase client
supabase: Client = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_ANON_KEY")
)

print("\n" + "="*60)
print("Testing list_clinics RPC function")
print("="*60)

# Test 1: List clinics without any parameters
print("\n1. Testing list_clinics() with no parameters:")
try:
    result = supabase.rpc("list_clinics").execute()
    print(f"Result data type: {type(result.data)}")
    print(f"Result data: {json.dumps(result.data, indent=2)}")
except Exception as e:
    print(f"Error: {e}")

# Test 2: List clinics with empty object
print("\n2. Testing list_clinics with empty object:")
try:
    result = supabase.rpc("list_clinics", {}).execute()
    print(f"Result data type: {type(result.data)}")
    print(f"Result data: {json.dumps(result.data, indent=2)}")
except Exception as e:
    print(f"Error: {e}")

# Test 3: Direct query to healthcare.clinics table
print("\n3. Direct query to healthcare.clinics table:")
try:
    result = supabase.schema("healthcare").table("clinics").select("*").limit(5).execute()
    print(f"Found {len(result.data)} clinics directly")
    for clinic in result.data[:3]:
        print(f"  - {clinic.get('name')} ({clinic.get('id')})")
        print(f"    is_active: {clinic.get('is_active')}")
except Exception as e:
    print(f"Error: {e}")

print("\n" + "="*60)
