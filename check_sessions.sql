-- Check sessions for organization 4e8ddba1-ad52-4613-9a03-ec64636b3f6c

SELECT
    cs.id,
    cs.user_identifier,
    cs.status,
    cs.channel_type,
    cs.created_at,
    cs.metadata->>'patient_name' as patient_name,
    cs.metadata->>'phone' as phone,
    (SELECT COUNT(*) FROM healthcare.conversation_logs cl WHERE cl.session_id = cs.id) as message_count
FROM public.conversation_sessions cs
WHERE cs.organization_id = '4e8ddba1-ad52-4613-9a03-ec64636b3f6c'
ORDER BY cs.created_at DESC
LIMIT 10;

-- Check specific sessions mentioned
SELECT
    '503da1c5-89ea-45f1-b507-7f5513ba0fac' as session_name,
    COUNT(*) as message_count
FROM healthcare.conversation_logs
WHERE session_id = '503da1c5-89ea-45f1-b507-7f5513ba0fac'
UNION ALL
SELECT
    '4db648b8-ec3f-430d-89c0-adf3221e663b' as session_name,
    COUNT(*) as message_count
FROM healthcare.conversation_logs
WHERE session_id = '4db648b8-ec3f-430d-89c0-adf3221e663b';

-- Check what the conversation_sessions shows for phone 79857608984
SELECT
    id,
    user_identifier,
    status,
    metadata->>'patient_name' as patient_name,
    created_at,
    (SELECT COUNT(*) FROM healthcare.conversation_logs cl WHERE cl.session_id = cs.id) as msg_count
FROM conversation_sessions cs
WHERE (user_identifier LIKE '%79857608984%' OR metadata->>'phone' LIKE '%79857608984%')
  AND organization_id = '4e8ddba1-ad52-4613-9a03-ec64636b3f6c'
ORDER BY created_at DESC;
