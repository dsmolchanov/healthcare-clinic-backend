# app/knowledge/processors/pdf_processor.py
"""PDF document processor with proper file handling"""

import logging
import io
import time
from typing import List, Dict, Any, Optional
import fitz  # PyMuPDF
from langchain.text_splitter import RecursiveCharacterTextSplitter
from app.knowledge.router import InputData, ProcessedDocument

logger = logging.getLogger(__name__)


class PDFProcessor:
    """Process PDF documents"""
    
    supported_mime_types = ['application/pdf']
    
    def __init__(self):
        """Initialize PDF processor with text splitter"""
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            separators=["\n\n", "\n", ". ", " ", ""]
        )
    
    def split_text(self, text: str) -> List[str]:
        """Split text into chunks"""
        return self.text_splitter.split_text(text)
    
    def can_process(self, mime_type: str) -> bool:
        """Check if this processor can handle the given mime type"""
        return mime_type in self.supported_mime_types
    
    async def process(self, input_data: InputData) -> ProcessedDocument:
        """
        Process PDF document and extract text
        
        Args:
            input_data: Input data containing PDF content
            
        Returns:
            ProcessedDocument with extracted content
        """
        start_time = time.time()
        
        try:
            # Get bytes content
            if isinstance(input_data.content, bytes):
                file_content = input_data.content
            else:
                file_content = input_data.content.encode('utf-8')
            
            # Create a BytesIO object from the content
            pdf_stream = io.BytesIO(file_content)
            
            # Open PDF from bytes (not file path)
            doc = fitz.open(stream=pdf_stream, filetype="pdf")
            
            # Extract text from all pages and store page count
            full_text = ""
            page_count = len(doc)  # Store page count BEFORE closing document
            for page_num in range(page_count):
                page = doc[page_num]
                text = page.get_text()
                if text.strip():
                    full_text += f"\n\n--- Page {page_num + 1} ---\n\n{text}"
            
            # Close the document properly
            doc.close()
            
            if not full_text.strip():
                logger.warning("PDF appears to be empty or contains only images")
                return ProcessedDocument(
                    content="",
                    chunks=[],
                    facts={"empty": True, "page_count": page_count},
                    metadata=input_data.metadata or {},
                    processing_time_ms=(time.time() - start_time) * 1000,
                    tokens_count=0
                )
            
            # Split into chunks
            chunks = self.split_text(full_text)
            
            # Create chunk dictionaries
            chunk_dicts = []
            for i, chunk in enumerate(chunks):
                chunk_metadata = {
                    **(input_data.metadata or {}),
                    "chunk_index": i,
                    "total_chunks": len(chunks),
                    "processor": "pdf",
                    "filename": input_data.filename
                }
                chunk_dicts.append({
                    "text": chunk,
                    "metadata": chunk_metadata
                })
            
            # Extract basic facts
            facts = {
                "page_count": page_count,  # Use stored page count
                "has_text": bool(full_text.strip()),
                "chunk_count": len(chunks)
            }
            
            # Calculate processing time
            processing_time_ms = (time.time() - start_time) * 1000
            
            logger.info(f"Successfully processed PDF with {len(chunks)} chunks")
            
            return ProcessedDocument(
                content=full_text,
                chunks=chunk_dicts,
                facts=facts,
                metadata=input_data.metadata or {},
                processing_time_ms=processing_time_ms,
                tokens_count=len(full_text.split())
            )
            
        except Exception as e:
            logger.error(f"Error processing PDF: {e}")
            # Try alternative method if primary fails
            try:
                return await self._process_with_fallback(input_data, start_time)
            except Exception as fallback_error:
                logger.error(f"Fallback processing also failed: {fallback_error}")
                raise
    
    async def _process_with_fallback(self, input_data: InputData, start_time: float) -> ProcessedDocument:
        """Fallback PDF processing method"""
        try:
            # Get bytes content
            if isinstance(input_data.content, bytes):
                file_content = input_data.content
            else:
                file_content = input_data.content.encode('utf-8')
            
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
                page_count = len(doc)
                doc.close()
            finally:
                # Clean up temp file
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            
            if full_text.strip():
                chunks = self.split_text(full_text)
                chunk_dicts = []
                for i, chunk in enumerate(chunks):
                    chunk_dicts.append({
                        "text": chunk,
                        "metadata": {
                            **(input_data.metadata or {}),
                            "chunk_index": i,
                            "total_chunks": len(chunks),
                            "processor": "pdf_fallback"
                        }
                    })
                
                return ProcessedDocument(
                    content=full_text,
                    chunks=chunk_dicts,
                    facts={"page_count": page_count, "fallback_used": True},
                    metadata=input_data.metadata or {},
                    processing_time_ms=(time.time() - start_time) * 1000,
                    tokens_count=len(full_text.split())
                )
            
            return ProcessedDocument(
                content="",
                chunks=[],
                facts={"empty": True, "fallback_used": True},
                metadata=input_data.metadata or {},
                processing_time_ms=(time.time() - start_time) * 1000,
                tokens_count=0
            )
            
        except Exception as e:
            logger.error(f"Fallback PDF processing failed: {e}")
            raise