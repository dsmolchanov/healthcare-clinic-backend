"""
HTML Document Processor
"""

import logging
from typing import Dict, Any, List
import re
from html.parser import HTMLParser

from ..router import BaseProcessor, InputData, ProcessedDocument

logger = logging.getLogger(__name__)

class HTMLTextExtractor(HTMLParser):
    """Extract text from HTML"""
    
    def __init__(self):
        super().__init__()
        self.text = []
        self.in_script = False
        self.in_style = False
    
    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.in_script = True
        elif tag == 'style':
            self.in_style = True
        elif tag in ['p', 'br', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            self.text.append('\n')
    
    def handle_endtag(self, tag):
        if tag == 'script':
            self.in_script = False
        elif tag == 'style':
            self.in_style = False
    
    def handle_data(self, data):
        if not self.in_script and not self.in_style:
            self.text.append(data)
    
    def get_text(self):
        return ''.join(self.text)

class HTMLProcessor(BaseProcessor):
    """Process HTML documents"""
    
    supported_mime_types = ['text/html', 'application/xhtml+xml']
    
    def can_process(self, mime_type: str) -> bool:
        return mime_type in self.supported_mime_types
    
    async def process(self, input_data: InputData) -> ProcessedDocument:
        """Process HTML document"""
        try:
            # Get HTML content
            if isinstance(input_data.content, bytes):
                html = input_data.content.decode('utf-8', errors='ignore')
            else:
                html = input_data.content
            
            # Extract text
            parser = HTMLTextExtractor()
            parser.feed(html)
            text = parser.get_text()
            
            # Clean text
            text = re.sub(r'\n{3,}', '\n\n', text)
            text = re.sub(r' {2,}', ' ', text)
            text = text.strip()
            
            # Extract metadata
            metadata = self._extract_metadata(html)
            
            # Create chunks
            chunks = self._create_chunks(text)
            
            # Extract facts
            facts = self._extract_facts(text, html)
            
            return ProcessedDocument(
                content=text,
                chunks=chunks,
                facts=facts,
                metadata=metadata,
                processing_time_ms=0,
                tokens_count=int(len(text.split()) * 1.3)
            )
            
        except Exception as e:
            logger.error(f"Error processing HTML: {str(e)}")
            raise
    
    def _extract_metadata(self, html: str) -> Dict[str, Any]:
        """Extract metadata from HTML"""
        metadata = {}
        
        # Extract title
        title_match = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
        if title_match:
            metadata['title'] = title_match.group(1).strip()
        
        # Extract meta tags
        meta_tags = re.findall(r'<meta\s+([^>]+)>', html, re.IGNORECASE)
        for tag in meta_tags:
            name_match = re.search(r'name=["\']([^"\']+)["\']', tag)
            content_match = re.search(r'content=["\']([^"\']+)["\']', tag)
            if name_match and content_match:
                metadata[f'meta_{name_match.group(1)}'] = content_match.group(1)
        
        return metadata
    
    def _create_chunks(self, text: str) -> List[Dict[str, Any]]:
        """Create chunks from text"""
        chunks = []
        words = text.split()
        chunk_size = 500
        overlap = 50
        
        for i in range(0, len(words), chunk_size - overlap):
            chunk_words = words[i:i + chunk_size]
            chunks.append({
                'index': len(chunks),
                'content': ' '.join(chunk_words),
                'section': '',
                'tokens': len(chunk_words),
            })
        
        return chunks
    
    def _extract_facts(self, text: str, html: str) -> Dict[str, Any]:
        """Extract facts from HTML"""
        facts = {}
        
        # Extract links
        links = re.findall(r'href=["\']([^"\']+)["\']', html)
        if links:
            facts['links'] = list(set(links))[:10]
        
        # Extract images
        images = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html)
        if images:
            facts['images'] = list(set(images))[:10]
        
        # Extract structured data (JSON-LD)
        json_ld = re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
        if json_ld:
            facts['structured_data'] = True
        
        return facts