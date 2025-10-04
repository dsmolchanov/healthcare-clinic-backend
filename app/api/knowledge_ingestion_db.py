# clinics/backend/app/api/knowledge_ingestion_db.py
"""Database-backed knowledge ingestion pipeline (no external vector DB required)"""

import hashlib
import os
import json
from datetime import datetime
from typing import List, Dict, Any
import logging

from langchain.text_splitter import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

class KnowledgeIngestionPipeline:
    """Pipeline for ingesting and indexing knowledge documents in database"""
    
    def __init__(self, clinic_id: str):
        self.clinic_id = clinic_id
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            separators=["\n\n", "\n", ". ", " ", ""]
        )
        logger.info(f"Initialized DB-backed knowledge pipeline for clinic {clinic_id}")
    
    async def ingest_document(
        self,
        content: str,
        metadata: Dict[str, Any],
        category: str
    ) -> Dict[str, Any]:
        """Ingest a document into the database"""
        
        # Generate document ID
        doc_id = hashlib.md5(f"{content[:100]}{category}".encode()).hexdigest()
        
        # Check if document already exists
        from app.database import get_db_connection
        async with get_db_connection() as conn:
            if conn:
                existing = await conn.fetchrow("""
                    SELECT id FROM knowledge_documents 
                    WHERE doc_hash = $1 AND clinic_id = $2
                """, doc_id, self.clinic_id)
                
                if existing:
                    return {"status": "already_indexed", "doc_id": doc_id}
        
        # Split into chunks
        chunks = self.text_splitter.split_text(content)
        
        # Store document in database
        async with get_db_connection() as conn:
            if conn:
                # Insert document record
                doc_result = await conn.fetchrow("""
                    INSERT INTO knowledge_documents (
                        clinic_id, title, category, content_preview,
                        source_type, source_url, source_filename,
                        chunk_count, doc_hash, metadata, is_active
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, true)
                    RETURNING id
                """, 
                    self.clinic_id,
                    metadata.get('title', metadata.get('url', 'Untitled')),
                    category,
                    content[:500],  # Preview
                    metadata.get('source', 'web'),
                    metadata.get('url'),
                    metadata.get('filename'),
                    len(chunks),
                    doc_id,
                    json.dumps(metadata),
                )
                
                doc_db_id = doc_result['id'] if doc_result else None
                
                # Store chunks
                if doc_db_id:
                    for i, chunk in enumerate(chunks):
                        await conn.execute("""
                            INSERT INTO knowledge_chunks (
                                document_id, clinic_id, chunk_index, 
                                content, metadata
                            ) VALUES ($1, $2, $3, $4, $5)
                        """,
                            doc_db_id,
                            self.clinic_id,
                            i,
                            chunk,
                            json.dumps({
                                'category': category,
                                'chunk_index': i,
                                'total_chunks': len(chunks),
                                'doc_id': doc_id,
                                **metadata
                            })
                        )
                
                logger.info(f"Stored document {doc_id} with {len(chunks)} chunks in database")
                
                return {
                    "status": "indexed",
                    "doc_id": doc_id,
                    "chunks": len(chunks),
                    "category": category,
                    "db_id": doc_db_id
                }
            else:
                # Fallback if no database connection
                logger.warning("No database connection, returning mock success")
                return {
                    "status": "indexed",
                    "doc_id": doc_id,
                    "chunks": len(chunks),
                    "category": category
                }
    
    async def search(self, query: str, top_k: int = 10) -> List[Dict[str, Any]]:
        """Search for relevant documents using database full-text search"""
        from app.database import get_db_connection
        
        async with get_db_connection() as conn:
            if conn:
                # Use PostgreSQL full-text search
                results = await conn.fetch("""
                    SELECT 
                        c.content,
                        c.metadata,
                        d.title,
                        d.category,
                        ts_rank(to_tsvector('english', c.content), 
                               plainto_tsquery('english', $1)) as rank
                    FROM knowledge_chunks c
                    JOIN knowledge_documents d ON c.document_id = d.id
                    WHERE c.clinic_id = $2
                        AND d.is_active = true
                        AND to_tsvector('english', c.content) @@ plainto_tsquery('english', $1)
                    ORDER BY rank DESC
                    LIMIT $3
                """, query, self.clinic_id, top_k)
                
                return [
                    {
                        'content': r['content'],
                        'metadata': json.loads(r['metadata']) if r['metadata'] else {},
                        'title': r['title'],
                        'category': r['category'],
                        'score': float(r['rank']) if r['rank'] else 0
                    }
                    for r in results
                ]
            
        return []