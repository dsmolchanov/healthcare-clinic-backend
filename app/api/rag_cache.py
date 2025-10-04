"""
RAG Cache for Healthcare System

Provides intelligent caching for RAG queries to improve performance and reduce
API costs. Implements semantic similarity caching and time-based invalidation.
"""

import os
import json
import hashlib
import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
import redis
from redis import Redis
import numpy as np
from openai import OpenAI

logger = logging.getLogger(__name__)


class RAGCache:
    """Intelligent caching system for RAG queries with semantic similarity"""
    
    def __init__(self, clinic_id: str, redis_url: Optional[str] = None):
        """Initialize RAG cache
        
        Args:
            clinic_id: Clinic identifier for cache isolation
            redis_url: Redis connection URL (defaults to env variable)
        """
        self.clinic_id = clinic_id
        self.cache_prefix = f"rag_cache:{clinic_id}"
        
        # Initialize Redis connection
        redis_url = redis_url or os.environ.get('REDIS_URL', 'redis://localhost:6379')
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        
        # Initialize OpenAI for embeddings
        self.openai = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
        
        # Cache configuration
        self.ttl_seconds = 3600  # 1 hour default TTL
        self.similarity_threshold = 0.95  # High similarity for cache hits
        self.max_cache_size = 1000  # Max cached items per clinic
        
        # Performance metrics
        self.metrics = {
            'hits': 0,
            'misses': 0,
            'semantic_hits': 0,
            'expired_evictions': 0
        }
    
    async def get(
        self, 
        query: str, 
        use_semantic: bool = True
    ) -> Optional[Dict[str, Any]]:
        """Retrieve cached response for query
        
        Args:
            query: The search query
            use_semantic: Whether to use semantic similarity matching
            
        Returns:
            Cached response if found, None otherwise
        """
        try:
            # Try exact match first
            cache_key = self._generate_key(query)
            cached_data = self.redis_client.get(cache_key)
            
            if cached_data:
                self.metrics['hits'] += 1
                try:
                    data = json.loads(cached_data)
                    # Make sure we return the response, not the entire cache object
                    result = data.get('response') if isinstance(data, dict) and 'response' in data else data

                    # Update access time for LRU
                    self._update_access_time(cache_key)

                    logger.info(f"Cache hit for query: {query[:50]}...")
                    return result
                except:
                    # If cache data is corrupted, ignore and continue
                    logger.warning(f"Corrupted cache data for query: {query[:50]}...")
            
            # Try semantic similarity if enabled
            if use_semantic:
                semantic_result = await self._semantic_search(query)
                if semantic_result:
                    self.metrics['semantic_hits'] += 1
                    logger.info(f"Semantic cache hit for query: {query[:50]}...")
                    return semantic_result
            
            self.metrics['misses'] += 1
            return None
            
        except Exception as e:
            logger.error(f"Cache retrieval error: {e}")
            return None
    
    async def set(
        self,
        query: str,
        response: Any,  # Can be Dict or List
        ttl: Optional[int] = None
    ) -> bool:
        """Store response in cache
        
        Args:
            query: The search query
            response: The response to cache
            ttl: Time to live in seconds (optional)
            
        Returns:
            True if successfully cached
        """
        try:
            # Check cache size limit
            await self._enforce_size_limit()
            
            # Generate cache key
            cache_key = self._generate_key(query)
            
            # Generate embedding for semantic search
            embedding = await self._generate_embedding(query)
            
            # Prepare cache data
            cache_data = {
                'query': query,
                'response': response,
                'embedding': embedding.tolist() if isinstance(embedding, np.ndarray) else embedding,
                'timestamp': datetime.utcnow().isoformat(),
                'access_count': 0
            }
            
            # Store in Redis
            ttl = ttl or self.ttl_seconds
            self.redis_client.setex(
                cache_key,
                ttl,
                json.dumps(cache_data)
            )
            
            # Store embedding in separate sorted set for similarity search
            embedding_key = f"{self.cache_prefix}:embeddings"
            self.redis_client.zadd(
                embedding_key,
                {cache_key: datetime.utcnow().timestamp()}
            )
            
            # Store embedding vector
            vector_key = f"{cache_key}:vector"
            self.redis_client.setex(
                vector_key,
                ttl,
                json.dumps(embedding.tolist() if isinstance(embedding, np.ndarray) else embedding)
            )
            
            logger.debug(f"Cached response for query: {query[:50]}...")
            return True
            
        except Exception as e:
            logger.error(f"Cache storage error: {e}")
            return False
    
    async def invalidate_pattern(self, pattern: str) -> int:
        """Invalidate cache entries matching pattern
        
        Args:
            pattern: Pattern to match (e.g., "doctor*" for all doctor queries)
            
        Returns:
            Number of entries invalidated
        """
        try:
            # Find matching keys
            search_pattern = f"{self.cache_prefix}:{pattern}"
            keys = self.redis_client.keys(search_pattern)
            
            if keys:
                # Delete matching entries
                deleted = self.redis_client.delete(*keys)
                
                # Clean up embeddings
                embedding_key = f"{self.cache_prefix}:embeddings"
                for key in keys:
                    self.redis_client.zrem(embedding_key, key)
                    
                    # Delete vector
                    vector_key = f"{key}:vector"
                    self.redis_client.delete(vector_key)
                
                logger.info(f"Invalidated {deleted} cache entries matching '{pattern}'")
                return deleted
            
            return 0
            
        except Exception as e:
            logger.error(f"Cache invalidation error: {e}")
            return 0
    
    async def invalidate_structured_data(self, data_type: str) -> int:
        """Invalidate cache when structured data changes
        
        Args:
            data_type: Type of data changed ('doctors', 'services', etc.)
            
        Returns:
            Number of entries invalidated
        """
        patterns = {
            'doctors': ['*doctor*', '*physician*', '*appointment*'],
            'services': ['*service*', '*procedure*', '*treatment*'],
            'availability': ['*available*', '*schedule*', '*slot*']
        }
        
        total_invalidated = 0
        for pattern in patterns.get(data_type, [data_type]):
            total_invalidated += await self.invalidate_pattern(pattern)
        
        return total_invalidated
    
    async def clear_all(self) -> bool:
        """Clear all cache entries for the clinic
        
        Returns:
            True if successfully cleared
        """
        try:
            # Find all cache keys
            pattern = f"{self.cache_prefix}:*"
            keys = self.redis_client.keys(pattern)
            
            if keys:
                self.redis_client.delete(*keys)
                logger.info(f"Cleared {len(keys)} cache entries for clinic {self.clinic_id}")
            
            # Reset metrics
            self.metrics = {
                'hits': 0,
                'misses': 0,
                'semantic_hits': 0,
                'expired_evictions': 0
            }
            
            return True
            
        except Exception as e:
            logger.error(f"Cache clear error: {e}")
            return False
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get cache performance metrics
        
        Returns:
            Dictionary of performance metrics
        """
        total_requests = self.metrics['hits'] + self.metrics['misses']
        
        return {
            **self.metrics,
            'hit_rate': self.metrics['hits'] / total_requests if total_requests > 0 else 0,
            'semantic_hit_rate': self.metrics['semantic_hits'] / total_requests if total_requests > 0 else 0,
            'total_requests': total_requests
        }
    
    async def _generate_embedding(self, text: str) -> np.ndarray:
        """Generate embedding for text
        
        Args:
            text: Text to embed
            
        Returns:
            Embedding vector
        """
        try:
            response = self.openai.embeddings.create(
                model="text-embedding-3-small",
                input=text
            )
            return np.array(response.data[0].embedding)
        except Exception as e:
            logger.error(f"Embedding generation error: {e}")
            # Return zero vector as fallback
            return np.zeros(1536)
    
    async def _semantic_search(self, query: str) -> Optional[Dict[str, Any]]:
        """Search for semantically similar cached queries
        
        Args:
            query: The search query
            
        Returns:
            Cached response if similar query found
        """
        try:
            # Generate embedding for query
            query_embedding = await self._generate_embedding(query)
            
            # Get all cached embeddings
            embedding_key = f"{self.cache_prefix}:embeddings"
            cache_keys = self.redis_client.zrange(embedding_key, 0, -1)
            
            best_similarity = 0
            best_result = None
            
            for cache_key in cache_keys:
                # Get cached vector
                vector_key = f"{cache_key}:vector"
                vector_data = self.redis_client.get(vector_key)
                
                if not vector_data:
                    continue
                
                cached_embedding = np.array(json.loads(vector_data))
                
                # Calculate cosine similarity
                similarity = self._cosine_similarity(query_embedding, cached_embedding)
                
                if similarity > self.similarity_threshold and similarity > best_similarity:
                    # Get cached data
                    cached_data = self.redis_client.get(cache_key)
                    if cached_data:
                        best_similarity = similarity
                        data = json.loads(cached_data)
                        best_result = data['response']
            
            return best_result
            
        except Exception as e:
            logger.error(f"Semantic search error: {e}")
            return None
    
    def _cosine_similarity(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
        """Calculate cosine similarity between vectors
        
        Args:
            vec1: First vector
            vec2: Second vector
            
        Returns:
            Cosine similarity score
        """
        try:
            dot_product = np.dot(vec1, vec2)
            norm1 = np.linalg.norm(vec1)
            norm2 = np.linalg.norm(vec2)
            
            if norm1 == 0 or norm2 == 0:
                return 0
            
            return dot_product / (norm1 * norm2)
        except:
            return 0
    
    def _generate_key(self, query: str) -> str:
        """Generate cache key for query
        
        Args:
            query: The search query
            
        Returns:
            Cache key
        """
        # Create hash of query for consistent keys
        query_hash = hashlib.md5(query.encode()).hexdigest()
        return f"{self.cache_prefix}:{query_hash}"
    
    def _update_access_time(self, cache_key: str):
        """Update access time for LRU eviction
        
        Args:
            cache_key: The cache key
        """
        try:
            # Update access count
            cached_data = self.redis_client.get(cache_key)
            if cached_data:
                data = json.loads(cached_data)
                data['access_count'] = data.get('access_count', 0) + 1
                data['last_access'] = datetime.utcnow().isoformat()
                
                # Get remaining TTL
                ttl = self.redis_client.ttl(cache_key)
                if ttl > 0:
                    self.redis_client.setex(cache_key, ttl, json.dumps(data))
        except Exception as e:
            logger.error(f"Access time update error: {e}")
    
    async def _enforce_size_limit(self):
        """Enforce cache size limit using LRU eviction"""
        try:
            # Get all cache keys
            pattern = f"{self.cache_prefix}:*"
            keys = [k for k in self.redis_client.keys(pattern) if not k.endswith(':vector')]
            
            if len(keys) >= self.max_cache_size:
                # Get access times
                key_times = []
                for key in keys:
                    cached_data = self.redis_client.get(key)
                    if cached_data:
                        data = json.loads(cached_data)
                        last_access = data.get('last_access', data.get('timestamp'))
                        key_times.append((key, last_access))
                
                # Sort by access time
                key_times.sort(key=lambda x: x[1])
                
                # Evict oldest 10%
                evict_count = max(1, len(keys) // 10)
                for key, _ in key_times[:evict_count]:
                    self.redis_client.delete(key)
                    
                    # Clean up vector
                    vector_key = f"{key}:vector"
                    self.redis_client.delete(vector_key)
                    
                    # Remove from embeddings set
                    embedding_key = f"{self.cache_prefix}:embeddings"
                    self.redis_client.zrem(embedding_key, key)
                
                logger.info(f"Evicted {evict_count} cache entries for size limit")
                self.metrics['expired_evictions'] += evict_count
                
        except Exception as e:
            logger.error(f"Size limit enforcement error: {e}")


class QueryNormalizer:
    """Normalize queries for better cache hit rates"""
    
    @staticmethod
    def normalize(query: str) -> str:
        """Normalize query for caching
        
        Args:
            query: Original query
            
        Returns:
            Normalized query
        """
        # Convert to lowercase
        normalized = query.lower()
        
        # Remove extra whitespace
        normalized = ' '.join(normalized.split())
        
        # Remove common variations
        replacements = {
            "what's": "what is",
            "doctor's": "doctor",
            "i'm": "i am",
            "i'd": "i would",
            "can't": "cannot",
            "won't": "will not",
            "shouldn't": "should not",
            "wouldn't": "would not",
            "couldn't": "could not"
        }
        
        for old, new in replacements.items():
            normalized = normalized.replace(old, new)
        
        # Remove punctuation at the end
        normalized = normalized.rstrip('.,!?')
        
        return normalized