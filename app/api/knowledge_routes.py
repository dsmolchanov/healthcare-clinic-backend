"""
Knowledge Management API Routes
"""

import os
import uuid
import json
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, BackgroundTasks, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Lazy imports to speed up startup
DocumentRouter = None
InputData = None
FactsExtractor = None
IntelligentChunker = None
DatabaseManager = None
KnowledgeIngestionPipeline = None

def lazy_import():
    """Lazy import heavy dependencies"""
    global DocumentRouter, InputData, FactsExtractor, IntelligentChunker, DatabaseManager, KnowledgeIngestionPipeline
    if DocumentRouter is None:
        from ..knowledge import DocumentRouter as DR, InputData as ID, FactsExtractor as FE, IntelligentChunker as IC
        DocumentRouter = DR
        InputData = ID
        FactsExtractor = FE
        IntelligentChunker = IC
    if DatabaseManager is None:
        from ..services.database_manager import DatabaseManager as DM
        DatabaseManager = DM
    if KnowledgeIngestionPipeline is None:
        # Try to use Pinecone version first, fall back to DB version if no API key
        if os.environ.get('PINECONE_API_KEY'):
            from ..api.knowledge_ingestion import KnowledgeIngestionPipeline as KIP
        else:
            logger.info("Using database-backed knowledge pipeline (no Pinecone API key found)")
            from ..api.knowledge_ingestion_db import KnowledgeIngestionPipeline as KIP
        KnowledgeIngestionPipeline = KIP

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

class KnowledgeUploadRequest(BaseModel):
    """Request model for knowledge upload"""
    type: str
    category: str = "general"
    metadata: Dict[str, Any] = {}
    clinic_id: str

class CrawlRequest(BaseModel):
    """Request model for URL crawling"""
    url: str
    max_pages: int = 30
    depth: int = 2
    category: str = "general"
    clinic_id: str

class ManualTextRequest(BaseModel):
    """Request model for manual text input"""
    content: str
    title: str
    category: str = "general"
    tags: List[str] = []
    clinic_id: str

async def get_clinic_id(clinic_id: Optional[str] = None) -> str:
    """Get clinic ID from request or session"""
    # In production, this would come from authentication/session
    # For testing, use the Shtern Dental clinic ID
    return clinic_id or "2b8f1c5a-92e1-473e-98f6-e3a13e92b7f5"

@router.post("/upload")
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    category: str = Form("general"),
    clinic_id: str = Form(...),
    metadata: str = Form("{}")
):
    """Upload and process a document"""
    try:
        # Validate file size (50MB max)
        contents = await file.read()
        if len(contents) > 50 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="File too large (max 50MB)")
        
        # Parse metadata
        import json
        try:
            metadata_dict = json.loads(metadata)
        except:
            metadata_dict = {}
        
        # Create ingestion job
        job_id = str(uuid.uuid4())
        
        # Store job in database
        from app.database import get_db_connection
        async with get_db_connection() as conn:
            await conn.execute("""
                INSERT INTO ingestion_jobs (id, clinic_id, job_type, status, input_data, input_metadata)
                VALUES ($1, $2, $3, $4, $5, $6)
            """, job_id, clinic_id, 'file', 'pending', 
                json.dumps({
                    'filename': file.filename,
                    'content_type': file.content_type,
                    'size': len(contents)
                }),
                json.dumps(metadata_dict)
            )
        
        # Process in background
        background_tasks.add_task(
            process_document_task,
            job_id,
            clinic_id,
            contents,
            file.filename,
            file.content_type,
            category,
            metadata_dict
        )
        
        return {"job_id": job_id, "status": "processing"}
        
    except Exception as e:
        logger.error(f"Error uploading document: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/crawl")
async def crawl_website(
    request: CrawlRequest,
    background_tasks: BackgroundTasks
):
    """Crawl a website and extract content"""
    try:
        # Validate URL
        import re
        url_pattern = re.compile(
            r'^https?://'  # http:// or https://
            r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain...
            r'localhost|'  # localhost...
            r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # ...or ip
            r'(?::\d+)?'  # optional port
            r'(?:/?|[/?]\S+)$', re.IGNORECASE
        )
        
        if not url_pattern.match(request.url):
            raise HTTPException(status_code=400, detail="Invalid URL format")
        
        # Create job
        job_id = str(uuid.uuid4())
        
        # Store job
        from app.database import get_db_connection
        async with get_db_connection() as conn:
            await conn.execute("""
                INSERT INTO ingestion_jobs (id, clinic_id, job_type, status, input_data)
                VALUES ($1, $2, $3, $4, $5)
            """, job_id, request.clinic_id, 'url', 'pending',
                json.dumps({
                    'url': request.url,
                    'max_pages': request.max_pages,
                    'depth': request.depth,
                    'category': request.category
                })
            )
        
        # Process in background
        background_tasks.add_task(
            crawl_website_task,
            job_id,
            request.clinic_id,
            request.url,
            request.max_pages,
            request.depth,
            request.category
        )
        
        return {"job_id": job_id, "status": "processing"}
        
    except Exception as e:
        logger.error(f"Error starting crawl: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/manual")
