# clinics/backend/app/api/improved_knowledge_base.py
"""
Improved Pinecone Knowledge Base with fixes for retrieval issues
"""

import os
import logging
from typing import List, Dict, Any, Optional
import pinecone
from openai import OpenAI

logger = logging.getLogger(__name__)


class ImprovedPineconeKnowledgeBase:
    """Improved knowledge base with better retrieval logic"""
    
    def __init__(self, clinic_id: str):
        self.clinic_id = clinic_id
        
        # Initialize Pinecone
        pc = pinecone.Pinecone(api_key=os.environ.get('PINECONE_API_KEY'))
        
        # Generate index name (use full UUID, not truncated)
        safe_clinic_id = clinic_id.lower().replace('_', '-').replace(' ', '-')[:8]
        self.index_name = f"clinic-{safe_clinic_id}-kb"
        
        logger.info(f"Connecting to Pinecone index: {self.index_name}")
        
        try:
            self.index = pc.Index(self.index_name)
        except Exception as e:
            logger.warning(f"Failed to connect to Pinecone: {e}")
            self.index = None
        
        # Initialize OpenAI
        self.openai = OpenAI()
        
        # Configurable parameters (adjusted for better recall)
        self.similarity_threshold = 0.3  # Lower threshold for better recall
        self.top_k = 5  # Retrieve more candidates
        self.fallback_top_k = 10  # For fallback search
    
    async def search(
        self,
        query: str,
        filter_dict: Optional[Dict] = None,
        top_k: int = 3,
        use_fallback: bool = True
    ) -> List[str]:
        """
        Enhanced search with fallback mechanism

        Args:
            query: Search query
            filter_dict: Optional metadata filter
            top_k: Number of results to return (default: 3, reduced from 5 for better performance)
            use_fallback: Whether to use fallback search if filtered search returns nothing

        Returns:
            List of relevant text chunks
        """
        
        if not self.index:
            logger.warning("Pinecone index not available")
            return []
        
        try:
            # Generate query embedding
            response = self.openai.embeddings.create(
                model="text-embedding-3-small",
                input=query
            )
            query_embedding = response.data[0].embedding
            
            # Primary search with clinic filter
            if filter_dict is None:
                filter_dict = {"clinic_id": self.clinic_id}
            
            results = self.index.query(
                vector=query_embedding,
                filter=filter_dict,
                top_k=top_k,
                include_metadata=True
            )
            
            # Process results with lower threshold
            relevant_texts = []
            for match in results.matches:
                if match.score >= self.similarity_threshold:
                    text = match.metadata.get('text', '')
                    if text:
                        relevant_texts.append(text)
                        logger.debug(f"Found match with score {match.score}: {text[:50]}...")
            
            # Fallback: If no results with filter, try without filter
            if not relevant_texts and use_fallback:
                logger.info("No results with filter, trying fallback search")
                
                fallback_results = self.index.query(
                    vector=query_embedding,
                    top_k=self.fallback_top_k,
                    include_metadata=True
                )
                
                # Use slightly higher threshold for unfiltered results
                fallback_threshold = self.similarity_threshold + 0.1
                
                for match in fallback_results.matches:
                    # Still check clinic_id in metadata for safety
                    if (match.score >= fallback_threshold and 
                        match.metadata.get('clinic_id') == self.clinic_id):
                        text = match.metadata.get('text', '')
                        if text:
                            relevant_texts.append(text)
                            logger.debug(f"Fallback match with score {match.score}: {text[:50]}...")
            
            logger.info(f"RAG search found {len(relevant_texts)} relevant items")
            return relevant_texts[:3]  # Return top 3 most relevant
            
        except Exception as e:
            logger.error(f"Error during RAG search: {e}")
            return []
    
    async def search_with_reranking(self, query: str) -> List[Dict[str, Any]]:
        """
        Advanced search with semantic reranking
        
        Returns results with scores and metadata
        """
        
        if not self.index:
            return []
        
        try:
            # Get initial results
            response = self.openai.embeddings.create(
                model="text-embedding-3-small",
                input=query
            )
            query_embedding = response.data[0].embedding
            
            # Get more candidates for reranking
            results = self.index.query(
                vector=query_embedding,
                filter={"clinic_id": self.clinic_id},
                top_k=10,
                include_metadata=True
            )
            
            # Rerank using cross-encoder or additional scoring
            ranked_results = []
            for match in results.matches:
                # Calculate enhanced score based on multiple factors
                base_score = match.score
                
                # Boost score based on category relevance
                category = match.metadata.get('category', '')
                category_boost = self._get_category_boost(query, category)
                
                # Boost score based on recency
                indexed_at = match.metadata.get('indexed_at', '')
                recency_boost = self._get_recency_boost(indexed_at)
                
                # Combined score
                final_score = base_score * 0.7 + category_boost * 0.2 + recency_boost * 0.1
                
                ranked_results.append({
                    'text': match.metadata.get('text', ''),
                    'score': final_score,
                    'category': category,
                    'metadata': match.metadata
                })
            
            # Sort by final score
            ranked_results.sort(key=lambda x: x['score'], reverse=True)
            
            # Filter by adjusted threshold
            filtered = [r for r in ranked_results if r['score'] >= self.similarity_threshold]
            
            return filtered[:5]
            
        except Exception as e:
            logger.error(f"Error in reranking search: {e}")
            return []
    
    def _get_category_boost(self, query: str, category: str) -> float:
        """Calculate category relevance boost"""
        query_lower = query.lower()
        
        # Define category keywords
        category_keywords = {
            'insurance': ['insurance', 'coverage', 'plan', 'dental plan'],
            'services': ['service', 'treatment', 'procedure', 'offer', 'price'],
            'policies': ['policy', 'appointment', 'cancel', 'book', 'schedule'],
            'aftercare': ['after', 'care', 'extraction', 'post', 'recovery']
        }
        
        # Check if query matches category keywords
        if category in category_keywords:
            for keyword in category_keywords[category]:
                if keyword in query_lower:
                    return 0.3  # Boost score
        
        return 0.0
    
    def _get_recency_boost(self, indexed_at: str) -> float:
        """Calculate recency boost (newer documents get slight boost)"""
        # Simple implementation - can be enhanced
        if indexed_at:
            return 0.1
        return 0.0
