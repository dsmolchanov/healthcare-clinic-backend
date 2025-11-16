-- Check for WhatsApp integrations in healthcare schema
SELECT 
    id,
    organization_id,
    type,
    provider,
    status,
    config,
    webhook_token,
    created_at
FROM healthcare.integrations
WHERE organization_id = '4e8ddba1-ad52-4613-9a03-ec64636b3f6c'
AND type = 'whatsapp'
ORDER BY created_at DESC;
