"""Markdown Document Processor"""

import re
import logging
from typing import Dict, Any, List

from ..router import BaseProcessor, InputData, ProcessedDocument

logger = logging.getLogger(__name__)

class MarkdownProcessor(BaseProcessor):
    """Process Markdown documents"""
    
    supported_mime_types = ['text/markdown', 'text/x-markdown']
    
    def can_process(self, mime_type: str) -> bool:
        return mime_type in self.supported_mime_types
    
    async def process(self, input_data: InputData) -> ProcessedDocument:
        """Process Markdown document"""
        try:
            # Get content
            if isinstance(input_data.content, bytes):
                text = input_data.content.decode('utf-8', errors='ignore')
            else:
                text = input_data.content
            
            # Extract structure
            sections = self._extract_sections(text)
            
            # Convert to plain text (remove markdown syntax)
            plain_text = self._markdown_to_text(text)
            
            # Create chunks based on sections
            chunks = self._create_chunks_from_sections(sections, plain_text)
            
            # Extract facts
            facts = self._extract_facts(text)
            
            return ProcessedDocument(
                content=plain_text,
                chunks=chunks,
                facts=facts,
                metadata={'format': 'markdown', 'section_count': len(sections)},
                processing_time_ms=0,
                tokens_count=int(len(plain_text.split()) * 1.3)
            )
        except Exception as e:
            logger.error(f"Error processing Markdown: {str(e)}")
            raise
    
    def _extract_sections(self, text: str) -> List[Dict[str, Any]]:
        """Extract sections from markdown"""
        sections = []
        lines = text.split('\n')
        current_section = {'level': 0, 'title': 'Document', 'content': []}
        
        for line in lines:
            # Check for headers
            header_match = re.match(r'^(#{1,6})\s+(.+)$', line)
            if header_match:
                # Save current section
                if current_section['content']:
                    current_section['content'] = '\n'.join(current_section['content'])
                    sections.append(current_section)
                
                # Start new section
                level = len(header_match.group(1))
                title = header_match.group(2)
                current_section = {'level': level, 'title': title, 'content': []}
            else:
                current_section['content'].append(line)
        
        # Add last section
        if current_section['content']:
            current_section['content'] = '\n'.join(current_section['content'])
            sections.append(current_section)
        
        return sections
    
    def _markdown_to_text(self, markdown: str) -> str:
        """Convert markdown to plain text"""
        # Remove code blocks
        text = re.sub(r'```[^`]*```', '', markdown, flags=re.MULTILINE)
        text = re.sub(r'`[^`]+`', '', text)
        
        # Remove headers markers
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        
        # Remove emphasis
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
        text = re.sub(r'\*([^*]+)\*', r'\1', text)
        text = re.sub(r'__([^_]+)__', r'\1', text)
        text = re.sub(r'_([^_]+)_', r'\1', text)
        
        # Remove links
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
        
        # Remove images
        text = re.sub(r'!\[([^\]]*)\]\([^)]+\)', '', text)
        
        # Remove list markers
        text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
        
        return text.strip()
    
    def _create_chunks_from_sections(self, sections: List[Dict], plain_text: str) -> List[Dict[str, Any]]:
        """Create chunks from sections"""
        chunks = []
        
        for i, section in enumerate(sections):
            content = self._markdown_to_text(section['content'])
            if content:
                chunks.append({
                    'index': i,
                    'content': content,
                    'section': section['title'],
                    'tokens': len(content.split()),
                })
        
        # If no sections or chunks, create from plain text
        if not chunks and plain_text:
            words = plain_text.split()
            chunk_size = 500
            for i in range(0, len(words), chunk_size - 50):
                chunk_words = words[i:i + chunk_size]
                chunks.append({
                    'index': len(chunks),
                    'content': ' '.join(chunk_words),
                    'section': '',
                    'tokens': len(chunk_words),
                })
        
        return chunks
    
    def _extract_facts(self, text: str) -> Dict[str, Any]:
        """Extract facts from markdown"""
        facts = {}
        
        # Extract links
        links = re.findall(r'\[([^\]]+)\]\(([^)]+)\)', text)
        if links:
            facts['links'] = [{'text': l[0], 'url': l[1]} for l in links[:10]]
        
        # Extract code language usage
        code_blocks = re.findall(r'```(\w+)', text)
        if code_blocks:
            facts['code_languages'] = list(set(code_blocks))
        
        return facts