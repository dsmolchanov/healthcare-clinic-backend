#!/usr/bin/env python3
"""
Fix Document Upload Issues
==========================
Fixes for PDF processing and Pinecone metadata errors
"""

import os
from pathlib import Path

def fix_pdf_processor():
    """Fix the PDF processor document closed issue"""
    
    pdf_processor_content = '''# app/knowledge/processors/pdf_processor.py
"""PDF document processor with proper file handling"""

import logging
import io
from typing import List, Dict, Any, Optional
import fitz  # PyMuPDF
from .base_processor import BaseProcessor

logger = logging.getLogger(__name__)


class PDFProcessor(BaseProcessor):
    """Process PDF documents"""
    
    def process(self, file_content: bytes, metadata: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Process PDF document and extract text
        
        Args:
            file_content: Raw PDF file bytes
            metadata: Optional metadata to attach
            
        Returns:
            List of processed chunks with metadata
        """
        try:
            # Create a BytesIO object from the content
            pdf_stream = io.BytesIO(file_content)
            
            # Open PDF from bytes (not file path)
            doc = fitz.open(stream=pdf_stream, filetype="pdf")
            
            # Extract text from all pages
            full_text = ""
            for page_num in range(len(doc)):
                page = doc[page_num]
                text = page.get_text()
                if text.strip():
                    full_text += f"\\n\\n--- Page {page_num + 1} ---\\n\\n{text}"
            
            # Close the document properly
            doc.close()
            
            if not full_text.strip():
                logger.warning("PDF appears to be empty or contains only images")
                return []
            
            # Split into chunks
            chunks = self.split_text(full_text)
            
            # Add metadata to each chunk
            result = []
            for i, chunk in enumerate(chunks):
                chunk_metadata = {
                    **(metadata or {}),
                    "chunk_index": i,
                    "total_chunks": len(chunks),
                    "processor": "pdf"
                }
                result.append({
                    "text": chunk,
                    "metadata": chunk_metadata
                })
            
            logger.info(f"Successfully processed PDF with {len(result)} chunks")
            return result
            
        except Exception as e:
            logger.error(f"Error processing PDF: {e}")
            # Try alternative method if primary fails
            try:
                return self._process_with_fallback(file_content, metadata)
            except Exception as fallback_error:
                logger.error(f"Fallback processing also failed: {fallback_error}")
                raise
    
    def _process_with_fallback(self, file_content: bytes, metadata: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Fallback PDF processing method"""
        try:
            # Alternative: Save temporarily and read
            import tempfile
            import os
            
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
                tmp.write(file_content)
                tmp_path = tmp.name
            
            try:
                doc = fitz.open(tmp_path)
                full_text = ""
                for page in doc:
                    full_text += page.get_text() + "\\n"
                doc.close()
            finally:
                # Clean up temp file
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            
            if full_text.strip():
                chunks = self.split_text(full_text)
                return [{"text": chunk, "metadata": metadata or {}} for chunk in chunks]
            
            return []
            
        except Exception as e:
            logger.error(f"Fallback PDF processing failed: {e}")
            raise
'''
    
    # Write the fixed PDF processor
    pdf_path = Path("app/knowledge/processors/pdf_processor.py")
    pdf_path.write_text(pdf_processor_content)
    print(f"✅ Fixed PDF processor at {pdf_path}")


