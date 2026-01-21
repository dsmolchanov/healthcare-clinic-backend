#!/usr/bin/env python3
"""Check sales organization and WhatsApp configuration."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from supabase import create_client

url = os.environ.get('SUPABASE_URL')
key = os.environ.get('SUPABASE_SERVICE_KEY')
supabase = create_client(url, key)

print("=== Sales Organizations ===")
orgs = supabase.schema('sales').table('organizations').select('*').execute()
for org in orgs.data:
    print(f"ID: {org['id']}")
    print(f"Name: {org['name']}")
    print(f"Slug: {org.get('slug')}")
    print(f"Settings: {org.get('settings')}")
    print()

print("\n=== WhatsApp Integrations (sales schema) ===")
try:
    wa = supabase.schema('sales').table('whatsapp_integrations').select('*').execute()
    for item in wa.data:
        print(item)
except Exception as e:
    print(f"No whatsapp_integrations table or error: {e}")

print("\n=== Agent Configs (sales schema) ===")
try:
    agents = supabase.schema('sales').table('agent_configs').select('id, name, organization_id, is_active').execute()
    for agent in agents.data:
        print(f"ID: {agent['id']}, Name: {agent['name']}, Org: {agent['organization_id']}, Active: {agent['is_active']}")
except Exception as e:
    print(f"Error: {e}")
