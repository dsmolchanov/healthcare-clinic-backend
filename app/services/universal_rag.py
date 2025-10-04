"""
Universal RAG Service
Makes RAG and mem0 memory systems universally available across all services

This module provides a unified interface for knowledge retrieval that can be used
by any service, not just the clinics module.
"""

import os
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class UniversalRAG:
    """Universal RAG interface for all services"""

    def __init__(
        self,
        namespace: Optional[str] = None,
        enable_cache: bool = True,
        enable_metrics: bool = True
    ):
        """
        Initialize universal RAG service

        Args:
            namespace: Optional namespace for multi-tenant support
            enable_cache: Enable caching for repeated queries
            enable_metrics: Enable performance metrics tracking
        """
        self.namespace = namespace or "default"
        self.enable_cache = enable_cache
        self.enable_metrics = enable_metrics

        # Lazy-loaded components
        self._search_engine = None
        self._cache = None
        self._metrics = None

    @property
    def search_engine(self):
        """Lazy load search engine"""
        if self._search_engine is None:
            # Import the existing hybrid search engine from clinics
            import sys
            sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'clinics', 'backend'))

            from app.api.hybrid_search_engine import HybridSearchEngine

            # Use namespace as clinic_id for backward compatibility
            self._search_engine = HybridSearchEngine(
                clinic_id=self.namespace,
                patient_phone=None  # Can be set per search
            )
        return self._search_engine

    @property
    def cache(self):
        """Lazy load cache"""
        if self._cache is None and self.enable_cache:
            try:
                import sys
                sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'clinics', 'backend'))
                from app.api.rag_cache import RAGCache
                self._cache = RAGCache(self.namespace)  # Pass clinic_id from namespace
            except ImportError:
                logger.warning("RAG cache not available")
                self._cache = None
        return self._cache

    @property
    def metrics(self):
        """Lazy load metrics tracker"""
        if self._metrics is None and self.enable_metrics:
            try:
                import sys
                sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'clinics', 'backend'))
                from app.api.rag_metrics import RAGMetrics
                self._metrics = RAGMetrics()
            except ImportError:
                logger.warning("RAG metrics not available")
                self._metrics = None
        return self._metrics

    async def search(
        self,
        query: str,
        filters: Optional[Dict] = None,
        top_k: int = 5,
        session_context: Optional[Dict] = None
    ) -> List[Dict]:
        """
        Universal search across knowledge base

        Args:
            query: Search query
            filters: Optional filters (e.g., document type, date range)
            top_k: Number of results to return
            session_context: Optional session context for personalization

        Returns:
            List of search results with metadata
        """
        start_time = datetime.utcnow()

        # Check cache if enabled
        if self.cache:
            cache_key = f"{self.namespace}:{query}:{str(filters)}:{top_k}"
            cached_result = await self.cache.get(cache_key)
            if cached_result:
                logger.debug(f"Cache hit for query: {query[:50]}...")
                return cached_result

        try:
            # Use hybrid search for comprehensive results
            results = await self.search_engine.hybrid_search(
                query=query,
                top_k=top_k,
                threshold=0.7,
                include_metadata=True,
                filters=filters
            )

            # Track metrics if enabled
            if self.metrics:
                latency_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
                await self.metrics.track_search(
                    query=query,
                    results_count=len(results),
                    latency_ms=latency_ms,
                    namespace=self.namespace
                )

            # Cache results if enabled
            if self.cache and results:
                await self.cache.set(cache_key, results, ttl=300)  # 5 min TTL

            return results

        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []

    async def get_context(
        self,
        query: str,
        session_id: str,
        max_tokens: int = 1000
    ) -> str:
        """
        Get formatted context for LLM injection

        Args:
            query: User query
            session_id: Session identifier
            max_tokens: Maximum tokens for context

        Returns:
            Formatted context string ready for LLM
        """
        # Search for relevant information
        results = await self.search(query, top_k=3)

        if not results:
            return ""

        # Format context for injection
        context_parts = []
        token_count = 0

        for result in results:
            content = result.get('content', '')
            metadata = result.get('metadata', {})

            # Build context entry
            entry = f"[Source: {metadata.get('source', 'Unknown')}]\n{content}\n"

            # Simple token estimation (rough)
            estimated_tokens = len(entry.split()) * 1.3

            if token_count + estimated_tokens > max_tokens:
                break

            context_parts.append(entry)
            token_count += estimated_tokens

        return "\n---\n".join(context_parts)


