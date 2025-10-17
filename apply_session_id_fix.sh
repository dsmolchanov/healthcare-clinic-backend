#!/bin/bash
# Apply session_id column type fix migration

set -e

MIGRATION_FILE="../../infra/db/migrations/20251018_fix_session_id_column_types.sql"

echo "🔧 Applying session_id column type fix migration..."
echo "================================================"
echo ""
echo "This migration will:"
echo "  1. Convert core.whatsapp_conversations.session_id from varchar to UUID"
echo "  2. Add FK constraint to conversation_sessions"
echo "  3. Add performance indexes for logging tables"
echo "  4. Verify all session_id columns are UUID"
echo ""
echo "Press Enter to continue or Ctrl+C to cancel..."
read

# Apply the migration
python3 apply_migration.py "$MIGRATION_FILE"

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Migration applied successfully!"
    echo ""
    echo "Verification steps:"
    echo "  1. Check that core.whatsapp_conversations.session_id is now UUID"
    echo "  2. Verify FK constraint exists: whatsapp_conversations_session_id_fkey"
    echo "  3. Check indexes: idx_whatsapp_conversations_session_id, idx_conversation_logs_session_id"
    echo ""
else
    echo ""
    echo "❌ Migration failed!"
    echo "Check the error messages above for details."
    exit 1
fi
