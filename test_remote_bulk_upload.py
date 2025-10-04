#!/usr/bin/env python3
"""Test the bulk upload flow against the remote Fly.io deployment"""

import requests
import json

# Test CSV data with more complete information
test_csv = """first_name,last_name,specialization,phone,email,years_of_experience
Emily,Wilson,Orthodontics,(555) 111-2222,emily.wilson@shtern.com,8
David,Brown,Endodontics,(555) 333-4444,david.brown@shtern.com,12
Lisa,Martinez,Periodontics,(555) 666-7777,lisa.martinez@shtern.com,5"""

clinic_id = 'e0c84f56-235d-49f2-9a44-37c1be579afc'  # Shtern Dental Clinic
base_url = 'https://healthcare-clinic-backend.fly.dev/api/bulk-upload'

print("=== TESTING REMOTE BULK UPLOAD FLOW ===\n")

# Step 1: Discovery
print("1. DISCOVERY PHASE")
print("-" * 40)

files = {
    'file': ('test_doctors_remote.csv', test_csv, 'text/csv')
}
data = {
    'clinic_id': clinic_id
}

try:
    response = requests.post(f"{base_url}/discover", files=files, data=data, timeout=30)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text[:500] if response.text else 'Empty'}")

    if response.status_code != 200:
        print(f"\nError response:")
        print(json.dumps(response.json(), indent=2) if response.headers.get('content-type', '').startswith('application/json') else response.text)
        exit(1)

    discovery_result = response.json()
    session_id = discovery_result.get('session_id')

    if not session_id:
        print("ERROR: No session_id in response")
        print(json.dumps(discovery_result, indent=2))
        exit(1)

    print(f"Session ID: {session_id}")
    print(f"Entities discovered: {len(discovery_result.get('discovered_entities', []))}")

    # Show discovered entities
    for entity in discovery_result.get('discovered_entities', []):
        print(f"  - {entity['field_name']} -> {entity.get('suggested_table', 'unknown')}.{entity.get('suggested_field', 'unknown')}")

except Exception as e:
    print(f"Discovery failed: {e}")
    exit(1)

# Step 2: Create mappings
print("\n2. MAPPING PHASE")
print("-" * 40)

mappings = []
for entity in discovery_result.get('discovered_entities', []):
    if entity.get('suggested_table') and entity.get('suggested_field'):
        print(f"  {entity['field_name']} -> {entity['suggested_table']}.{entity['suggested_field']}")
        mappings.append({
            "original_field": entity['field_name'],
            "target_table": entity['suggested_table'],
            "target_field": entity['suggested_field'],
            "data_type": entity.get('data_type', 'string')
        })

if not mappings:
    print("ERROR: No valid mappings created")
    exit(1)

# Step 3: Validate mappings
print("\n3. VALIDATION PHASE")
print("-" * 40)

validation_data = {
    'session_id': session_id,
    'mappings': json.dumps(mappings)
}

try:
    response = requests.post(f"{base_url}/validate-mappings", data=validation_data, timeout=30)
    print(f"Status: {response.status_code}")

    if response.status_code == 200:
        validation_result = response.json()
        print(f"Validation success: {validation_result.get('success')}")
        if 'tables_preview' in validation_result:
            for table, info in validation_result['tables_preview'].items():
                print(f"  {table}: {info['record_count']} records")
    else:
        print(f"Validation failed: {response.text}")
except Exception as e:
    print(f"Validation error: {e}")

# Step 4: Import data
print("\n4. IMPORT PHASE")
print("-" * 40)

import_data = {
    'session_id': session_id,
    'mappings': json.dumps(mappings)
}

try:
    response = requests.post(f"{base_url}/import", data=import_data, timeout=30)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text[:500] if response.text else 'Empty'}")

    if response.status_code == 200:
        import_result = response.json()
        print(f"\nImport success: {import_result.get('success')}")

        if 'imported' in import_result:
            print("\nImported records:")
            for key, value in import_result['imported'].items():
                if isinstance(value, int) and value > 0:
                    print(f"  {key}: {value} records")
                elif isinstance(value, list) and len(value) > 0:
                    print(f"  {key}: {len(value)} records")

        if 'errors' in import_result and import_result['errors']:
            print("\nErrors:")
            for error in import_result['errors'] if isinstance(import_result['errors'], list) else [import_result['errors']]:
                print(f"  - {error}")
    else:
        print(f"\nImport failed with status {response.status_code}")
        print(response.text)
except Exception as e:
    print(f"Import error: {e}")

print("\n=== TEST COMPLETE ===")