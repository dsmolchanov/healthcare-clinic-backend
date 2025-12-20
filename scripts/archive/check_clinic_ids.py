#!/usr/bin/env python3
"""
Check and Fix Clinic IDs for RAG System
"""

from dotenv import load_dotenv
load_dotenv()

import os
from supabase import create_client, ClientOptions

# Create two clients - one for healthcare schema, one for public
healthcare_options = ClientOptions(
    schema='healthcare',
    auto_refresh_token=True,
    persist_session=False
)

public_options = ClientOptions(
    schema='public',
    auto_refresh_token=True,
    persist_session=False  
)

healthcare_supabase = create_client(
    os.environ.get('SUPABASE_URL'),
    os.environ.get('SUPABASE_ANON_KEY'),
    options=healthcare_options
)

public_supabase = create_client(
    os.environ.get('SUPABASE_URL'),
    os.environ.get('SUPABASE_ANON_KEY'),
    options=public_options
)

# Check clinic IDs
clinic_ids = [
    'e0c84f56-aa76-45fa-aebc-9fe5b49f7e86',  # Widget default
    '3e411ecb-3411-4add-91e2-8fa897310cb0',  # Shtern Dental
    'e0c84f56-58c1-4089-a2d9-18af2e58dda7'   # From logs
]

print('Checking clinics in healthcare schema:')
print('='*60)

for cid in clinic_ids:
    result = healthcare_supabase.table('clinics').select('id, name, organization_id').eq('id', cid).execute()
    if result.data:
        clinic = result.data[0]
        print(f'✓ Found: {cid}')
        print(f'  Name: {clinic["name"]}')
        print(f'  Org ID: {clinic.get("organization_id", "None")}')
        
        # Check documents in public schema
        doc_result = public_supabase.table('knowledge_documents').select('id').eq('clinic_id', cid).execute()
        if doc_result.data:
            print(f'  Documents: {len(doc_result.data)}')
        else:
            print(f'  Documents: 0')
    else:
        print(f'✗ Not found: {cid}')
    print()

print('\nChecking which clinic has documents in public.knowledge_documents:')
print('='*60)

# Get all unique clinic IDs with documents
docs_result = public_supabase.table('knowledge_documents').select('clinic_id').execute()
if docs_result.data:
    clinic_ids_with_docs = set(doc['clinic_id'] for doc in docs_result.data)
    for cid in clinic_ids_with_docs:
        count_result = public_supabase.table('knowledge_documents').select('id').eq('clinic_id', cid).execute()
        # Try to get clinic name from healthcare schema
        clinic_result = healthcare_supabase.table('clinics').select('name').eq('id', cid).execute()
        clinic_name = clinic_result.data[0]['name'] if clinic_result.data else 'Unknown'
        print(f'Clinic {cid} ({clinic_name}): {len(count_result.data)} documents')

print('\n\nPinecone Index Name Calculation:')
print('='*60)
print('The multilingual_message_processor.py truncates clinic IDs to 8 chars:')
print('safe_clinic_id = clinic_id.lower().replace("_", "-")[:8]')
print()

for cid in clinic_ids_with_docs:
    truncated = cid.lower().replace('_', '-')[:8]
    print(f'{cid} → clinic-{truncated}-kb')

print('\n\nSolution:')
print('='*60)
print('The widget needs to use the correct clinic ID.')
print('The documents are stored under: 3e411ecb-3411-4add-91e2-8fa897310cb0')
print('Update the widget ChatInterface.tsx to use:')
print('clinicId = "3e411ecb-3411-4add-91e2-8fa897310cb0"')