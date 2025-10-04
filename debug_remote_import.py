#!/usr/bin/env python3
"""Debug remote import issue"""

import requests
import json

# Test with better field names
test_csv = """first_name,last_name,specialization,phone,email
Alice,Johnson,Orthodontics,(555) 111-2222,alice.johnson@shtern.com
Bob,Williams,Endodontics,(555) 333-4444,bob.williams@shtern.com"""

clinic_id = 'e0c84f56-235d-49f2-9a44-37c1be579afc'
base_url = 'https://healthcare-clinic-backend.fly.dev/api/bulk-upload'

print("=== DEBUGGING REMOTE IMPORT ===\n")

# Discovery
files = {'file': ('test.csv', test_csv, 'text/csv')}
data = {'clinic_id': clinic_id}

response = requests.post(f"{base_url}/discover", files=files, data=data, timeout=30)
print(f"Discovery Status: {response.status_code}")

if response.status_code == 200:
    result = response.json()
    session_id = result['session_id']

    print(f"\nDiscovered entities:")
    for entity in result['discovered_entities']:
        print(f"  {entity['field_name']} -> {entity['suggested_table']}.{entity['suggested_field']}")
        print(f"    Samples: {entity.get('sample_values', [])[:2]}")

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

    print(f"\nMappings to be used:")
    for m in mappings:
        print(f"  {m['original_field']} -> {m['target_table']}.{m['target_field']}")

    # Validate
    validation_data = {
        'session_id': session_id,
        'mappings': json.dumps(mappings)
    }

    response = requests.post(f"{base_url}/validate-mappings", data=validation_data, timeout=30)
    print(f"\nValidation Status: {response.status_code}")
    if response.status_code == 200:
        print(f"Validation result: {json.dumps(response.json(), indent=2)}")

    # Import
    import_data = {
        'session_id': session_id,
        'mappings': json.dumps(mappings)
    }

    response = requests.post(f"{base_url}/import", data=import_data, timeout=30)
    print(f"\nImport Status: {response.status_code}")

    if response.status_code == 200:
        result = response.json()
        print(f"Import result: {json.dumps(result, indent=2)}")
    else:
        print(f"Error: {response.text}")