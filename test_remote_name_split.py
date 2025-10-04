#!/usr/bin/env python3
"""Test remote bulk upload with combined name field"""

import requests
import json

# Test CSV with combined name field
test_csv = """name,specialization,phone,email
Dr. Michael Chen,Oral Surgery,(555) 222-3333,michael.chen@shtern.com
Jennifer Smith,Dental Hygiene,(555) 444-5555,jennifer.smith@shtern.com
William Brown III,Periodontics,(555) 666-7777,william.brown@shtern.com"""

clinic_id = 'e0c84f56-235d-49f2-9a44-37c1be579afc'
base_url = 'https://healthcare-clinic-backend.fly.dev/api/bulk-upload'

print("=== TESTING REMOTE NAME SPLITTING ===\n")

# Discovery phase
files = {'file': ('test_names.csv', test_csv, 'text/csv')}
data = {'clinic_id': clinic_id}

response = requests.post(f"{base_url}/discover", files=files, data=data, timeout=30)
print(f"Discovery Status: {response.status_code}")

if response.status_code == 200:
    result = response.json()
    session_id = result['session_id']

    print(f"Session ID: {session_id}")
    print(f"\nDiscovered entities:")
    for entity in result['discovered_entities']:
        print(f"  {entity['field_name']} -> {entity['suggested_table']}.{entity['suggested_field']}")

    # Create mappings
    mappings = []
    for entity in result['discovered_entities']:
        if entity.get('suggested_table') and entity.get('suggested_field'):
            mappings.append({
                "original_field": entity['field_name'],
                "target_table": entity['suggested_table'],
                "target_field": entity['suggested_field'],
                "data_type": entity.get('data_type', 'string')
            })

    # Import
    import_data = {
        'session_id': session_id,
        'mappings': json.dumps(mappings)
    }

    response = requests.post(f"{base_url}/import", data=import_data, timeout=30)
    print(f"\nImport Status: {response.status_code}")

    if response.status_code == 200:
        import_result = response.json()
        print(f"Success: {import_result.get('success')}")
        print(f"Imported: {import_result.get('imported')}")
    else:
        print(f"Error: {response.text[:500]}")
else:
    print(f"Discovery failed: {response.text[:500]}")