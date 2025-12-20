import os
from supabase import create_client
from supabase.client import ClientOptions

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

options = ClientOptions(schema='healthcare')
supabase = create_client(url, key, options=options)

# Check calendar_integrations
result = supabase.from_('calendar_integrations').select('*').execute()

print(f"Total calendar integrations: {len(result.data)}")
print(f"\nData:")
for integration in result.data:
    print(f"  - Clinic: {integration.get('clinic_id')}")
    print(f"    Provider: {integration.get('provider')}")
    print(f"    Sync enabled: {integration.get('sync_enabled')}")
    print(f"    Has credentials: {bool(integration.get('credentials_vault_ref'))}")
    print()