def fix_knowledge_ingestion():
    """Fix the Pinecone metadata serialization issue"""
    
    print("\n📝 Fixing Pinecone metadata serialization...")
    
    # Read current ingestion file
    ingestion_path = Path("app/api/knowledge_ingestion.py")
    content = ingestion_path.read_text()
    
    # Add helper function to clean metadata
    helper_function = '''
    def _clean_metadata_for_pinecone(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Clean metadata to ensure Pinecone compatibility
        
        Pinecone only accepts strings, numbers, booleans, or lists of strings as metadata values
        """
        cleaned = {}
        for key, value in metadata.items():
            if value is None:
                continue
            elif isinstance(value, (str, int, float, bool)):
                cleaned[key] = value
            elif isinstance(value, list):
                # Ensure list contains only strings
                if all(isinstance(v, str) for v in value):
                    cleaned[key] = value
                else:
                    # Convert list items to strings
                    cleaned[key] = [str(v) for v in value]
            elif isinstance(value, dict):
                # Convert dict to JSON string
                import json
                cleaned[key] = json.dumps(value)
            else:
                # Convert other types to string
                cleaned[key] = str(value)
        
        return cleaned
'''
    
    # Find where to insert the helper
    lines = content.split('\n')
    
    # Find the class definition
    class_line = -1
    for i, line in enumerate(lines):
        if 'class KnowledgeIngestionPipeline' in line:
            class_line = i
            break
    
    # Find the first method after __init__
    insert_line = -1
    for i in range(class_line, len(lines)):
        if 'def ingest_document' in lines[i]:
            insert_line = i
            break
    
    # Insert the helper function before ingest_document
    if insert_line > 0 and '_clean_metadata_for_pinecone' not in content:
        helper_lines = helper_function.strip().split('\n')
        for j, helper_line in enumerate(helper_lines):
            lines.insert(insert_line + j, helper_line)
        
        # Now update the metadata usage in the ingest_document method
        updated_content = '\n'.join(lines)
        
        # Replace metadata usage in vectors
        updated_content = updated_content.replace(
            "'metadata': {\n                    **metadata,",
            "'metadata': self._clean_metadata_for_pinecone({\n                    **metadata,"
        )
        
        # Also update the closing of metadata dict
        updated_content = updated_content.replace(
            "                    'indexed_at': datetime.utcnow().isoformat()\n                }",
            "                    'indexed_at': datetime.utcnow().isoformat()\n                })"
        )
        
        # Write back
        ingestion_path.write_text(updated_content)
        print("✅ Fixed Pinecone metadata serialization")
    else:
        print("ℹ️ Metadata cleaning already implemented or insertion point not found")


def fix_knowledge_routes():
    """Fix the knowledge routes to handle file uploads properly"""
    
    routes_content = '''# Partial fix for app/api/knowledge_routes.py
# Add this error handling to the upload endpoint

async def upload_document(
    file: UploadFile = File(...),
    category: str = Form("general"),
    organization_id: str = Form(...),
    job_id: Optional[str] = Form(None)
):
    """Upload and process a document with better error handling"""
    
    try:
        # Read file content into memory once
        file_content = await file.read()
        
        # Reset file pointer if needed
        await file.seek(0)
        
        # Process based on content type
        content_type = file.content_type or "application/octet-stream"
        
        # Special handling for PDFs
        if content_type == "application/pdf" or file.filename.endswith('.pdf'):
            # Ensure we have bytes
            if isinstance(file_content, str):
                file_content = file_content.encode('utf-8')
            
            # Process PDF with fixed processor
            from app.knowledge.processors.pdf_processor import PDFProcessor
            processor = PDFProcessor()
            chunks = processor.process(file_content, {
                "filename": file.filename,
                "category": category,
                "content_type": content_type
            })
        else:
            # Process other file types
            # ... existing processing logic ...
            pass
            
        # Continue with ingestion
        # ... rest of the endpoint ...
        
    except Exception as e:
        logger.error(f"Error processing upload: {e}")
        raise HTTPException(status_code=500, detail=str(e))
'''
    
    print("\n📝 Knowledge routes fix instructions:")
    print("The knowledge routes need manual update to:")
    print("1. Read file content once and pass bytes to processors")
    print("2. Handle file pointer properly")
    print("3. Clean metadata before sending to Pinecone")


def main():
    """Apply all upload fixes"""
    
    print("🔧 Fixing Document Upload Issues")
    print("=" * 50)
    
    # Fix PDF processor
    fix_pdf_processor()
    
    # Fix Pinecone metadata
    fix_knowledge_ingestion()
    
    # Show routes fix instructions
    fix_knowledge_routes()
    
    print("\n✅ Fixes applied!")
    print("\nNext steps:")
    print("1. Deploy the fixes: fly deploy -a clinic-webhooks")
    print("2. Test PDF uploads through the UI")
    print("3. Monitor logs for successful ingestion")
    
    print("\n💡 What was fixed:")
    print("1. PDF processor now handles BytesIO properly")
    print("2. Metadata is cleaned before sending to Pinecone")
    print("3. Complex objects are serialized to JSON strings")


if __name__ == "__main__":
    main()