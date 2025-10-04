"""
Intelligent Chunker - Smart chunking that preserves context
"""

import re
import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
import hashlib

try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False
    logging.warning("tiktoken not available. Install with: pip install tiktoken")

logger = logging.getLogger(__name__)

@dataclass
class Chunk:
    """Represents a document chunk"""
    index: int
    content: str
    section: Optional[str]
    subsection: Optional[str]
    tokens: int
    start_char: int
    end_char: int
    metadata: Dict[str, Any]
    embedding_id: Optional[str] = None
    content_hash: Optional[str] = None

class IntelligentChunker:
    """Smart chunking that preserves context and semantic boundaries"""
    
    def __init__(
        self,
        max_tokens: int = 800,
        overlap_tokens: int = 150,
        preserve_sections: bool = True,
        min_chunk_tokens: int = 100
    ):
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens
        self.preserve_sections = preserve_sections
        self.min_chunk_tokens = min_chunk_tokens
        
        # Initialize tokenizer
        if TIKTOKEN_AVAILABLE:
            try:
                self.tokenizer = tiktoken.get_encoding("cl100k_base")
            except:
                self.tokenizer = None
                logger.warning("Failed to initialize tiktoken encoder")
        else:
            self.tokenizer = None
    
    async def chunk(self, document: Any, structured_content: List[Dict] = None) -> List[Chunk]:
        """Create intelligent chunks from document"""
        if structured_content:
            # Use structured content for better chunking
            return await self._chunk_structured(structured_content, document)
        else:
            # Fall back to text-based chunking
            return await self._chunk_text(document.content if hasattr(document, 'content') else str(document))
    
    async def _chunk_structured(self, structured_content: List[Dict], document: Any) -> List[Chunk]:
        """Chunk based on document structure"""
        chunks = []
        current_section = None
        current_subsection = None
        section_buffer = []
        section_start_char = 0
        
        for item in structured_content:
            # Detect section changes
            if self._is_section_header(item):
                # Process buffered content
                if section_buffer:
                    section_chunks = await self._process_section_buffer(
                        section_buffer,
                        current_section,
                        current_subsection,
                        section_start_char,
                        len(chunks)
                    )
                    chunks.extend(section_chunks)
                    section_buffer = []
                
                # Update section info
                current_section = item.get('text', item.get('title', ''))
                current_subsection = None
                section_start_char = item.get('position', section_start_char)
            
            elif self._is_subsection_header(item):
                current_subsection = item.get('text', item.get('title', ''))
            
            else:
                # Add content to buffer
                section_buffer.append(item)
        
        # Process remaining buffer
        if section_buffer:
            section_chunks = await self._process_section_buffer(
                section_buffer,
                current_section,
                current_subsection,
                section_start_char,
                len(chunks)
            )
            chunks.extend(section_chunks)
        
        # If no chunks created, fall back to text chunking
        if not chunks and hasattr(document, 'content'):
            chunks = await self._chunk_text(document.content)
        
        return chunks
    
    async def _chunk_text(self, text: str) -> List[Chunk]:
        """Chunk plain text intelligently"""
        chunks = []
        
        # Split into sentences for better boundary detection
        sentences = self._split_into_sentences(text)
        
        current_chunk = []
        current_tokens = 0
        chunk_start_char = 0
        chunk_index = 0
        
        for sent_idx, sentence in enumerate(sentences):
            sent_tokens = self._count_tokens(sentence)
            
            # Check if adding this sentence would exceed limit
            if current_tokens + sent_tokens > self.max_tokens and current_chunk:
                # Create chunk
                chunk_content = ' '.join(current_chunk)
                chunk_end_char = chunk_start_char + len(chunk_content)
                
                chunks.append(Chunk(
                    index=chunk_index,
                    content=chunk_content,
                    section=None,
                    subsection=None,
                    tokens=current_tokens,
                    start_char=chunk_start_char,
                    end_char=chunk_end_char,
                    metadata={'sentence_count': len(current_chunk)},
                    content_hash=self._generate_hash(chunk_content)
                ))
                
                # Prepare for next chunk with overlap
                overlap_sentences = self._calculate_overlap(current_chunk, self.overlap_tokens)
                current_chunk = overlap_sentences + [sentence]
                current_tokens = sum(self._count_tokens(s) for s in current_chunk)
                chunk_start_char = chunk_end_char - len(' '.join(overlap_sentences))
                chunk_index += 1
            else:
                current_chunk.append(sentence)
                current_tokens += sent_tokens
        
        # Add remaining content
        if current_chunk:
            chunk_content = ' '.join(current_chunk)
            chunks.append(Chunk(
                index=chunk_index,
                content=chunk_content,
                section=None,
                subsection=None,
                tokens=current_tokens,
                start_char=chunk_start_char,
                end_char=chunk_start_char + len(chunk_content),
                metadata={'sentence_count': len(current_chunk)},
                content_hash=self._generate_hash(chunk_content)
            ))
        
        return chunks
    
    async def _process_section_buffer(
        self,
        buffer: List[Dict],
        section: str,
        subsection: str,
        start_char: int,
        base_index: int
    ) -> List[Chunk]:
        """Process buffered content for a section"""
        chunks = []
        
        # Combine buffer content
        content_parts = []
        for item in buffer:
            text = item.get('text', item.get('content', ''))
            if text:
                content_parts.append(text)
        
        if not content_parts:
            return chunks
        
        full_content = ' '.join(content_parts)
        
        # Check if content fits in single chunk
        total_tokens = self._count_tokens(full_content)
        
        if total_tokens <= self.max_tokens:
            # Single chunk for this section
            chunks.append(Chunk(
                index=base_index,
                content=full_content,
                section=section,
                subsection=subsection,
                tokens=total_tokens,
                start_char=start_char,
                end_char=start_char + len(full_content),
                metadata={'is_complete_section': True},
                content_hash=self._generate_hash(full_content)
            ))
        else:
            # Split into multiple chunks
            sentences = self._split_into_sentences(full_content)
            section_chunks = await self._chunk_sentences_with_context(
                sentences,
                section,
                subsection,
                start_char,
                base_index
            )
            chunks.extend(section_chunks)
        
        return chunks
    
    async def _chunk_sentences_with_context(
        self,
        sentences: List[str],
        section: str,
        subsection: str,
        start_char: int,
        base_index: int
    ) -> List[Chunk]:
        """Chunk sentences while preserving context"""
        chunks = []
        current_chunk = []
        current_tokens = 0
        chunk_start = start_char
        
        for sentence in sentences:
            sent_tokens = self._count_tokens(sentence)
            
            if current_tokens + sent_tokens > self.max_tokens and current_chunk:
                # Create chunk
                chunk_content = ' '.join(current_chunk)
                chunks.append(Chunk(
                    index=base_index + len(chunks),
                    content=chunk_content,
                    section=section,
                    subsection=subsection,
                    tokens=current_tokens,
                    start_char=chunk_start,
                    end_char=chunk_start + len(chunk_content),
                    metadata={'part_of_section': True},
                    content_hash=self._generate_hash(chunk_content)
                ))
                
                # Overlap for context
                overlap_sentences = self._calculate_overlap(current_chunk, self.overlap_tokens)
                current_chunk = overlap_sentences + [sentence]
                current_tokens = sum(self._count_tokens(s) for s in current_chunk)
                chunk_start += len(chunk_content) - len(' '.join(overlap_sentences))
            else:
                current_chunk.append(sentence)
                current_tokens += sent_tokens
        
        # Add remaining
        if current_chunk:
            chunk_content = ' '.join(current_chunk)
            chunks.append(Chunk(
                index=base_index + len(chunks),
                content=chunk_content,
                section=section,
                subsection=subsection,
                tokens=current_tokens,
                start_char=chunk_start,
                end_char=chunk_start + len(chunk_content),
                metadata={'part_of_section': True},
                content_hash=self._generate_hash(chunk_content)
            ))
        
        return chunks
    
    def _split_into_sentences(self, text: str) -> List[str]:
        """Split text into sentences"""
        # Simple sentence splitter (can be enhanced with NLTK or spaCy)
        sentences = re.split(r'(?<=[.!?])\s+', text)
        
        # Handle edge cases
        processed_sentences = []
        for sentence in sentences:
            # Merge short fragments with previous sentence
            if processed_sentences and len(sentence) < 30:
                processed_sentences[-1] += ' ' + sentence
            else:
                processed_sentences.append(sentence)
        
        return processed_sentences
    
    def _count_tokens(self, text: str) -> int:
        """Count tokens in text"""
        if self.tokenizer:
            return len(self.tokenizer.encode(text))
        else:
            # Fallback: approximate with word count
            return int(len(text.split()) * 1.3)
    
    def _calculate_overlap(self, sentences: List[str], target_tokens: int) -> List[str]:
        """Calculate overlapping sentences for context"""
        overlap = []
        total_tokens = 0
        
        # Add sentences from the end until we reach target tokens
        for sentence in reversed(sentences):
            sent_tokens = self._count_tokens(sentence)
            if total_tokens + sent_tokens <= target_tokens:
                overlap.insert(0, sentence)
                total_tokens += sent_tokens
            else:
                break
        
        return overlap
    
    def _is_section_header(self, item: Dict) -> bool:
        """Check if item is a section header"""
        # Check various indicators
        if item.get('is_heading'):
            return True
        if item.get('style', '').lower().startswith('heading'):
            return True
        if item.get('font_size', 0) > 16:
            return True
        if item.get('level', 0) > 0 and item.get('level', 0) <= 3:
            return True
        return False
    
    def _is_subsection_header(self, item: Dict) -> bool:
        """Check if item is a subsection header"""
        if item.get('level', 0) >= 4:
            return True
        if item.get('style', '').lower() in ['heading 4', 'heading 5', 'heading 6']:
            return True
        return False
    
    def _generate_hash(self, content: str) -> str:
        """Generate hash for content deduplication"""
        return hashlib.md5(content.encode()).hexdigest()
    
    def merge_duplicate_chunks(self, chunks: List[Chunk]) -> List[Chunk]:
        """Remove duplicate chunks based on content hash"""
        seen_hashes = set()
        unique_chunks = []
        
        for chunk in chunks:
            if chunk.content_hash not in seen_hashes:
                seen_hashes.add(chunk.content_hash)
                unique_chunks.append(chunk)
        
        # Re-index chunks
        for i, chunk in enumerate(unique_chunks):
            chunk.index = i
        
        return unique_chunks
    
    def add_context_to_chunks(self, chunks: List[Chunk], document_metadata: Dict[str, Any]) -> List[Chunk]:
        """Add document-level context to each chunk"""
        for chunk in chunks:
            chunk.metadata.update({
                'document_title': document_metadata.get('title', ''),
                'document_category': document_metadata.get('category', ''),
                'document_source': document_metadata.get('source', ''),
                'chunk_position': f"{chunk.index + 1}/{len(chunks)}"
            })
        
        return chunks