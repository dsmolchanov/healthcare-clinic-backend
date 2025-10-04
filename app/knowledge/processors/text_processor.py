"""
Plain Text Document Processor
"""

import logging
from typing import Dict, Any, List
import re

from ..router import BaseProcessor, InputData, ProcessedDocument

logger = logging.getLogger(__name__)

class TextProcessor(BaseProcessor):
    """Process plain text documents"""
    
    supported_mime_types = ['text/plain', 'text/*']
    
    def can_process(self, mime_type: str) -> bool:
        """Check if this processor can handle the given mime type"""
        return mime_type in self.supported_mime_types or mime_type.startswith('text/')
    
    async def process(self, input_data: InputData) -> ProcessedDocument:
        """Process text document"""
        try:
            # Get text content
            if isinstance(input_data.content, bytes):
                # Try to decode with different encodings
                for encoding in ['utf-8', 'latin-1', 'cp1252']:
                    try:
                        text = input_data.content.decode(encoding)
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    text = input_data.content.decode('utf-8', errors='ignore')
            else:
                text = input_data.content
            
            # Clean and normalize text
            text = self._normalize_text(text)
            
            # Create chunks
            chunks = self._create_chunks(text)
            
            # Extract facts
            facts = self._extract_facts(text)
            
            # Count tokens
            tokens_count = len(text.split()) * 1.3
            
            return ProcessedDocument(
                content=text,
                chunks=chunks,
                facts=facts,
                metadata={
                    'encoding': 'utf-8',
                    'line_count': len(text.splitlines()),
                    'word_count': len(text.split()),
                    'char_count': len(text),
                },
                processing_time_ms=0,
                tokens_count=int(tokens_count)
            )
            
        except Exception as e:
            logger.error(f"Error processing text: {str(e)}")
            raise
    
    def _normalize_text(self, text: str) -> str:
        """Normalize text by cleaning up whitespace and special characters"""
        # Remove excessive whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r' {2,}', ' ', text)
        text = re.sub(r'\t+', ' ', text)
        
        # Remove null characters
        text = text.replace('\x00', '')
        
        # Trim lines
        lines = [line.strip() for line in text.splitlines()]
        text = '\n'.join(lines)
        
        return text.strip()
    
    def _create_chunks(self, text: str) -> List[Dict[str, Any]]:
        """Create chunks from text"""
        chunks = []
        
        # Split by paragraphs first
        paragraphs = text.split('\n\n')
        
        current_chunk = ""
        chunk_index = 0
        
        for para in paragraphs:
            if not para.strip():
                continue
            
            # Check if adding this paragraph would exceed chunk size
            potential_chunk = current_chunk + "\n\n" + para if current_chunk else para
            
            if len(potential_chunk.split()) > 500:
                # Save current chunk
                if current_chunk:
                    chunks.append({
                        'index': chunk_index,
                        'content': current_chunk.strip(),
                        'section': '',
                        'tokens': len(current_chunk.split()),
                    })
                    chunk_index += 1
                
                # Start new chunk with overlap
                words = current_chunk.split()
                overlap = ' '.join(words[-50:]) if len(words) > 50 else ''
                current_chunk = overlap + "\n\n" + para if overlap else para
            else:
                current_chunk = potential_chunk
        
        # Add remaining chunk
        if current_chunk:
            chunks.append({
                'index': chunk_index,
                'content': current_chunk.strip(),
                'section': '',
                'tokens': len(current_chunk.split()),
            })
        
        # If no chunks were created (text too small), create one chunk
        if not chunks and text.strip():
            chunks.append({
                'index': 0,
                'content': text.strip(),
                'section': '',
                'tokens': len(text.split()),
            })
        
        return chunks
    
    def _extract_facts(self, text: str) -> Dict[str, Any]:
        """Extract facts from text"""
        facts = {}
        
        # Phone numbers
        phones = re.findall(r'[\+]?[(]?[0-9]{3}[)]?[-\s\.]?[(]?[0-9]{3}[)]?[-\s\.]?[0-9]{4,6}', text)
        if phones:
            facts['phone_numbers'] = list(set(phones))[:5]
        
        # Emails
        emails = re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', text)
        if emails:
            facts['emails'] = list(set(emails))[:5]
        
        # URLs
        urls = re.findall(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', text)
        if urls:
            facts['urls'] = list(set(urls))[:5]
        
        # Simple date patterns
        dates = re.findall(r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? \d{4}\b', text, re.IGNORECASE)
        if dates:
            facts['dates'] = list(set(dates))[:5]
        
        # Time patterns
        times = re.findall(r'\b\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?\b', text)
        if times:
            facts['times'] = list(set(times))[:5]
        
        # Dollar amounts
        amounts = re.findall(r'\$[\d,]+\.?\d*', text)
        if amounts:
            facts['dollar_amounts'] = list(set(amounts))[:5]
        
        return facts