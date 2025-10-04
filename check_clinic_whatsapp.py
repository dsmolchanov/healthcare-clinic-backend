#!/usr/bin/env python3
"""
Check clinic WhatsApp configuration in database
"""

import asyncio
import os
from supabase import create_client, Client

async def check_clinic_whatsapp():
    """Check clinic WhatsApp settings"""

    # Initialize Supabase client
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_ANON_KEY")

    if not supabase_url or not supabase_key:
        print("Error: SUPABASE_URL and SUPABASE_KEY required")
        return

    supabase: Client = create_client(supabase_url, supabase_key)

    print("=" * 60)
    print("Checking Clinic WhatsApp Configuration")
    print("=" * 60)

    # Check clinics table
    print("\n1. Checking clinics table:")
    result = supabase.table("clinics").select("*").execute()

    if result.data:
        for clinic in result.data:
            print(f"\nClinic: {clinic.get('name', 'Unknown')}")
            print(f"  ID: {clinic.get('id')}")
            print(f"  Phone: {clinic.get('phone_number', 'Not set')}")
            print(f"  WhatsApp Settings: {clinic.get('whatsapp_settings', {})}")

    # Check whatsapp_integrations table
    print("\n2. Checking whatsapp_integrations table:")
    result = supabase.table("whatsapp_integrations").select("*").execute()

    if result.data:
        for integration in result.data:
            print(f"\nIntegration ID: {integration.get('id')}")
            print(f"  Clinic ID: {integration.get('clinic_id')}")
            print(f"  Phone: {integration.get('phone_number')}")
            print(f"  Provider: {integration.get('provider', 'twilio')}")
            print(f"  Active: {integration.get('is_active')}")
            print(f"  Config: {integration.get('config', {})}")
    else:
        print("  No WhatsApp integrations found")

    # Check whatsapp_sessions table for recent activity
    print("\n3. Checking recent WhatsApp sessions:")
    result = supabase.table("whatsapp_sessions").select("*").order("created_at", desc=True).limit(5).execute()

    if result.data:
        for session in result.data:
            print(f"\nSession ID: {session.get('id')}")
            print(f"  Phone: {session.get('patient_phone')}")
            print(f"  Status: {session.get('status')}")
            print(f"  Created: {session.get('created_at')}")
    else:
        print("  No WhatsApp sessions found")

    # Check evolution_instances table if it exists
    print("\n4. Checking evolution_instances table:")
    try:
        result = supabase.table("evolution_instances").select("*").execute()

        if result.data:
            for instance in result.data:
                print(f"\nInstance: {instance.get('instance_name')}")
                print(f"  Phone: {instance.get('phone_number')}")
                print(f"  Status: {instance.get('status')}")
                print(f"  Type: {instance.get('connection_type')}")
                print(f"  Webhook: {instance.get('webhook_url')}")
        else:
            print("  No Evolution instances found")
    except:
        print("  Evolution instances table not found")

if __name__ == "__main__":
    asyncio.run(check_clinic_whatsapp())