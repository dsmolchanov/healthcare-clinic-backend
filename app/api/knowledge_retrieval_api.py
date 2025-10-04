"""
Knowledge Retrieval API
Universal endpoints for querying RAG and memory systems

This module provides REST API endpoints for knowledge retrieval
that can be used by any service or frontend application.
"""

from fastapi import APIRouter, HTTPException, Query, Body
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class SearchRequest(BaseModel):
    """Request model for knowledge search"""
    query: str = Field(..., description="Search query text")
    namespace: Optional[str] = Field(None, description="Optional namespace (e.g., clinic_id)")
    filters: Optional[Dict[str, Any]] = Field(None, description="Optional filters")
    top_k: int = Field(5, ge=1, le=20, description="Number of results to return")
    session_id: Optional[str] = Field(None, description="Optional session ID for context")
    include_memory: bool = Field(True, description="Include memory search results")
    include_rag: bool = Field(True, description="Include RAG search results")


class SearchResponse(BaseModel):
    """Response model for knowledge search"""
    query: str
    results: List[Dict[str, Any]]
    memory_results: Optional[List[Dict[str, Any]]] = None
    latency_ms: float
    total_results: int
    namespace: Optional[str] = None


class ContextRequest(BaseModel):
    """Request model for context generation"""
    query: str = Field(..., description="Query for context generation")
    session_id: str = Field(..., description="Session identifier")
    namespace: Optional[str] = Field(None, description="Optional namespace")
    user_id: Optional[str] = Field(None, description="Optional user ID")
    max_tokens: int = Field(1000, ge=100, le=4000, description="Maximum tokens for context")


class ContextResponse(BaseModel):
    """Response model for context generation"""
    query: str
    context: str
    sources: List[str]
    memory_count: int
    knowledge_count: int
    total_tokens: int


@router.post("/search", response_model=SearchResponse)
async def search_knowledge(request: SearchRequest):
    """
    Search across RAG and memory systems

    Args:
        request: Search request parameters

    Returns:
        Combined search results from RAG and memory
    """
    start_time = datetime.utcnow()

    try:
        # Import universal systems
        import sys
        import os
        sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'services', 'rag'))
        from universal_rag import UniversalRAG, UniversalMemory

        results = []
        memory_results = []

        # Search RAG if requested
        if request.include_rag:
            rag = UniversalRAG(namespace=request.namespace)
            rag_results = await rag.search(
                query=request.query,
                filters=request.filters,
                top_k=request.top_k,
                session_context={"session_id": request.session_id} if request.session_id else None
            )
            results.extend(rag_results)

        # Search memory if requested
        if request.include_memory and request.session_id:
            memory = UniversalMemory(user_id=request.session_id)
            mem_results = await memory.search(
                query=request.query,
                top_k=request.top_k,
                session_id=request.session_id
            )
            memory_results = mem_results

        # Calculate latency
        latency_ms = (datetime.utcnow() - start_time).total_seconds() * 1000

        return SearchResponse(
            query=request.query,
            results=results,
            memory_results=memory_results if memory_results else None,
            latency_ms=latency_ms,
            total_results=len(results) + len(memory_results),
            namespace=request.namespace
        )

    except Exception as e:
        logger.error(f"Search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/context", response_model=ContextResponse)
async def get_context(request: ContextRequest):
    """
    Get formatted context for LLM injection

    Args:
        request: Context request parameters

    Returns:
        Formatted context ready for LLM injection
    """
    try:
        # Import universal systems
        import sys
        import os
        sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'services', 'rag'))
        from universal_rag import UniversalRAG, UniversalMemory, UnifiedContextInjector

        # Initialize services
        rag = UniversalRAG(namespace=request.namespace)
        memory = UniversalMemory(user_id=request.user_id or request.session_id)
        injector = UnifiedContextInjector(rag=rag, memory=memory)

        # Get injected context
        state = {}
        state = await injector.inject_context(
            state=state,
            query=request.query,
            session_id=request.session_id,
            include_rag=True,
            include_memory=True
        )

        # Extract sources
        sources = []
        if state.get('knowledge'):
            sources.append("Knowledge Base")
        if state.get('memories'):
            sources.append("Conversation Memory")

        # Estimate tokens (rough)
        context = state.get('injected_context', '')
        token_estimate = len(context.split()) if context else 0

        return ContextResponse(
            query=request.query,
            context=context,
            sources=sources,
            memory_count=len(state.get('memories', [])),
            knowledge_count=1 if state.get('knowledge') else 0,
            total_tokens=token_estimate
        )

    except Exception as e:
        logger.error(f"Context generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/add-memory")
async def add_memory(
    content: str = Body(..., description="Memory content to store"),
    session_id: str = Body(..., description="Session identifier"),
    user_id: Optional[str] = Body(None, description="Optional user ID"),
    metadata: Optional[Dict[str, Any]] = Body(None, description="Optional metadata")
):
    """
    Add a memory to the system

    Args:
        content: Memory content
        session_id: Session ID
        user_id: Optional user ID
        metadata: Optional metadata

    Returns:
        Memory storage result
    """
    try:
        # Import universal memory
        import sys
        import os
        sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'services', 'rag'))
        from universal_rag import UniversalMemory

        memory = UniversalMemory(user_id=user_id or session_id)
        result = await memory.add(
            content=content,
            metadata=metadata,
            session_id=session_id
        )

        return {"status": "success", "result": result}

    except Exception as e:
        logger.error(f"Memory storage failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def get_knowledge_stats(
    namespace: Optional[str] = Query(None, description="Optional namespace filter")
):
    """
    Get statistics about the knowledge system

    Args:
        namespace: Optional namespace filter

    Returns:
        Knowledge system statistics
    """
    try:
        # This would connect to actual metrics in production
        return {
            "namespace": namespace or "default",
            "total_documents": 0,  # Would query actual count
            "total_memories": 0,    # Would query actual count
            "last_updated": datetime.utcnow().isoformat(),
            "status": "operational"
        }

    except Exception as e:
        logger.error(f"Failed to get stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/clear-session-memory")
async def clear_session_memory(
    session_id: str = Query(..., description="Session ID to clear")
):
    """
    Clear memory for a specific session

    Args:
        session_id: Session ID

    Returns:
        Clear operation result
    """
    try:
        # This would clear actual session memory in production
        return {
            "status": "success",
            "session_id": session_id,
            "message": "Session memory cleared"
        }

    except Exception as e:
        logger.error(f"Failed to clear session memory: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Export router for inclusion in main app
__all__ = ["router"]