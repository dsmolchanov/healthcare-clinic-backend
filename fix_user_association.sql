-- Direct SQL to associate user with Shtern Dental Clinic
-- Run this with supabase client or in SQL editor

-- Your specific IDs
-- User ID: 88beb3b9-89a0-4902-ae06-5dae65f7b447
-- Organization ID: 4e8ddba1-ad52-4613-9a03-ec64636b3f6c

-- First, check if association already exists
SELECT * FROM core.user_organizations
WHERE user_id = '88beb3b9-89a0-4902-ae06-5dae65f7b447'
AND organization_id = '4e8ddba1-ad52-4613-9a03-ec64636b3f6c';

-- If not exists, create the association
INSERT INTO core.user_organizations (
    user_id,
    organization_id,
    role,
    permissions,
    is_active,
    joined_at
) VALUES (
    '88beb3b9-89a0-4902-ae06-5dae65f7b447',
    '4e8ddba1-ad52-4613-9a03-ec64636b3f6c',
    'owner',
    '{"all": true}'::jsonb,
    true,
    NOW()
) ON CONFLICT (user_id, organization_id)
DO UPDATE SET
    role = 'owner',
    is_active = true,
    permissions = '{"all": true}'::jsonb;

-- Update user metadata to include organization_id
UPDATE auth.users
SET raw_user_meta_data =
    COALESCE(raw_user_meta_data, '{}'::jsonb) ||
    '{"organization_id": "4e8ddba1-ad52-4613-9a03-ec64636b3f6c"}'::jsonb
WHERE id = '88beb3b9-89a0-4902-ae06-5dae65f7b447';

-- Verify the association was created
SELECT
    uo.*,
    o.name as organization_name,
    u.email
FROM core.user_organizations uo
JOIN core.organizations o ON o.id = uo.organization_id
JOIN auth.users u ON u.id = uo.user_id
WHERE uo.user_id = '88beb3b9-89a0-4902-ae06-5dae65f7b447';

-- Check that user can now see the clinic
SELECT
    c.id as clinic_id,
    c.name as clinic_name,
    c.organization_id,
    o.name as organization_name
FROM healthcare.clinics c
JOIN core.organizations o ON o.id = c.organization_id
WHERE c.organization_id IN (
    SELECT organization_id
    FROM core.user_organizations
    WHERE user_id = '88beb3b9-89a0-4902-ae06-5dae65f7b447'
);
