#!/usr/bin/env python3
"""
Create the missing clinic directly
"""

print("""
The clinic with ID '3e411ecb-3411-4add-91e2-8fa897310cb0' needs to be created in the database.

Since the environment variables are not properly set, please:

1. Go to your Supabase dashboard
2. Navigate to the SQL Editor
3. Run this SQL command:

-- Create organization first (if using organization_id foreign key)
INSERT INTO organizations (id, name, created_at, updated_at)
VALUES (
    '3e411ecb-3411-4add-91e2-8fa897310cb0',
    'Shtern Dental Organization',
    NOW(),
    NOW()
)
ON CONFLICT (id) DO NOTHING;

-- Then create the clinic
INSERT INTO clinics (
    id,
    organization_id,
    name,
    phone,
    email,
    address,
    city,
    state,
    country,
    timezone,
    settings,
    created_at,
    updated_at
) VALUES (
    '3e411ecb-3411-4add-91e2-8fa897310cb0',
    '3e411ecb-3411-4add-91e2-8fa897310cb0',
    'Shtern Dental Clinic',
    '+1234567890',
    'info@shterndental.com',
    '123 Dental Street',
    'Tel Aviv',
    'IL',
    'Israel',
    'Asia/Jerusalem',
    '{
        "business_hours": {
            "monday": {"open": "09:00", "close": "18:00"},
            "tuesday": {"open": "09:00", "close": "18:00"},
            "wednesday": {"open": "09:00", "close": "18:00"},
            "thursday": {"open": "09:00", "close": "18:00"},
            "friday": {"open": "09:00", "close": "15:00"},
            "saturday": {"closed": true},
            "sunday": {"open": "09:00", "close": "18:00"}
        },
        "appointment_settings": {
            "slot_duration_minutes": 30,
            "buffer_time_minutes": 15
        }
    }'::jsonb,
    NOW(),
    NOW()
)
ON CONFLICT (id) DO NOTHING;

-- Verify it was created
SELECT id, name, organization_id FROM clinics WHERE id = '3e411ecb-3411-4add-91e2-8fa897310cb0';

After running this SQL, try the bulk upload again.
""")