#!/usr/bin/env python3
"""Test the bulk upload endpoint on production"""

import requests

# Test CSV data
test_csv = """first_name,last_name,specialization,phone,email
John,Smith,Cardiology,(555) 123-4567,john.smith@clinic.com
Jane,Doe,Pediatrics,(555) 987-6543,jane.doe@clinic.com"""

# Create form data
files = {
    'file': ('test_doctors.csv', test_csv, 'text/csv')
}
data = {
    'clinic_id': 'e0c84f56-235d-49f2-9a44-37c1be579afc'  # Shtern Dental Clinic
}

# Send request
url = 'https://healthcare-clinic-backend.fly.dev/api/bulk-upload/discover'
print(f"Testing endpoint: {url}")

try:
    response = requests.post(url, files=files, data=data, timeout=30)
    print(f"Status code: {response.status_code}")

    if response.status_code == 200:
        result = response.json()
        print("✅ Success!")
        print(f"Session ID: {result.get('session_id')}")
        print(f"Entities discovered: {len(result.get('discovered_entities', []))}")
        for entity in result.get('discovered_entities', []):
            print(f"  - {entity['field_name']}: {entity['suggested_table']}.{entity['suggested_field']}")
    else:
        print(f"❌ Error: {response.text}")
except Exception as e:
    print(f"❌ Request failed: {e}")