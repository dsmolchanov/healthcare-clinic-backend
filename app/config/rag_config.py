# clinics/backend/app/config/rag_config.py
"""
RAG System Configuration
========================
Centralized configuration for RAG parameters
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class RAGConfig:
    """RAG system configuration"""
    
    # Similarity thresholds
    primary_similarity_threshold: float = 0.3  # Lower for better recall
    fallback_similarity_threshold: float = 0.4  # Slightly higher for fallback
    
    # Search parameters
    primary_top_k: int = 5  # Number of results to retrieve
    fallback_top_k: int = 10  # More results for fallback
    max_results_to_use: int = 3  # Maximum results to include in context
    
    # Embedding model
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    
    # Caching
    enable_embedding_cache: bool = True
    cache_ttl_seconds: int = 3600  # 1 hour
    
    # Performance
    enable_reranking: bool = True
    enable_fallback_search: bool = True
    
    # Monitoring
    log_search_metrics: bool = True
    log_slow_queries_threshold_ms: float = 1000.0


# Default configuration
DEFAULT_RAG_CONFIG = RAGConfig()


# Environment-specific configurations
def get_rag_config(environment: Optional[str] = None) -> RAGConfig:
    """Get RAG configuration for specific environment"""
    
    if environment == "production":
        return RAGConfig(
            primary_similarity_threshold=0.35,  # Slightly higher for production
            enable_embedding_cache=True,
            log_search_metrics=True
        )
    elif environment == "development":
        return RAGConfig(
            primary_similarity_threshold=0.25,  # Lower for testing
            log_search_metrics=True,
            log_slow_queries_threshold_ms=500.0
        )
    else:
        return DEFAULT_RAG_CONFIG
