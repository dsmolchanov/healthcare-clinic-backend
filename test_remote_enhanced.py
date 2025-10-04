#!/usr/bin/env python3
"""Test remote enhanced mapping and preview"""

import requests
import json

# Test CSV with fields that should auto-map
test_csv = """first_name,last_name,email,phone,languages,specialization,education
Sarah,Wilson,sarah.wilson@shtern.com,(555) 222-3333,"English, Hebrew",Orthodontics,NYU Dental
Robert,Davis,robert.davis@shtern.com,(555) 444-5555,English,Endodontics,UCLA School of Dentistry"""

clinic_id = 'e0c84f56-235d-49f2-9a44-37c1be579afc'
base_url = 'https://healthcare-clinic-backend.fly.dev/api/bulk-upload'

print("=== TESTING REMOTE ENHANCED MAPPING ===\n")

# Discovery
files = {'file': ('test.csv', test_csv, 'text/csv')}
data = {'clinic_id': clinic_id}

response = requests.post(f"{base_url}/discover", files=files, data=data, timeout=30)
print(f"Discovery Status: {response.status_code}\n")

if response.status_code == 200:
    result = response.json()
    session_id = result['session_id']

    print("Discovered entities with auto-mapping:")
    print("-" * 60)
    for entity in result['discovered_entities']:
        confidence = entity.get('confidence', 0)
        auto_map = " ✅ AUTO-MAPPED" if confidence >= 0.80 else " ❓ Manual"
        print(f"{entity['field_name']:20} → {entity['suggested_table']}.{entity['suggested_field']:20} [{confidence:.2f}]{auto_map}")
    print("-" * 60)

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

    # Validate to see preview
    validation_data = {
        'session_id': session_id,
        'mappings': json.dumps(mappings)
    }

    response = requests.post(f"{base_url}/validate-mappings", data=validation_data, timeout=30)
    print(f"\nValidation Status: {response.status_code}")

    if response.status_code == 200:
        validation = response.json()
        print("\nValidation Preview:")

        for table, info in validation.get('tables_preview', {}).items():
            print(f"\n📋 {table.upper()} Table:")
            print(f"  Records to import: {info['record_count']}")
            print(f"  Fields mapped: {len(info['fields'])}")

            # Show sample row if available
            if 'sample_row' in info and info['sample_row']:
                print(f"\n  Preview of first row:")
                print("  " + "-" * 50)
                for field, value in info['sample_row'].items():
                    print(f"  {field:25} : {value}")
                print("  " + "-" * 50)
            else:
                print("  (No preview available)")
else:
    print(f"Discovery failed: {response.text[:500]}")