async def add_manual_text(
    request: ManualTextRequest,
    background_tasks: BackgroundTasks
):
    """Add manual text to knowledge base"""
    try:
        # Create job
        job_id = str(uuid.uuid4())
        
        # Store job
        from app.database import get_db_connection
        async with get_db_connection() as conn:
            await conn.execute("""
                INSERT INTO ingestion_jobs (id, clinic_id, job_type, status, input_data, input_metadata)
                VALUES ($1, $2, $3, $4, $5, $6)
            """, job_id, request.clinic_id, 'text', 'pending',
                json.dumps({
                    'content': request.content,
                    'title': request.title
                }),
                json.dumps({
                    'category': request.category,
                    'tags': request.tags
                })
            )
        
        # Process in background
        background_tasks.add_task(
            process_text_task,
            job_id,
            request.clinic_id,
            request.content,
            request.title,
            request.category,
            request.tags
        )
        
        return {"job_id": job_id, "status": "processing"}
        
    except Exception as e:
        logger.error(f"Error adding manual text: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/status/{job_id}")
async def get_job_status(job_id: str):
    """Get status of an ingestion job"""
    try:
        from app.database import get_db_connection
        async with get_db_connection() as conn:
            row = await conn.fetchrow("""
                SELECT id, status, progress, result, error, started_at, completed_at,
                       EXTRACT(EPOCH FROM (NOW() - started_at)) as duration_seconds
                FROM ingestion_jobs
                WHERE id = $1
            """, job_id)
            
            if not row:
                raise HTTPException(status_code=404, detail="Job not found")
            
            # Auto-kill stuck jobs (running for more than 5 minutes without completion)
            if row['status'] in ['pending', 'processing'] and row['duration_seconds'] and row['duration_seconds'] > 300:
                await conn.execute("""
                    UPDATE ingestion_jobs
                    SET status = 'failed',
                        error = 'Job timed out after 5 minutes',
                        completed_at = NOW()
                    WHERE id = $1 AND status IN ('pending', 'processing')
                """, job_id)
                
                return {
                    "id": row['id'],
                    "status": "failed",
                    "progress": row['progress'] or 0,
                    "result": None,
                    "error": "Job timed out after 5 minutes",
                    "started_at": row['started_at'],
                    "completed_at": datetime.utcnow()
                }
            
            return {
                "id": row['id'],
                "status": row['status'],
                "progress": row['progress'] or 0,
                "result": row['result'],
                "error": row['error'],
                "started_at": row['started_at'],
                "completed_at": row['completed_at']
            }
            
    except Exception as e:
        logger.error(f"Error getting job status: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/jobs/active")
async def get_active_jobs(clinic_id: str = Depends(get_clinic_id)):
    """Get all active jobs for a clinic"""
    try:
        from app.database import get_db_connection
        async with get_db_connection() as conn:
            # First, clean up stuck jobs older than 5 minutes
            await conn.execute("""
                UPDATE ingestion_jobs
                SET status = 'failed',
                    error = 'Job timed out',
                    completed_at = NOW()
                WHERE clinic_id = $1 
                    AND status IN ('pending', 'processing')
                    AND started_at < NOW() - INTERVAL '5 minutes'
            """, clinic_id)
            
            # Get all jobs for the clinic
            rows = await conn.fetch("""
                SELECT id, job_type, status, progress, 
                       started_at, completed_at, error,
                       EXTRACT(EPOCH FROM (NOW() - started_at)) as duration_seconds
                FROM ingestion_jobs
                WHERE clinic_id = $1
                ORDER BY started_at DESC
                LIMIT 50
            """, clinic_id)
            
            jobs = []
            for row in rows:
                jobs.append({
                    "id": row['id'],
                    "type": row['job_type'],
                    "status": row['status'],
                    "progress": row['progress'] or 0,
                    "started_at": row['started_at'],
                    "completed_at": row['completed_at'],
                    "error": row['error'],
                    "duration_seconds": row['duration_seconds']
                })
            
            # Count jobs by status
            stats = await conn.fetchrow("""
                SELECT 
                    COUNT(*) FILTER (WHERE status = 'processing') as active,
                    COUNT(*) FILTER (WHERE status IN ('completed', 'completed_with_errors')) as completed,
                    COUNT(*) FILTER (WHERE status = 'failed') as failed
                FROM ingestion_jobs
                WHERE clinic_id = $1
                    AND started_at > NOW() - INTERVAL '24 hours'
            """, clinic_id)
            
            return {
                "jobs": jobs,
                "stats": {
                    "active": stats['active'] or 0,
                    "completed": stats['completed'] or 0,
                    "failed": stats['failed'] or 0
                }
            }
            
    except Exception as e:
        logger.error(f"Error getting active jobs: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/jobs/{job_id}")
