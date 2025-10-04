# clinics/backend/app/api/enhanced_knowledge_ingestion.py
"""Enhanced Knowledge Ingestion Pipeline with Multimodal Processing"""

import hashlib
import os
import json
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from io import BytesIO

from langchain.text_splitter import RecursiveCharacterTextSplitter
from pinecone import Pinecone, ServerlessSpec
from openai import OpenAI

# Import processors
from app.knowledge.processors.multimodal_pdf_processor import MultimodalPDFProcessor
from app.knowledge.processors.text_processor import TextProcessor
from app.knowledge.router import InputData, ProcessedDocument

logger = logging.getLogger(__name__)


class EnhancedKnowledgeIngestionPipeline:
    """Enhanced pipeline with multimodal document processing capabilities"""
    
    def __init__(self, clinic_id: str):
        self.clinic_id = clinic_id
        # Ensure index name is lowercase with hyphens only and under 45 chars
        safe_clinic_id = clinic_id.lower().replace('_', '-').replace(' ', '-')[:8]
        self.index_name = f"clinic-{safe_clinic_id}-kb"
        
        # Initialize processors
        self.pdf_processor = MultimodalPDFProcessor()
        self.text_processor = TextProcessor()
        
        # OpenAI client for embeddings
        self.openai = OpenAI()
        
        # Text splitter for additional processing if needed
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
        
        logger.info(f"Initializing Pinecone for index: {self.index_name}")
        
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
            logger.info(f"Created new Pinecone index: {self.index_name}")
        
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
                # Ensure list contains only strings
                if all(isinstance(v, str) for v in value):
                    cleaned[key] = value
                else:
                    cleaned[key] = [str(v) for v in value]
            elif isinstance(value, dict):
                # Convert dict to JSON string
                cleaned[key] = json.dumps(value)
            else:
                # Convert other types to string
                cleaned[key] = str(value)
        
        return cleaned
    
    async def ingest_document(
        self,
        content: bytes,
        filename: str,
        mime_type: str,
        metadata: Dict[str, Any],
        category: str = "general"
    ) -> Dict[str, Any]:
        """
        Ingest a document with multimodal processing
        
        Args:
            content: Raw document bytes
            filename: Original filename
            mime_type: MIME type of the document
            metadata: Additional metadata
            category: Document category
            
        Returns:
            Dict with ingestion results
        """
        
        try:
            # Prepare input data for processor
            input_data = InputData(
                content=content,
                filename=filename,
                mime_type=mime_type,
                metadata={
                    **metadata,
                    'clinic_id': self.clinic_id,
                    'clinic_name': metadata.get('clinic_name', 'Unknown Clinic'),
                    'category': category
                }
            )
            
            # Select appropriate processor
            processed_doc = None
            if mime_type == 'application/pdf':
                logger.info(f"Processing PDF with multimodal AI: {filename}")
                processed_doc = await self.pdf_processor.process(input_data)
            elif mime_type.startswith('text/'):
                logger.info(f"Processing text document: {filename}")
                processed_doc = await self.text_processor.process(input_data)
            else:
                logger.warning(f"Unsupported mime type: {mime_type}, attempting text processing")
                processed_doc = await self.text_processor.process(input_data)
            
            if not processed_doc or not processed_doc.chunks:
                logger.warning(f"No content extracted from document: {filename}")
                return {
                    "status": "failed",
                    "error": "No content could be extracted",
                    "filename": filename
                }
            
            # Generate document ID based on content hash
            doc_id = hashlib.md5(
                f"{processed_doc.content[:500]}{category}{self.clinic_id}".encode()
            ).hexdigest()
            
            # Check if document already exists
            existing = self.index.fetch(ids=[f"{doc_id}_0"])
            if existing.vectors:
                logger.info(f"Document already indexed: {doc_id}")
                return {
                    "status": "already_indexed",
                    "doc_id": doc_id,
                    "filename": filename
                }
            
            # Store document in database
            db_doc_id = await self._store_document_in_db(
                processed_doc,
                filename,
                category,
                doc_id
            )
            
            # Index chunks in Pinecone
            vectors = []
            for i, chunk in enumerate(processed_doc.chunks):
                chunk_id = f"{doc_id}_{i}"
                
                # Get chunk text
                chunk_text = chunk.get('text', chunk.get('content', ''))
                if not chunk_text:
                    continue
                
                # Generate embedding
                embedding_response = self.openai.embeddings.create(
                    model="text-embedding-3-small",
                    input=chunk_text
                )
                embedding = embedding_response.data[0].embedding
                
                # Prepare metadata
                chunk_metadata = self._clean_metadata_for_pinecone({
                    **metadata,
                    **chunk.get('metadata', {}),
                    'category': category,
                    'chunk_index': i,
                    'total_chunks': len(processed_doc.chunks),
                    'chunk_type': chunk.get('metadata', {}).get('chunk_type', 'text'),
                    'text': chunk_text[:1000],  # Store preview
                    'doc_id': doc_id,
                    'db_id': str(db_doc_id) if db_doc_id else None,
                    'clinic_id': self.clinic_id,
                    'filename': filename,
                    'indexed_at': datetime.utcnow().isoformat(),
                    'processor': processed_doc.metadata.get('processor', 'unknown'),
                    'extraction_method': processed_doc.metadata.get('extraction_method', 'text')
                })
                
                vectors.append({
                    'id': chunk_id,
                    'values': embedding,
                    'metadata': chunk_metadata
                })
            
            # Batch upsert to Pinecone
            if vectors:
                self.index.upsert(vectors=vectors)
                logger.info(f"Successfully indexed {len(vectors)} chunks for document: {filename}")
            
            return {
                "status": "indexed",
                "doc_id": doc_id,
                "db_id": db_doc_id,
                "chunks": len(vectors),
                "category": category,
                "filename": filename,
                "extraction_details": {
                    "processor": processed_doc.metadata.get('processor'),
                    "model_used": processed_doc.metadata.get('model_used'),
                    "tables_found": processed_doc.metadata.get('tables_found', 0),
                    "images_found": processed_doc.metadata.get('images_found', 0),
                    "processing_time_ms": processed_doc.processing_time_ms
                }
            }
            
        except Exception as e:
            logger.error(f"Error ingesting document {filename}: {e}")
            return {
                "status": "failed",
                "error": str(e),
                "filename": filename
            }
    
    async def _store_document_in_db(
        self,
        processed_doc: ProcessedDocument,
        filename: str,
        category: str,
        doc_id: str
    ) -> Optional[int]:
        """Store processed document in database"""
        
        try:
            from app.database import get_db_connection
            
            # Calculate cost (rough estimate)
            cost_credits = len(processed_doc.chunks) * 0.00002
            
            # Extract facts if available
            facts = processed_doc.facts if processed_doc.facts else {}
            fact_count = len(facts) if isinstance(facts, dict) else 0
            
            async with get_db_connection() as conn:
                result = await conn.fetchrow("""
                    INSERT INTO knowledge_documents (
                        clinic_id, source_type, source_url, source_filename,
                        category, title, raw_content, processed_content,
                        chunk_count, fact_count, cost_credits, metadata, tags
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                    RETURNING id
                """, 
                    self.clinic_id,
                    'file',
                    '',
                    filename,
                    category,
                    filename,
                    processed_doc.content[:5000],  # Store first 5000 chars
                    processed_doc.content[:10000],  # Store first 10000 chars
                    len(processed_doc.chunks),
                    fact_count,
                    cost_credits,
                    json.dumps({
                        **processed_doc.metadata,
                        'facts': facts,
                        'doc_id': doc_id
                    }),
                    []  # Tags can be added later
                )
                
                return result['id'] if result else None
                
        except Exception as e:
            logger.error(f"Error storing document in database: {e}")
            # Continue without database storage
            return None
    
    async def ingest_from_url(
        self,
        url: str,
        category: str = "general",
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Ingest content from a URL (for website parsing)
        
        Args:
            url: URL to fetch and process
            category: Document category
            metadata: Additional metadata
            
        Returns:
            Dict with ingestion results
        """
        
        try:
            import httpx
            from app.knowledge.web_crawler import EnhancedWebCrawler
            
            # Use the enhanced web crawler for comprehensive extraction
            crawler = EnhancedWebCrawler(max_depth=2, max_pages=10)
            crawl_result = await crawler.crawl(url)
            
            # Process the structured data
            structured_data = crawl_result.get('structured_data', {})
            
            # Combine all relevant text
            combined_text = []
            
            # Add main content
            for page in crawl_result.get('raw_pages', []):
                if page.get('content'):
                    combined_text.append(page['content'])
            
            # Add services
            if structured_data.get('services'):
                combined_text.append("\n\nServices Offered:\n" + 
                                   "\n".join(f"• {s}" for s in structured_data['services']))
            
            # Add FAQs
            if structured_data.get('faqs'):
                faq_text = "\n\nFrequently Asked Questions:\n"
                for faq in structured_data['faqs']:
                    faq_text += f"\nQ: {faq.get('question', '')}\nA: {faq.get('answer', '')}\n"
                combined_text.append(faq_text)
            
            # Add team info
            if structured_data.get('team'):
                team_text = "\n\nOur Team:\n"
                for member in structured_data['team']:
                    team_text += f"• {member.get('name', '')} - {member.get('role', '')}\n"
                combined_text.append(team_text)
            
            # Add contact info
            if structured_data.get('contact'):
                contact_text = "\n\nContact Information:\n"
                if structured_data['contact'].get('phones'):
                    contact_text += f"Phone: {', '.join(structured_data['contact']['phones'])}\n"
                if structured_data['contact'].get('emails'):
                    contact_text += f"Email: {', '.join(structured_data['contact']['emails'])}\n"
                if structured_data['contact'].get('address'):
                    contact_text += f"Address: {structured_data['contact']['address']}\n"
                combined_text.append(contact_text)
            
            # Add business hours
            if structured_data.get('hours'):
                hours_text = "\n\nBusiness Hours:\n"
                for day, hours in structured_data['hours'].items():
                    hours_text += f"{day}: {hours}\n"
                combined_text.append(hours_text)
            
            full_content = "\n".join(combined_text)
            
            if not full_content.strip():
                return {
                    "status": "failed",
                    "error": "No content could be extracted from URL",
                    "url": url
                }
            
            # Create chunks
            chunks = self.text_splitter.split_text(full_content)
            
            # Generate document ID
            doc_id = hashlib.md5(
                f"{full_content[:500]}{category}{self.clinic_id}".encode()
            ).hexdigest()
            
            # Check if already indexed
            existing = self.index.fetch(ids=[f"{doc_id}_0"])
            if existing.vectors:
                return {
                    "status": "already_indexed",
                    "doc_id": doc_id,
                    "url": url
                }
            
            # Index chunks
            vectors = []
            for i, chunk in enumerate(chunks):
                chunk_id = f"{doc_id}_{i}"
                
                # Generate embedding
                embedding_response = self.openai.embeddings.create(
                    model="text-embedding-3-small",
                    input=chunk
                )
                embedding = embedding_response.data[0].embedding
                
                # Prepare metadata
                chunk_metadata = self._clean_metadata_for_pinecone({
                    **(metadata or {}),
                    'category': category,
                    'chunk_index': i,
                    'total_chunks': len(chunks),
                    'text': chunk[:1000],
                    'doc_id': doc_id,
                    'clinic_id': self.clinic_id,
                    'source_url': url,
                    'indexed_at': datetime.utcnow().isoformat(),
                    'processor': 'web_crawler',
                    'pages_crawled': crawl_result.get('pages_crawled', 1)
                })
                
                vectors.append({
                    'id': chunk_id,
                    'values': embedding,
                    'metadata': chunk_metadata
                })
            
            # Upsert to Pinecone
            if vectors:
                self.index.upsert(vectors=vectors)
                logger.info(f"Successfully indexed {len(vectors)} chunks from URL: {url}")
            
            return {
                "status": "indexed",
                "doc_id": doc_id,
                "chunks": len(vectors),
                "category": category,
                "url": url,
                "pages_crawled": crawl_result.get('pages_crawled', 1),
                "extraction_summary": {
                    "services_found": len(structured_data.get('services', [])),
                    "faqs_found": len(structured_data.get('faqs', [])),
                    "team_members_found": len(structured_data.get('team', [])),
                    "has_contact_info": bool(structured_data.get('contact')),
                    "has_business_hours": bool(structured_data.get('hours'))
                }
            }
            
        except Exception as e:
            logger.error(f"Error ingesting from URL {url}: {e}")
            return {
                "status": "failed",
                "error": str(e),
                "url": url
            }
    
    async def update_document(
        self,
        doc_id: str,
        new_content: bytes,
        filename: str,
        mime_type: str,
        metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Update an existing document with new content"""
        
        # Delete old chunks
        chunk_ids = [f"{doc_id}_{i}" for i in range(200)]  # Support up to 200 chunks
        self.index.delete(ids=chunk_ids)
        
        # Re-ingest with new content
        return await self.ingest_document(
            new_content,
            filename,
            mime_type,
            metadata,
            metadata.get('category', 'general')
        )
    
    async def delete_document(self, doc_id: str) -> Dict[str, Any]:
        """Delete a document and all its chunks"""
        
        # Delete all chunks for this document
        chunk_ids = [f"{doc_id}_{i}" for i in range(200)]  # Support up to 200 chunks
        self.index.delete(ids=chunk_ids)
        
        logger.info(f"Deleted document: {doc_id}")
        
        return {"status": "deleted", "doc_id": doc_id}