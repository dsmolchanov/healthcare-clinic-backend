"""
Image OCR Processor using pytesseract
"""

import logging
from typing import Dict, Any, List
import base64
from io import BytesIO

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    logging.warning("PIL not available. Install with: pip install pillow")

try:
    import pytesseract
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False
    logging.warning("pytesseract not available. Install with: pip install pytesseract")

from ..router import BaseProcessor, InputData, ProcessedDocument

logger = logging.getLogger(__name__)

class ImageOCRProcessor(BaseProcessor):
    """Process images and extract text using OCR"""
    
    supported_mime_types = ['image/*', 'image/jpeg', 'image/png', 'image/gif', 'image/bmp', 'image/webp']
    
    def can_process(self, mime_type: str) -> bool:
        return (mime_type.startswith('image/') or mime_type in self.supported_mime_types) and PIL_AVAILABLE
    
    async def process(self, input_data: InputData) -> ProcessedDocument:
        """Process image and extract text"""
        if not PIL_AVAILABLE:
            return await self._process_fallback(input_data)
        
        try:
            # Load image
            if isinstance(input_data.content, bytes):
                image = Image.open(BytesIO(input_data.content))
            else:
                # Assume base64 encoded string
                img_data = base64.b64decode(input_data.content)
                image = Image.open(BytesIO(img_data))
            
            # Get image info
            metadata = {
                'format': image.format,
                'mode': image.mode,
                'size': image.size,
                'width': image.width,
                'height': image.height,
            }
            
            # Extract text using OCR
            text = ""
            if TESSERACT_AVAILABLE:
                try:
                    # Preprocess image for better OCR
                    processed_image = self._preprocess_image(image)
                    text = pytesseract.image_to_string(processed_image)
                    metadata['ocr_engine'] = 'tesseract'
                except Exception as e:
                    logger.warning(f"OCR failed: {e}")
                    text = "OCR processing failed. Please check tesseract installation."
                    metadata['ocr_error'] = str(e)
            else:
                text = "OCR requires tesseract. Install with: apt-get install tesseract-ocr && pip install pytesseract"
                metadata['ocr_engine'] = 'none'
            
            # Clean extracted text
            text = text.strip()
            
            # Create chunks
            chunks = []
            if text:
                chunks = [{
                    'index': 0,
                    'content': text,
                    'section': 'OCR_extracted',
                    'tokens': len(text.split()),
                }]
            
            # Extract facts
            facts = self._extract_facts(text) if text else {}
            
            return ProcessedDocument(
                content=text,
                chunks=chunks,
                facts=facts,
                metadata=metadata,
                processing_time_ms=0,
                tokens_count=len(text.split()) if text else 0
            )
            
        except Exception as e:
            logger.error(f"Error processing image: {str(e)}")
            raise
    
    def _preprocess_image(self, image: Image) -> Image:
        """Preprocess image for better OCR results"""
        # Convert to grayscale
        if image.mode != 'L':
            image = image.convert('L')
        
        # Resize if too small
        if image.width < 1000:
            ratio = 1000 / image.width
            new_size = (int(image.width * ratio), int(image.height * ratio))
            image = image.resize(new_size, Image.Resampling.LANCZOS)
        
        return image
    
    def _extract_facts(self, text: str) -> Dict[str, Any]:
        """Extract facts from OCR text"""
        import re
        facts = {}
        
        # Phone numbers
        phones = re.findall(r'[\+]?[(]?[0-9]{3}[)]?[-\s\.]?[(]?[0-9]{3}[)]?[-\s\.]?[0-9]{4,6}', text)
        if phones:
            facts['phone_numbers'] = list(set(phones))[:5]
        
        # Emails
        emails = re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', text)
        if emails:
            facts['emails'] = list(set(emails))[:5]
        
        return facts
    
    async def _process_fallback(self, input_data: InputData) -> ProcessedDocument:
        """Fallback when dependencies are not available"""
        return ProcessedDocument(
            content="Image processing requires PIL and pytesseract",
            chunks=[],
            facts={},
            metadata={'processor': 'fallback'},
            processing_time_ms=0,
            tokens_count=0
        )