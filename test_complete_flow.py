#!/usr/bin/env python3
"""Test the complete bulk upload flow"""

import requests
import json
import time

# Test CSV data
test_csv = """first_name,last_name,specialization,phone,email
John,Smith,Cardiology,(555) 123-4567,john.smith@clinic.com
Jane,Doe,Pediatrics,(555) 987-6543,jane.doe@clinic.com
Robert,Johnson,Orthopedics,(555) 555-5555,robert.j@clinic.com"""

clinic_id = 'e0c84f56-235d-49f2-9a44-37c1be579afc'  # Shtern Dental Clinic
base_url = 'https://healthcare-clinic-backend.fly.dev/api/bulk-upload'

print("=== TESTING COMPLETE BULK UPLOAD FLOW ===\n")

# Step 1: Discovery
print("1. DISCOVERY PHASE")
print("-" * 40)

files = {
    'file': ('test_doctors.csv', test_csv, 'text/csv')
}
data = {
    'clinic_id': clinic_id
}

response = requests.post(f"{base_url}/discover", files=files, data=data, timeout=30)
print(f"Status: {response.status_code}")

if response.status_code != 200:
    print(f"Error: {response.text}")
    exit(1)

discovery_result = response.json()
session_id = discovery_result['session_id']
print(f"Session ID: {session_id}")
print(f"Entities discovered: {len(discovery_result['discovered_entities'])}")

# Step 2: Create mappings
print("\n2. MAPPING PHASE")
print("-" * 40)

mappings = []
for entity in discovery_result['discovered_entities']:
    print(f"  {entity['field_name']} -> {entity['suggested_table']}.{entity['suggested_field']}")
    mappings.append({
        "original_field": entity['field_name'],
        "target_table": entity['suggested_table'],
        "target_field": entity['suggested_field'],
        "data_type": entity['data_type']
    })

# Step 3: Validate mappings
print("\n3. VALIDATION PHASE")
print("-" * 40)

validation_data = {
    'session_id': session_id,
    'mappings': json.dumps(mappings)
}

response = requests.post(f"{base_url}/validate-mappings", data=validation_data, timeout=30)
print(f"Status: {response.status_code}")

if response.status_code == 200:
    validation_result = response.json()
    print(f"Validation success: {validation_result.get('success')}")
    if 'tables_preview' in validation_result:
        for table, info in validation_result['tables_preview'].items():
            print(f"  {table}: {info['record_count']} records")

# Step 4: Import data
print("\n4. IMPORT PHASE")
print("-" * 40)

import_data = {
    'session_id': session_id,
    'mappings': json.dumps(mappings)
}

response = requests.post(f"{base_url}/import", data=import_data, timeout=30)
print(f"Status: {response.status_code}")

if response.status_code == 200:
    import_result = response.json()
    print(f"Import success: {import_result.get('success')}")

    if 'imported' in import_result:
        print("\nImported records:")
        for table, count in import_result['imported'].items():
            # Handle if count is a list or dict
            if isinstance(count, (list, dict)):
                print(f"  {table}: {len(count)} records (data: {count})")
            elif count > 0:
                print(f"  {table}: {count} records")

    if 'errors' in import_result and import_result['errors']:
        print("\nErrors:")
        for error in import_result['errors']:
            print(f"  - {error}")
else:
    print(f"Import failed: {response.text}")

print("\n=== TEST COMPLETE ===")