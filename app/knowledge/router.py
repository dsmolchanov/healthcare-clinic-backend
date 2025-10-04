"""
Document Router - Routes documents to appropriate processors based on type
"""

import os
import mimetypes
from typing import Dict, Any, Optional, Type
from abc import ABC, abstractmethod
import logging
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class InputData:
    """Input data for processing"""
    content: bytes | str
    mime_type: str
    filename: Optional[str] = None
    metadata: Dict[str, Any] = None
    clinic_id: str = None
    category: str = "general"

@dataclass
class ProcessedDocument:
    """Processed document result"""
    content: str
    chunks: list[Dict[str, Any]]
    facts: Dict[str, Any]
    metadata: Dict[str, Any]
    processing_time_ms: float
    tokens_count: int
    
class BaseProcessor(ABC):
    """Base class for all document processors"""
    
    @abstractmethod
    async def process(self, input_data: InputData) -> ProcessedDocument:
        """Process the input data and return structured output"""
        pass
    
    @abstractmethod
    def can_process(self, mime_type: str) -> bool:
        """Check if this processor can handle the given mime type"""
        pass

class DocumentRouter:
    """Routes documents to appropriate processors based on type"""
    
    def __init__(self):
        self.processors: Dict[str, Type[BaseProcessor]] = {}
        self._register_processors()
    
    def _register_processors(self):
        """Register all available processors"""
        # Import processors lazily to avoid circular imports
        from .processors.pdf_processor import PDFProcessor
        from .processors.docx_processor import DocxProcessor
        from .processors.text_processor import TextProcessor
        from .processors.html_processor import HTMLProcessor
        from .processors.image_processor import ImageOCRProcessor
        from .processors.csv_processor import CSVProcessor
        from .processors.markdown_processor import MarkdownProcessor
        
        # Register processors with their mime types
        self.processor_classes = [
            PDFProcessor,
            DocxProcessor,
            TextProcessor,
            HTMLProcessor,
            ImageOCRProcessor,
            CSVProcessor,
            MarkdownProcessor,
        ]
        
        # Build mime type to processor mapping
        for processor_class in self.processor_classes:
            processor = processor_class()
            for mime_type in processor.supported_mime_types:
                self.processors[mime_type] = processor_class
    
    def get_processor(self, mime_type: str) -> Optional[BaseProcessor]:
        """Get the appropriate processor for the given mime type"""
        # Direct match
        if mime_type in self.processors:
            return self.processors[mime_type]()
        
        # Check for wildcard matches (e.g., image/*)
        base_type = mime_type.split('/')[0] if '/' in mime_type else mime_type
        wildcard_type = f"{base_type}/*"
        
        if wildcard_type in self.processors:
            return self.processors[wildcard_type]()
        
        # Fallback to text processor for unknown types
        logger.warning(f"No specific processor for {mime_type}, using text processor")
        from .processors.text_processor import TextProcessor
        return TextProcessor()
    
    async def process(self, input_data: InputData) -> ProcessedDocument:
        """Process the input data using the appropriate processor"""
        start_time = datetime.now()
        
        # Get processor
        processor = self.get_processor(input_data.mime_type)
        
        if not processor:
            raise ValueError(f"No processor available for mime type: {input_data.mime_type}")
        
        try:
            # Process the document
            logger.info(f"Processing {input_data.mime_type} document with {processor.__class__.__name__}")
            result = await processor.process(input_data)
            
            # Validate result
            if not result:
                raise ValueError(f"Processor returned empty result for {input_data.filename or 'document'}")
            
            if not result.chunks or len(result.chunks) == 0:
                logger.warning(f"No chunks extracted from {input_data.filename or 'document'}")
                # Return with empty chunks rather than failing
                result.chunks = []
            
            # Add processing time
            processing_time_ms = (datetime.now() - start_time).total_seconds() * 1000
            result.processing_time_ms = processing_time_ms
            
            logger.info(f"Processed document in {processing_time_ms:.2f}ms, extracted {len(result.chunks)} chunks")
            
            return result
            
        except Exception as e:
            # Provide more detailed error information
            error_msg = f"Error processing {input_data.filename or 'document'} ({input_data.mime_type}): {str(e)}"
            logger.error(error_msg, exc_info=True)
            
            # Re-raise with more context
            raise ValueError(error_msg) from e
    
    def detect_mime_type(self, filename: str, content: bytes = None) -> str:
        """Detect mime type from filename or content"""
        # Try to guess from filename
        mime_type, _ = mimetypes.guess_type(filename)
        
        if mime_type:
            return mime_type
        
        # Try to detect from content
        if content:
            # Check for common file signatures
            if content.startswith(b'%PDF'):
                return 'application/pdf'
            elif content.startswith(b'PK'):  # ZIP-based formats (docx, xlsx, etc.)
                if '.docx' in filename.lower():
                    return 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                elif '.xlsx' in filename.lower():
                    return 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            elif content.startswith(b'\xff\xd8\xff'):  # JPEG
                return 'image/jpeg'
            elif content.startswith(b'\x89PNG'):  # PNG
                return 'image/png'
            elif content.startswith(b'<!DOCTYPE html') or content.startswith(b'<html'):
                return 'text/html'
        
        # Default to plain text
        return 'text/plain'
    
    def get_supported_types(self) -> list[str]:
        """Get list of all supported mime types"""
        return list(self.processors.keys())
    
    def validate_file_size(self, content: bytes, max_size_mb: int = 50) -> bool:
        """Validate file size is within limits"""
        size_mb = len(content) / (1024 * 1024)
        return size_mb <= max_size_mb