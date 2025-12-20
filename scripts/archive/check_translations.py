import os
from dotenv import load_dotenv
load_dotenv()

from app.database import create_supabase_client
import json

supabase = create_supabase_client()

# Get recently translated services
result = supabase.schema('healthcare').table('services').select('id,name,name_i18n,description_i18n').limit(5).execute()

if result.data:
    for service in result.data:
        print(f"\n{'='*60}")
        print(f"✅ Service: {service['name']}")
        print(f"   ID: {service['id']}")
        print(f"\n📦 name_i18n:")
        if service.get('name_i18n'):
            print(json.dumps(service['name_i18n'], indent=2, ensure_ascii=False))
        else:
            print("   (empty)")
        if service.get('description_i18n'):
            print(f"\n📦 description_i18n:")
            desc = service['description_i18n']
            if desc:
                print(json.dumps(desc, indent=2, ensure_ascii=False))
else:
    print("No services found")
