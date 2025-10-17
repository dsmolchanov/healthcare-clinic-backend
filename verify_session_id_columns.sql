-- Verification script for session_id column types and constraints
-- Run this after applying the migration to verify everything is correct

\echo '============================================================================='
\echo 'SESSION_ID COLUMN TYPE VERIFICATION'
\echo '============================================================================='
\echo ''

-- Check all session_id column types across all schemas
\echo 'Checking session_id column types...'
\echo ''

SELECT
  table_schema,
  table_name,
  column_name,
  data_type,
  character_maximum_length,
  is_nullable,
  column_default
FROM information_schema.columns
WHERE column_name = 'session_id'
  AND table_schema IN ('core', 'healthcare', 'public')
ORDER BY table_schema, table_name;

\echo ''
\echo '============================================================================='
\echo 'FOREIGN KEY CONSTRAINTS'
\echo '============================================================================='
\echo ''

-- Check FK constraints on session_id columns
SELECT
  tc.table_schema,
  tc.table_name,
  tc.constraint_name,
  tc.constraint_type,
  kcu.column_name,
  ccu.table_schema AS foreign_table_schema,
  ccu.table_name AS foreign_table_name,
  ccu.column_name AS foreign_column_name
FROM information_schema.table_constraints AS tc
JOIN information_schema.key_column_usage AS kcu
  ON tc.constraint_name = kcu.constraint_name
  AND tc.table_schema = kcu.table_schema
LEFT JOIN information_schema.constraint_column_usage AS ccu
  ON ccu.constraint_name = tc.constraint_name
  AND ccu.table_schema = tc.table_schema
WHERE tc.constraint_type = 'FOREIGN KEY'
  AND kcu.column_name = 'session_id'
  AND tc.table_schema IN ('core', 'healthcare', 'public')
ORDER BY tc.table_schema, tc.table_name;

\echo ''
\echo '============================================================================='
\echo 'INDEXES ON SESSION_ID COLUMNS'
\echo '============================================================================='
\echo ''

-- Check indexes on session_id columns
SELECT
  schemaname,
  tablename,
  indexname,
  indexdef
FROM pg_indexes
WHERE indexdef LIKE '%session_id%'
  AND schemaname IN ('core', 'healthcare', 'public')
ORDER BY schemaname, tablename;

\echo ''
\echo '============================================================================='
\echo 'DATA STATISTICS'
\echo '============================================================================='
\echo ''

-- Count records with session_id in each table
\echo 'Records with session_id by table:'
\echo ''

SELECT 'core.whatsapp_conversations' AS table_name,
       COUNT(*) AS total_rows,
       COUNT(session_id) AS rows_with_session_id,
       COUNT(session_id) * 100.0 / NULLIF(COUNT(*), 0) AS percentage_populated
FROM core.whatsapp_conversations
UNION ALL
SELECT 'core.whatsapp_messages',
       COUNT(*),
       COUNT(session_id),
       COUNT(session_id) * 100.0 / NULLIF(COUNT(*), 0)
FROM core.whatsapp_messages
UNION ALL
SELECT 'healthcare.conversation_logs',
       COUNT(*),
       COUNT(session_id),
       COUNT(session_id) * 100.0 / NULLIF(COUNT(*), 0)
FROM healthcare.conversation_logs;

\echo ''
\echo '============================================================================='
\echo 'EXPECTED RESULTS'
\echo '============================================================================='
\echo ''
\echo 'All session_id columns should be:'
\echo '  - data_type: uuid'
\echo '  - character_maximum_length: NULL (not applicable for UUID)'
\echo '  - Have FK constraint to conversation_sessions(id)'
\echo '  - Have index for performance'
\echo ''
\echo 'If any column shows "character varying" or has a length limit,'
\echo 'the migration needs to be applied or re-run.'
\echo ''
