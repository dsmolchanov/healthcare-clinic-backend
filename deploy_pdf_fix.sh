#!/bin/bash

# Deploy PDF processor fix to production
# This fixes the "document closed" error

echo "========================================="
echo "Deploying PDF Processor Fix"
echo "========================================="
echo ""

# Check if we're in the right directory
if [ ! -f "fly.toml" ]; then
    echo "Error: fly.toml not found. Please run from clinics/backend directory"
    exit 1
fi

echo "1. Current deployment status:"
fly status

echo ""
echo "2. Files being updated:"
echo "   - app/knowledge/processors/pdf_processor.py (fixed document closed error)"
echo "   - app/knowledge/processors/multimodal_pdf_processor.py (new multimodal processor)"
echo "   - app/api/enhanced_knowledge_ingestion.py (enhanced pipeline)"
echo "   - app/api/knowledge_routes.py (improved error handling)"
echo "   - app/knowledge/router.py (better error messages)"

echo ""
echo "3. Testing locally first..."
python3 -c "
import sys
sys.path.insert(0, '.')
try:
    from app.knowledge.processors.pdf_processor import PDFProcessor
    from app.knowledge.processors.multimodal_pdf_processor import MultimodalPDFProcessor
    print('   ✓ Modules import successfully')
except Exception as e:
    print(f'   ✗ Import error: {e}')
    sys.exit(1)
"

if [ $? -ne 0 ]; then
    echo "Local tests failed. Please fix errors before deploying."
    exit 1
fi

echo ""
echo "4. Deploying to Fly.io..."
fly deploy

if [ $? -eq 0 ]; then
    echo ""
    echo "========================================="
    echo "✓ Deployment Successful!"
    echo "========================================="
    echo ""
    echo "Changes deployed:"
    echo "1. Fixed 'document closed' error in PDF processor"
    echo "2. Added multimodal PDF processing with GPT-5-mini"
    echo "3. Enhanced knowledge ingestion pipeline"
    echo "4. Fixed continuous embedding/chunking after PDF errors"
    echo "5. Improved error handling and early exit on failures"
    echo ""
    echo "Next steps:"
    echo "1. Monitor logs: fly logs -f"
    echo "2. Test PDF upload through the API"
    echo "3. Verify RAG retrieval improvements"
else
    echo ""
    echo "✗ Deployment failed. Check the errors above."
    exit 1
fi