async def cancel_and_delete_job(job_id: str):
    """Cancel a job and delete all related data from the database"""
    try:
        from app.database import get_db_connection
        async with get_db_connection() as conn:
            # Validate UUID format
            import uuid
            try:
                uuid.UUID(job_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid job ID format")
            
            # Use direct deletion approach instead of RPC for now
            # First verify the job belongs to this clinic
            job = await conn.fetchrow("""
                SELECT id, clinic_id, status, result
                FROM ingestion_jobs
                WHERE id = $1::uuid
            """, job_id)
            
            if not job:
                raise HTTPException(status_code=404, detail="Job not found")
            
            # For now, allow deletion regardless of clinic (for testing)
            # In production, this should check user authentication
            # if job['clinic_id'] != clinic_id:
            #     raise HTTPException(status_code=403, detail="Unauthorized to cancel this job")
            clinic_id = job['clinic_id']  # Use the job's clinic ID
            
            # Track what we delete
            deleted_docs = 0
            deleted_chunks = 0
            deleted_facts = 0
            
            # Start transaction for atomic deletion
            async with conn.transaction():
                # First, get document IDs associated with this job
                doc_ids = await conn.fetch("""
                    SELECT id FROM knowledge_documents
                    WHERE clinic_id = $1 
                    AND metadata->>'job_id' = $2
                """, clinic_id, job_id)
                
                deleted_docs = len(doc_ids)
                
                # Delete facts for these documents
                if doc_ids:
                    for doc in doc_ids:
                        result = await conn.execute("""
                            DELETE FROM knowledge_facts
                            WHERE document_id = $1
                        """, doc['id'])
                        deleted_facts += int(result.split()[-1]) if result else 0
                        
                        result = await conn.execute("""
                            DELETE FROM knowledge_chunks
                            WHERE document_id = $1
                        """, doc['id'])
                        deleted_chunks += int(result.split()[-1]) if result else 0
                
                # Delete documents
                await conn.execute("""
                    DELETE FROM knowledge_documents
                    WHERE clinic_id = $1 
                    AND metadata->>'job_id' = $2
                """, clinic_id, job_id)
                
                # Delete the job itself
                await conn.execute("""
                    DELETE FROM ingestion_jobs
                    WHERE id = $1::uuid AND clinic_id = $2
                """, job_id, clinic_id)
            
            logger.info(f"Successfully deleted job {job_id} and {deleted_docs} documents")
            
            return {
                "status": "deleted",
                "job_id": job_id,
                "details": {
                    "documents": deleted_docs,
                    "chunks": deleted_chunks,
                    "facts": deleted_facts
                },
                "message": f"Job and {deleted_docs} documents have been completely removed"
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling/deleting job {job_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/jobs/cleanup")
async def cleanup_stuck_jobs(clinic_id: str = Depends(get_clinic_id)):
    """Manually cleanup stuck jobs"""
    try:
        from app.database import get_db_connection
        async with get_db_connection() as conn:
            result = await conn.execute("""
                UPDATE ingestion_jobs
                SET status = 'failed',
                    error = 'Job cleaned up due to timeout',
                    completed_at = NOW()
                WHERE clinic_id = $1 
                    AND status IN ('pending', 'processing')
                    AND started_at < NOW() - INTERVAL '5 minutes'
                RETURNING id
            """, clinic_id)
            
            cleaned_count = len(result) if result else 0
            
            return {
                "message": f"Cleaned up {cleaned_count} stuck jobs",
                "cleaned_count": cleaned_count
            }
            
    except Exception as e:
        logger.error(f"Error cleaning up jobs: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/index-structured-data")
async def index_structured_data(
    clinic_id: str,
    data_types: Optional[List[str]] = None,
    background_tasks: BackgroundTasks = None
):
    """Index structured data from database into Pinecone"""
    try:
        # Default to indexing both doctors and services
        if data_types is None:
            data_types = ['doctors', 'services']

        # Import dependencies
        from ..api.structured_data_embedder import StructuredDataEmbedder
        from ..database import create_supabase_client

        # Get Supabase client
        supabase = create_supabase_client()

        # Create embedder
        embedder = StructuredDataEmbedder(clinic_id, supabase)
        results = {}

        # Index doctors if requested
        if 'doctors' in data_types:
            logger.info(f"Indexing doctors for clinic {clinic_id}")
            results['doctors'] = await embedder.embed_doctors()

        # Index services if requested
        if 'services' in data_types:
            logger.info(f"Indexing services for clinic {clinic_id}")
            results['services'] = await embedder.embed_services()

        return {
            "status": "success",
            "clinic_id": clinic_id,
            "results": results
        }

    except Exception as e:
        logger.error(f"Error indexing structured data: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/structured-data")
async def delete_structured_data(
    clinic_id: str,
    data_type: str = 'all'
):
    """Delete structured data vectors from Pinecone"""
    try:
        from ..api.structured_data_embedder import StructuredDataEmbedder
        from ..database import create_supabase_client

        # Get Supabase client
        supabase = create_supabase_client()

        # Create embedder
        embedder = StructuredDataEmbedder(clinic_id, supabase)

        # Delete vectors
        result = await embedder.delete_structured_data(data_type)

        return {
            "status": "success",
            "clinic_id": clinic_id,
            "result": result
        }

    except Exception as e:
        logger.error(f"Error deleting structured data: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/search")
async def search_knowledge(
    query: str,
    category: Optional[str] = None,
    clinic_id: str = Depends(get_clinic_id),
    limit: int = 10
):
    """Search knowledge base"""
    try:
        # Lazy import dependencies
        lazy_import()

        # Use existing KnowledgeIngestionPipeline for search
        pipeline = KnowledgeIngestionPipeline(clinic_id)

        # Search using Pinecone
        from ..api.message_processor import PineconeKnowledgeBase
        kb = PineconeKnowledgeBase(clinic_id)

        if category:
            results = await kb.search_by_category(query, category, top_k=limit)
        else:
            results = await kb.search(query, top_k=limit)

        return {"results": results}

    except Exception as e:
        logger.error(f"Error searching knowledge: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/documents")
async def list_documents(
    clinic_id: str = Depends(get_clinic_id),
    category: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
):
    """List documents in knowledge base"""
    try:
        from app.database import get_db_connection
        async with get_db_connection() as conn:
            if conn is None:
                # Return empty list if no database connection
                return {"documents": [], "total": 0}
                
            if category:
                query = """
                    SELECT id, title, category, source_type, source_url, source_filename,
                           chunk_count, fact_count, cost_credits, processed_at, tags
                    FROM knowledge_documents
                    WHERE clinic_id = $1 AND is_active = true AND category = $2
                    ORDER BY processed_at DESC LIMIT $3 OFFSET $4
                """
                params = [clinic_id, category, limit, offset]
            else:
                query = """
                    SELECT id, title, category, source_type, source_url, source_filename,
                           chunk_count, fact_count, cost_credits, processed_at, tags
                    FROM knowledge_documents
                    WHERE clinic_id = $1 AND is_active = true
                    ORDER BY processed_at DESC LIMIT $2 OFFSET $3
                """
                params = [clinic_id, limit, offset]
            
            rows = await conn.fetch(query, *params)
            
            documents = []
            for row in rows:
                documents.append({
                    "id": row['id'],
                    "title": row['title'],
                    "category": row['category'],
                    "sourceType": row['source_type'],
                    "sourceUrl": row['source_url'],
                    "sourceFilename": row['source_filename'],
                    "chunks": row['chunk_count'],
                    "facts": row['fact_count'],
                    "cost": float(row['cost_credits']) if row['cost_credits'] else 0,
                    "processedAt": row['processed_at'].isoformat() if row['processed_at'] else None,
                    "tags": row['tags']
                })
            
            return {"documents": documents, "total": len(documents)}
            
    except Exception as e:
        logger.error(f"Error listing documents: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/documents/{doc_id}")
async def delete_document(
    doc_id: int,
    clinic_id: str = Depends(get_clinic_id)
):
    """Delete a document from knowledge base"""
    try:
        from app.database import get_db_connection
        async with get_db_connection() as conn:
            # Verify ownership
            row = await conn.fetchrow("""
                SELECT id FROM knowledge_documents
                WHERE id = $1 AND clinic_id = $2
            """, doc_id, clinic_id)
            
            if not row:
                raise HTTPException(status_code=404, detail="Document not found")
            
            # Soft delete
            await conn.execute("""
                UPDATE knowledge_documents
                SET is_active = false, updated_at = NOW()
                WHERE id = $1
            """, doc_id)
            
            return {"status": "deleted"}
            
    except Exception as e:
        logger.error(f"Error deleting document: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Background task functions
async def process_document_task(
    job_id: str,
    clinic_id: str,
    content: bytes,
    filename: str,
    content_type: str,
    category: str,
    metadata: Dict[str, Any]
):
    """Process document in background"""
    try:
        # Lazy import dependencies
        lazy_import()
        from app.database import get_db_connection
        
        # Update job status
        async with get_db_connection() as conn:
            await conn.execute("""
                UPDATE ingestion_jobs
                SET status = 'processing', started_at = NOW(), progress = 10
                WHERE id = $1
            """, job_id)
        
        # Initialize components
        router = DocumentRouter()
        extractor = FactsExtractor()
        chunker = IntelligentChunker()
        
        # Create input data
        input_data = InputData(
            content=content,
            mime_type=content_type or router.detect_mime_type(filename, content),
            filename=filename,
            metadata=metadata,
            clinic_id=clinic_id,
            category=category
        )
        
        # Process document - this might raise an exception
        try:
            processed_doc = await router.process(input_data)
        except Exception as e:
            logger.error(f"Document processing failed for job {job_id}: {str(e)}")
            # Update job as failed immediately
            async with get_db_connection() as conn:
                await conn.execute("""
                    UPDATE ingestion_jobs
                    SET status = 'failed',
                        error = $2,
                        completed_at = NOW()
                    WHERE id = $1
                """, job_id, f"Document processing failed: {str(e)}")
            return  # Exit early, don't continue with failed document
        
        # Check if document has content before continuing
        if not processed_doc or not processed_doc.content or len(processed_doc.chunks) == 0:
            logger.warning(f"Document has no content for job {job_id}")
            async with get_db_connection() as conn:
                await conn.execute("""
                    UPDATE ingestion_jobs
                    SET status = 'failed',
                        error = 'Document has no extractable content',
                        completed_at = NOW()
                    WHERE id = $1
                """, job_id)
            return  # Exit early
        
        # Update progress
        async with get_db_connection() as conn:
            await conn.execute("""
                UPDATE ingestion_jobs SET progress = 40 WHERE id = $1
            """, job_id)
        
        # Extract facts (with error handling)
        facts = {}
        try:
            facts = await extractor.extract(processed_doc.content, processed_doc.metadata)
        except Exception as e:
            logger.warning(f"Failed to extract facts: {str(e)}")
            # Continue anyway, facts are optional
        
        # Update progress
        async with get_db_connection() as conn:
            await conn.execute("""
                UPDATE ingestion_jobs SET progress = 60 WHERE id = $1
            """, job_id)
        
        # Create chunks (with error handling)
        chunks = processed_doc.chunks  # Use chunks from processed_doc
        try:
            # Try to create additional chunks if chunker provides different logic
            chunks = await chunker.chunk(processed_doc)
        except Exception as e:
            logger.warning(f"Failed to create additional chunks: {str(e)}")
            # Use chunks from processed_doc as fallback
            chunks = processed_doc.chunks
        
        # Update progress
        async with get_db_connection() as conn:
            await conn.execute("""
                UPDATE ingestion_jobs SET progress = 80 WHERE id = $1
            """, job_id)
        
        # Store in database and Pinecone (with error handling)
        try:
            pipeline = KnowledgeIngestionPipeline(clinic_id)
            
            # Ensure proper metadata for file uploads
            if filename:
                # It's a file upload, ensure source_type is 'file'
                metadata['source_type'] = 'file'
                
                # Extract title from filename if not provided
                if not metadata.get('title') or metadata.get('title') == 'Untitled':
                    import os
                    title = os.path.splitext(filename)[0]
                    title = title.replace('_', ' ').replace('-', ' ')
                    title = ' '.join(word.capitalize() for word in title.split())
                    metadata['title'] = title or 'Document'
            
            doc_id = await pipeline.ingest_document(
                content=processed_doc.content,
                metadata={
                    **metadata,
                    'filename': filename,
                    'mime_type': content_type,
                    'facts': facts,
                    'processing_time_ms': processed_doc.processing_time_ms,
                    'job_id': job_id  # Track job ID for cleanup
                },
                category=category
            )
            
            # Complete job successfully
            async with get_db_connection() as conn:
                await conn.execute("""
                    UPDATE ingestion_jobs
                    SET status = 'completed',
                        progress = 100,
                        completed_at = NOW(),
                        result = $2
                    WHERE id = $1
                """, job_id, json.dumps({
                    'document_id': doc_id.get('doc_id') if isinstance(doc_id, dict) else doc_id,
                    'chunks': len(chunks),
                    'facts': facts,
                    'tokens': processed_doc.tokens_count,
                    'status': doc_id.get('status', 'indexed') if isinstance(doc_id, dict) else 'indexed'
                }))
                
        except Exception as e:
            logger.error(f"Failed to ingest document into knowledge base: {str(e)}")
            async with get_db_connection() as conn:
                await conn.execute("""
                    UPDATE ingestion_jobs
                    SET status = 'failed',
                        error = $2,
                        completed_at = NOW()
                    WHERE id = $1
                """, job_id, f"Knowledge base ingestion failed: {str(e)}")
        
    except Exception as e:
        logger.error(f"Error processing document: {str(e)}")
        from app.database import get_db_connection
        async with get_db_connection() as conn:
            await conn.execute("""
                UPDATE ingestion_jobs
                SET status = 'failed',
                    error = $2,
                    completed_at = NOW()
                WHERE id = $1
            """, job_id, str(e))

async def crawl_website_task(
    job_id: str,
    clinic_id: str,
    url: str,
    max_pages: int,
    depth: int,
    category: str
):
    """Crawl website in background"""
    try:
        from app.database import get_db_connection
        async with get_db_connection() as conn:
            await conn.execute("""
                UPDATE ingestion_jobs
                SET status = 'processing', started_at = NOW(), progress = 10
                WHERE id = $1
            """, job_id)
        
        # Import crawling modules
        from ..knowledge.web_crawler import EnhancedWebCrawler, SitemapCrawler
        from ..knowledge.schema_extractor import SchemaOrgExtractor, ContentCleaner
        
        # Create progress callback to update database
        async def update_crawl_progress(pages_crawled, total_pages, current_url, progress):
            """Update crawl progress in database"""
            try:
                async with get_db_connection() as conn:
                    # Map crawl progress to 10-50% range
                    scaled_progress = 10 + (progress * 0.4)
                    await conn.execute("""
                        UPDATE ingestion_jobs 
                        SET progress = $2,
                            result = $3
                        WHERE id = $1
                    """, job_id, int(scaled_progress), json.dumps({
                        'pages_crawled': pages_crawled,
                        'max_pages': total_pages,
                        'current_url': current_url[:100],
                        'status': 'crawling'
                    }))
            except Exception as e:
                logger.warning(f"Failed to update crawl progress: {e}")
        
        # Initialize crawler with progress callback
        crawler = EnhancedWebCrawler(
            max_depth=depth,
            max_pages=max_pages,
            rate_limit_delay=1.0,
            progress_callback=update_crawl_progress
        )
        
        # Update initial progress
        async with get_db_connection() as conn:
            await conn.execute("""
                UPDATE ingestion_jobs SET progress = 10 WHERE id = $1
            """, job_id)
        
        # Crawl website
        logger.info(f"Starting crawl of {url} for clinic {clinic_id}")
        crawl_result = await crawler.crawl(url)
        
        # Update progress after crawl completes
        async with get_db_connection() as conn:
            await conn.execute("""
                UPDATE ingestion_jobs 
                SET progress = 50,
                    result = $2
                WHERE id = $1
            """, job_id, json.dumps({
                'pages_crawled': crawl_result.get('pages_crawled', 0),
                'status': 'crawl_complete_processing_content'
            }))
        
        # Extract structured data
        schema_extractor = SchemaOrgExtractor()
        structured_data = crawl_result.get('structured_data', {})
        
        # Process each crawled page
        document_ids = []
        failed_pages = []
        total_pages = len(crawl_result.get('raw_pages', []))
        
        # Initialize pipeline with error handling
        pipeline = None
        try:
            # Ensure KnowledgeIngestionPipeline is imported
            global KnowledgeIngestionPipeline
            if KnowledgeIngestionPipeline is None:
                # Try to use Pinecone version first, fall back to DB version if no API key
                pinecone_key = os.environ.get('PINECONE_API_KEY')
                logger.info(f"PINECONE_API_KEY status: {'Set' if pinecone_key else 'Not set'}")
                if pinecone_key:
                    logger.info(f"PINECONE_API_KEY starts with: {pinecone_key[:10]}...")
                
                if pinecone_key:
                    from app.api.knowledge_ingestion import KnowledgeIngestionPipeline as KIP
                else:
                    logger.info("Using database-backed knowledge pipeline (no Pinecone API key found)")
                    from app.api.knowledge_ingestion_db import KnowledgeIngestionPipeline as KIP
                KnowledgeIngestionPipeline = KIP
            
            pipeline = KnowledgeIngestionPipeline(clinic_id)
        except Exception as e:
            logger.error(f"Failed to initialize KnowledgeIngestionPipeline: {str(e)}")
            # Update job status to failed
            async with get_db_connection() as conn:
                await conn.execute("""
                    UPDATE ingestion_jobs
                    SET status = 'failed',
                        error = $2,
                        completed_at = NOW()
                    WHERE id = $1
                """, job_id, f"Pipeline initialization failed: {str(e)}")
            return
        
        if not pipeline:
            logger.error("Pipeline is None after initialization")
            return
        
        for idx, page in enumerate(crawl_result.get('raw_pages', [])):
            # Update progress more accurately (50-90% range for processing)
            progress = 50 + (40 * (idx + 1) / max(total_pages, 1))
            
            # Update progress for every page (or every 2 pages for larger crawls)
            # This ensures UI stays responsive
            update_frequency = 1 if total_pages <= 10 else 2
            if idx % update_frequency == 0 or idx == total_pages - 1:
                async with get_db_connection() as conn:
                    await conn.execute("""
                        UPDATE ingestion_jobs 
                        SET progress = $2,
                            result = $3
                        WHERE id = $1
                    """, job_id, int(progress), json.dumps({
                        'current_page': idx + 1,
                        'total_pages': total_pages,
                        'documents_created': len(document_ids),
                        'documents_failed': len(failed_pages),
                        'status': 'processing_pages',
                        'current_url': page.get('url', 'unknown')[:100]  # Show what's being processed
                    }))
            
            # Clean and process content
            content = ContentCleaner.clean_text(page.get('content', ''))
            
            if not ContentCleaner.is_relevant_content(content):
                continue
            
            # Combine extracted information - ensure Pinecone-compatible metadata
            # Convert empty dicts to empty strings for Pinecone compatibility
            business_hours_data = page.get('business_hours', {})
            if isinstance(business_hours_data, dict) and business_hours_data:
                business_hours_str = json.dumps(business_hours_data)
            else:
                business_hours_str = ""
                
            contact_info_data = page.get('contact_info', {})
            if isinstance(contact_info_data, dict) and contact_info_data:
                contact_info_str = json.dumps(contact_info_data)
            else:
                contact_info_str = ""
            
            page_metadata = {
                'url': page.get('url', ''),
                'title': page.get('title', ''),
                'description': page.get('description', ''),
                'depth': page.get('depth', 0),
                'source': 'web_crawler',
                'crawled_at': page.get('crawled_at', ''),
                'structured_data': json.dumps(page.get('structured_data', [])) if page.get('structured_data') else "",
                'contact_info': contact_info_str,
                'services': json.dumps(page.get('services', [])) if page.get('services') else "",
                'team_members': json.dumps(page.get('team_members', [])) if page.get('team_members') else "",
                'business_hours': business_hours_str,
                'faqs': json.dumps(page.get('faqs', [])) if page.get('faqs') else "",
                'testimonials': json.dumps(page.get('testimonials', [])) if page.get('testimonials') else ""
            }
            
            # Ingest the page content
            try:
                result = await pipeline.ingest_document(
                    content=content,
                    metadata=page_metadata,
                    category=category
                )
                logger.info(f"Ingestion result for {page.get('url')}: {result}")
                if result and isinstance(result, dict):
                    # Get doc_id from result - could be string (Pinecone) or int (DB)
                    doc_id = result.get('doc_id') or result.get('db_id')
                    status = result.get('status', 'unknown')
                    
                    if doc_id:
                        document_ids.append(doc_id)
                        logger.info(f"Successfully ingested page {idx+1}/{total_pages}: {page.get('url', 'unknown')} (status: {status}, doc_id: {doc_id})")
                    else:
                        logger.warning(f"No doc_id in result for page {page.get('url')}: {result}")
                        failed_pages.append({'url': page.get('url'), 'error': 'No document ID returned'})
                else:
                    logger.warning(f"Invalid result from pipeline for page {page.get('url')}: {result}")
                    failed_pages.append({'url': page.get('url'), 'error': 'Invalid pipeline result'})
            except Exception as e:
                logger.error(f"Error ingesting page {page.get('url')}: {str(e)}", exc_info=True)
                failed_pages.append({'url': page.get('url'), 'error': str(e)})
                # Continue processing other pages instead of failing completely
        
        # Try to parse sitemap for additional URLs
        sitemap_urls = []
        try:
            sitemap_crawler = SitemapCrawler()
            sitemap_urls = await sitemap_crawler.parse_sitemap(url)
        except Exception as e:
            logger.warning(f"Failed to parse sitemap for {url}: {str(e)}")
        
        # Store structured clinic information (only if we have valid integer IDs)
        if structured_data and document_ids:
            # Check if the first document_id is an integer (DB-backed) or string hash (Pinecone)
            first_doc_id = document_ids[0] if document_ids else None
            if first_doc_id and isinstance(first_doc_id, (int, type(None))):
                # Only store in knowledge_facts if we have integer IDs (DB-backed pipeline)
                async with get_db_connection() as conn:
                    await conn.execute("""
                        INSERT INTO knowledge_facts (clinic_id, fact_type, fact_data, confidence_score, source_document_id)
                        VALUES ($1, $2, $3, $4, $5)
                        ON CONFLICT DO NOTHING
                    """, clinic_id, 'clinic_info', json.dumps(structured_data), 0.9, first_doc_id)
            else:
                # For Pinecone-backed pipeline, just log that we extracted structured data
                logger.info(f"Extracted structured data for clinic {clinic_id}, stored in Pinecone metadata")
        
        # Determine final status based on results
        failed_count = len(failed_pages)
        if len(document_ids) == 0:
            final_status = 'failed'
            final_message = f"Failed to process any pages from {total_pages} crawled"
        elif failed_count > 0:
            final_status = 'completed_with_errors'
            final_message = f"Processed {len(document_ids)} of {total_pages} pages ({failed_count} failed)"
        else:
            final_status = 'completed'
            final_message = f"Successfully processed all {len(document_ids)} pages"
        
        # Update progress to complete with detailed results
        async with get_db_connection() as conn:
            await conn.execute("""
                UPDATE ingestion_jobs
                SET status = $2,
                    progress = 100,
                    completed_at = NOW(),
                    result = $3
                WHERE id = $1
            """, job_id, final_status, json.dumps({
                'pages_crawled': crawl_result.get('pages_crawled', 0),
                'total_pages_found': total_pages,
                'documents_created': len(document_ids),
                'documents_failed': failed_count,
                'failed_pages': failed_pages[:10],  # Include first 10 failed pages for debugging
                'structured_data_extracted': bool(structured_data),
                'sitemap_urls_found': len(sitemap_urls),
                'message': final_message,
                'status': 'complete',
                'clinic_info': {
                    'name': structured_data.get('clinic_info', {}).get('name', ''),
                    'services_count': len(structured_data.get('services', [])),
                    'team_count': len(structured_data.get('team', [])),
                    'faqs_count': len(structured_data.get('faqs', []))
                }
            }))
        
        logger.info(final_message)
            
    except Exception as e:
        logger.error(f"Error crawling website: {str(e)}")
        from app.database import get_db_connection
        
        # Try to save partial results if any
        try:
            async with get_db_connection() as conn:
                # Get current progress
                job_info = await conn.fetchrow("""
                    SELECT progress FROM ingestion_jobs WHERE id = $1
                """, job_id)
                
                current_progress = job_info['progress'] if job_info else 0
                
                await conn.execute("""
                    UPDATE ingestion_jobs
                    SET status = 'failed',
                        error = $2,
                        completed_at = NOW(),
                        result = $3
                    WHERE id = $1
                """, job_id, str(e), json.dumps({
                    'error': str(e),
                    'partial_progress': current_progress,
                    'message': f'Crawl failed after processing {current_progress}% of content. Error: {str(e)}'
                }))
        except Exception as db_error:
            logger.error(f"Failed to update job status: {str(db_error)}")

async def process_text_task(
    job_id: str,
    clinic_id: str,
    content: str,
    title: str,
    category: str,
    tags: List[str]
):
    """Process manual text in background"""
    try:
        from app.database import get_db_connection
        async with get_db_connection() as conn:
            await conn.execute("""
                UPDATE ingestion_jobs
                SET status = 'processing', started_at = NOW()
                WHERE id = $1
            """, job_id)
        
        # Process using pipeline
        pipeline = KnowledgeIngestionPipeline(clinic_id)
        doc_id = await pipeline.ingest_document(
            content=content,
            metadata={
                'title': title,
                'tags': tags,
                'source': 'manual_entry'
            },
            category=category
        )
        
        # Complete job
        async with get_db_connection() as conn:
            await conn.execute("""
                UPDATE ingestion_jobs
                SET status = 'completed',
                    progress = 100,
                    completed_at = NOW(),
                    result = $2
                WHERE id = $1
            """, job_id, json.dumps({
                'document_id': doc_id,
                'message': 'Text processed successfully'
            }))
            
    except Exception as e:
        logger.error(f"Error processing text: {str(e)}")
        from app.database import get_db_connection
        async with get_db_connection() as conn:
            await conn.execute("""
                UPDATE ingestion_jobs
                SET status = 'failed',
                    error = $2,
                    completed_at = NOW()
                WHERE id = $1
            """, job_id, str(e))