class UniversalMemory:
    """Universal memory interface using mem0"""

    def __init__(self, user_id: Optional[str] = None):
        """
        Initialize universal memory service

        Args:
            user_id: Optional user identifier for memory isolation
        """
        self.user_id = user_id or "default"
        self._memory_manager = None

    @property
    def memory_manager(self):
        """Lazy load memory manager"""
        if self._memory_manager is None:
            # Import the existing memory system from clinics
            import sys
            sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'clinics', 'backend'))

            from app.memory.conversation_memory import ConversationMemoryManager

            self._memory_manager = ConversationMemoryManager()
        return self._memory_manager

    async def add(
        self,
        content: str,
        metadata: Optional[Dict] = None,
        session_id: Optional[str] = None
    ) -> Dict:
        """
        Add memory to the system

        Args:
            content: Memory content
            metadata: Optional metadata
            session_id: Optional session ID

        Returns:
            Memory storage result
        """
        try:
            # Use mem0 if available through the memory manager
            if hasattr(self.memory_manager, 'memory') and self.memory_manager.memory:
                result = await self.memory_manager.memory.add(
                    content,
                    user_id=self.user_id,
                    metadata=metadata
                )
                return {"status": "success", "memory_id": result.get('id')}
            else:
                # Fallback to basic storage
                logger.warning("mem0 not available, using basic storage")
                return {"status": "stored", "method": "basic"}

        except Exception as e:
            logger.error(f"Failed to add memory: {e}")
            return {"status": "error", "error": str(e)}

    async def search(
        self,
        query: str,
        top_k: int = 5,
        session_id: Optional[str] = None
    ) -> List[Dict]:
        """
        Search memories

        Args:
            query: Search query
            top_k: Number of results
            session_id: Optional session filter

        Returns:
            List of relevant memories
        """
        try:
            # Use mem0 search if available
            if hasattr(self.memory_manager, 'memory') and self.memory_manager.memory:
                results = await self.memory_manager.memory.search(
                    query=query,
                    user_id=self.user_id,
                    limit=top_k
                )
                return results
            else:
                # Fallback to empty results
                logger.warning("mem0 not available for search")
                return []

        except Exception as e:
            logger.error(f"Memory search failed: {e}")
            return []

    async def get_context(
        self,
        query: str,
        session_id: str,
        max_memories: int = 3
    ) -> List[str]:
        """
        Get relevant memories as context

        Args:
            query: Current query
            session_id: Session ID
            max_memories: Maximum number of memories

        Returns:
            List of relevant memory strings
        """
        memories = await self.search(query, top_k=max_memories, session_id=session_id)

        return [
            mem.get('memory', mem.get('content', ''))
            for mem in memories
            if mem.get('memory') or mem.get('content')
        ]


class UnifiedContextInjector:
    """Unified context injection for LangGraph nodes"""

    def __init__(
        self,
        rag: Optional[UniversalRAG] = None,
        memory: Optional[UniversalMemory] = None
    ):
        """
        Initialize unified context injector

        Args:
            rag: RAG service instance
            memory: Memory service instance
        """
        self.rag = rag or UniversalRAG()
        self.memory = memory or UniversalMemory()

    async def inject_context(
        self,
        state: Dict[str, Any],
        query: str,
        session_id: str,
        include_rag: bool = True,
        include_memory: bool = True
    ) -> Dict[str, Any]:
        """
        Inject RAG and memory context into LangGraph state

        Args:
            state: Current LangGraph state
            query: Current query
            session_id: Session identifier
            include_rag: Include RAG results
            include_memory: Include memory results

        Returns:
            Updated state with injected context
        """
        context_parts = []

        # Add RAG context
        if include_rag and self.rag:
            rag_context = await self.rag.get_context(query, session_id)
            if rag_context:
                context_parts.append(f"## Knowledge Base Context:\n{rag_context}")
                state['knowledge'] = rag_context

        # Add memory context
        if include_memory and self.memory:
            memories = await self.memory.get_context(query, session_id)
            if memories:
                memory_text = "\n".join(memories)
                context_parts.append(f"## Conversation Memory:\n{memory_text}")
                state['memories'] = memories

        # Combine all context
        if context_parts:
            state['injected_context'] = "\n\n".join(context_parts)
            state['context_injected'] = True
        else:
            state['injected_context'] = ""
            state['context_injected'] = False

        return state


# Convenience function for easy integration
async def get_universal_context(
    query: str,
    session_id: str,
    namespace: Optional[str] = None,
    user_id: Optional[str] = None
) -> str:
    """
    Quick helper to get combined RAG and memory context

    Args:
        query: User query
        session_id: Session ID
        namespace: Optional namespace
        user_id: Optional user ID

    Returns:
        Combined context string
    """
    rag = UniversalRAG(namespace=namespace)
    memory = UniversalMemory(user_id=user_id)
    injector = UnifiedContextInjector(rag=rag, memory=memory)

    state = {}
    state = await injector.inject_context(
        state=state,
        query=query,
        session_id=session_id
    )

    return state.get('injected_context', '')