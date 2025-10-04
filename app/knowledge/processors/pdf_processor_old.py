# app/knowledge/processors/pdf_processor.py
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
                    full_text += f"\n\n--- Page {page_num + 1} ---\n\n{text}"
            
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
                    full_text += page.get_text() + "\n"
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
