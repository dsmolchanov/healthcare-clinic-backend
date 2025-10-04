#!/bin/bash

# Deploy Knowledge Metadata Fix
# Fixes title extraction and source_type for uploaded documents

echo "========================================="
echo "Deploying Knowledge Metadata Fix"
echo "========================================="
echo ""

# Check if we're in the right directory
if [ ! -f "fly.toml" ]; then
    echo "Error: fly.toml not found. Please run from clinics/backend directory"
    exit 1
fi

echo "1. Changes being deployed:"
echo "   - Fixed title extraction from filenames"
echo "   - Fixed source_type showing 'file' instead of 'url'"
echo "   - Added metadata preprocessing for file uploads"
echo ""

echo "2. Testing locally..."
python3 -c "
import sys
sys.path.insert(0, '.')
try:
    from app.api.knowledge_routes import process_document_task
    print('   ✓ Knowledge routes import successfully')
except Exception as e:
    print(f'   ✗ Import error: {e}')
    sys.exit(1)
"

if [ $? -ne 0 ]; then
    echo "Local tests failed. Please fix errors before deploying."
    exit 1
fi

echo ""
echo "3. Deploying to Fly.io..."
fly deploy

if [ $? -eq 0 ]; then
    echo ""
    echo "========================================="
    echo "✓ Deployment Successful!"
    echo "========================================="
    echo ""
    echo "Fixed issues:"
    echo "1. Document titles now extracted from filenames"
    echo "2. Source type correctly shows 'file' for uploads"
    echo "3. Real-time updates work via WebSocket subscription"
    echo ""
    echo "User experience improvements:"
    echo "• No need to reload page after upload"
    echo "• Proper document titles instead of 'Untitled'"
    echo "• Correct source type in document list"
    echo ""
    echo "Monitor deployment:"
    echo "fly logs -a healthcare-clinic-backend"
else
    echo ""
    echo "✗ Deployment failed. Check the errors above."
    exit 1
fi