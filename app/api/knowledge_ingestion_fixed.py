# clinics/backend/app/api/knowledge_ingestion_fixed.py
"""Fixed Knowledge Ingestion Pipeline with proper metadata handling"""

import hashlib
import os
import json
import logging
from datetime import datetime
from typing import List, Dict, Any

from langchain.text_splitter import RecursiveCharacterTextSplitter
from pinecone import Pinecone, ServerlessSpec
from openai import OpenAI


class FixedKnowledgeIngestionPipeline:
    """Fixed pipeline with proper title and source type handling"""
    
    def __init__(self, clinic_id: str):
        self.clinic_id = clinic_id
        # Ensure index name is lowercase with hyphens only and under 45 chars
        safe_clinic_id = clinic_id.lower().replace('_', '-').replace(' ', '-')[:8]
        self.index_name = f"clinic-{safe_clinic_id}-kb"
        self.openai = OpenAI()
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            separators=["\n\n", "\n", ". ", " ", ""]
        )
        self._init_pinecone()
    
    def _init_pinecone(self):
        """Initialize Pinecone with existing configuration"""
        api_key = os.environ.get('PINECONE_API_KEY')
        if not api_key:
            raise ValueError("PINECONE_API_KEY environment variable not set")
        
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Initializing Pinecone with API key starting with: {api_key[:10]}...")
        
        self.pc = Pinecone(api_key=api_key)
        
        existing_indexes = [index.name for index in self.pc.list_indexes()]
        if self.index_name not in existing_indexes:
            self.pc.create_index(
                name=self.index_name,
                dimension=1536,
                metric='cosine',
                spec=ServerlessSpec(
                    cloud='aws',
                    region='us-east-1'
                )
            )
        
        self.index = self.pc.Index(self.index_name)
    
    def _clean_metadata_for_pinecone(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Clean metadata to ensure Pinecone compatibility"""
        cleaned = {}
        for key, value in metadata.items():
            if value is None:
                continue
            elif isinstance(value, (str, int, float, bool)):
                cleaned[key] = value
            elif isinstance(value, list):
                if all(isinstance(v, str) for v in value):
                    cleaned[key] = value
                else:
                    cleaned[key] = [str(v) for v in value]
            elif isinstance(value, dict):
                cleaned[key] = json.dumps(value)
            else:
                cleaned[key] = str(value)
        
        return cleaned
    
    def _extract_title_from_content(self, content: str, metadata: Dict[str, Any]) -> str:
        """Extract a meaningful title from the document content or metadata"""
        
        # First, check if filename is provided and use it as title
        filename = metadata.get('filename', '')
        if filename:
            # Remove extension and clean up filename
            import os
            title = os.path.splitext(filename)[0]
            # Replace underscores and hyphens with spaces
            title = title.replace('_', ' ').replace('-', ' ')
            # Capitalize words
            title = ' '.join(word.capitalize() for word in title.split())
            if title and title != 'Untitled':
                return title
        
        # Try to extract from content (first line or heading)
        if content:
            lines = content.strip().split('\n')
            for line in lines[:5]:  # Check first 5 lines
                line = line.strip()
                if line and len(line) < 100:  # Reasonable title length
                    # Clean up the line
                    if line.startswith('#'):  # Markdown heading
                        line = line.lstrip('#').strip()
                    if line and not line.startswith('---'):  # Not a separator
                        return line
        
        # If still no title, generate from category and date
        category = metadata.get('category', 'Document')
        date_str = datetime.now().strftime("%Y-%m-%d")
        return f"{category.capitalize()} - {date_str}"
    
    def _determine_source_type(self, metadata: Dict[str, Any]) -> str:
        """Determine the correct source type from metadata"""
        
        # Check explicit source
        if 'source' in metadata:
            return metadata['source']
        
        # Check if it's a file upload
        if 'filename' in metadata and metadata['filename']:
            return 'file'
        
        # Check if it's from web crawling
        if 'url' in metadata and metadata['url']:
            return 'url'
        
        # Check if it's manual entry
        if metadata.get('source') == 'manual_entry':
            return 'manual'
        
        # Default to file if mime_type is present
        if 'mime_type' in metadata:
            return 'file'
        
        return 'unknown'
    
    async def ingest_document(
        self,
        content: str,
        metadata: Dict[str, Any],
        category: str
    ) -> Dict[str, Any]:
        """Ingest a document with proper metadata handling"""
        
        # Determine source type correctly
        source_type = self._determine_source_type(metadata)
        
        # Extract or generate a meaningful title
        title = metadata.get('title', '')
        if not title or title == 'Untitled':
            title = self._extract_title_from_content(content, metadata)
        
        # Generate document ID
        doc_id = hashlib.md5(f"{content[:100]}{category}".encode()).hexdigest()
        
        # Check if document already exists
        existing = self.index.fetch(ids=[doc_id])
        if existing.vectors:
            return {
                "status": "already_indexed",
                "doc_id": doc_id,
                "title": title,
                "source_type": source_type
            }
        
        # Split into chunks
        chunks = self.text_splitter.split_text(content)
        
        # Calculate cost (rough estimate: $0.02 per 1000 embeddings)
        cost_credits = len(chunks) * 0.00002
        
        # Store document in database
        from app.database import get_db_connection
        
        db_doc_id = None
        async with get_db_connection() as conn:
            # Insert into knowledge_documents table with proper metadata
            result = await conn.fetchrow("""
                INSERT INTO knowledge_documents (
                    clinic_id, source_type, source_url, source_filename,
                    category, title, raw_content, processed_content,
                    chunk_count, fact_count, cost_credits, metadata, tags,
                    processed_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                RETURNING id
            """, 
                self.clinic_id,
                source_type,  # Use determined source type
                metadata.get('url', ''),
                metadata.get('filename', ''),
                category,
                title,  # Use extracted/generated title
                content[:5000],  # Store first 5000 chars of raw content
                content[:10000],  # Store first 10000 chars of processed content
                len(chunks),
                metadata.get('fact_count', 0),
                cost_credits,
                json.dumps({
                    **metadata,
                    'original_title': metadata.get('title', ''),
                    'extracted_title': title,
                    'source_type': source_type,
                    'job_id': metadata.get('job_id', '')
                }),
                metadata.get('tags', []),
                datetime.utcnow()  # Add processed_at timestamp
            )
            db_doc_id = result['id'] if result else None
        
        # Process each chunk for Pinecone
        vectors = []
        for i, chunk in enumerate(chunks):
            chunk_id = f"{doc_id}_{i}"
            
            # Generate embedding
            embedding = self.openai.embeddings.create(
                model="text-embedding-3-small",
                input=chunk
            ).data[0].embedding
            
            # Prepare vector with metadata
            vectors.append({
                'id': chunk_id,
                'values': embedding,
                'metadata': self._clean_metadata_for_pinecone({
                    **metadata,
                    'category': category,
                    'chunk_index': i,
                    'total_chunks': len(chunks),
                    'text': chunk[:1000],  # Store preview
                    'doc_id': doc_id,
                    'db_id': str(db_doc_id) if db_doc_id else None,
                    'clinic_id': self.clinic_id,
                    'title': title,  # Include title in chunk metadata
                    'source_type': source_type,  # Include source type
                    'indexed_at': datetime.utcnow().isoformat()
                })
            })
        
        # Batch upsert to Pinecone
        self.index.upsert(vectors=vectors)
        
        # Log successful ingestion
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Successfully indexed document: {title} ({source_type}) with {len(chunks)} chunks")
        
        return {
            "status": "indexed",
            "doc_id": doc_id,
            "db_id": db_doc_id,
            "chunks": len(chunks),
            "category": category,
            "title": title,
            "source_type": source_type,
            "cost": cost_credits
        }
    
    async def update_document(
        self,
        doc_id: str,
        new_content: str,
        metadata: Dict[str, Any]
    ):
        """Incremental update of existing document"""
        
        # Delete old chunks
        chunk_ids = [f"{doc_id}_{i}" for i in range(100)]  # Max 100 chunks
        self.index.delete(ids=chunk_ids)
        
        # Re-ingest with new content
        return await self.ingest_document(
            new_content,
            metadata,
            metadata.get('category', 'general')
        )
    
    async def delete_document(self, doc_id: str):
        """Delete a document and all its chunks"""
        
        # Delete all chunks for this document
        chunk_ids = [f"{doc_id}_{i}" for i in range(100)]  # Max 100 chunks
        self.index.delete(ids=chunk_ids)
        
        return {"status": "deleted", "doc_id": doc_id}
    
    async def list_categories(self) -> List[str]:
        """List all unique categories in the knowledge base"""
        
        # Query a sample of vectors to get categories
        sample_results = self.index.query(
            vector=[0.0] * 1536,  # Dummy vector
            top_k=100,
            include_metadata=True
        )
        
        categories = set()
        for match in sample_results.matches:
            if 'category' in match.metadata:
                categories.add(match.metadata['category'])
        
        return sorted(list(categories))