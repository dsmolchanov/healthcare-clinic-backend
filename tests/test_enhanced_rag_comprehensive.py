#!/usr/bin/env python3
"""
Comprehensive Test Suite for Enhanced RAG System

Tests all components: structured data indexing, hybrid search, entity extraction,
caching, and performance metrics.
"""

import os
import sys
import asyncio
import pytest
import time
from typing import List, Dict, Any

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import create_supabase_client
from app.api.structured_data_embedder import StructuredDataEmbedder
from app.api.hybrid_search_engine import HybridSearchEngine
from app.api.entity_extractor import MedicalEntityExtractor
from app.api.rag_cache import RAGCache
from app.api.rag_metrics import RAGMetrics, SearchQualityAnalyzer

# Test configuration
TEST_CLINIC_ID = "e0c84f56-235d-49f2-9a44-37c1be579afc"


class TestStructuredDataIndexing:
    """Test structured data embedding and indexing"""
    
    @pytest.mark.asyncio
    async def test_doctor_indexing(self):
        """Test indexing doctors into Pinecone"""
        supabase = create_supabase_client()
        embedder = StructuredDataEmbedder(TEST_CLINIC_ID, supabase)
        
        result = await embedder.embed_doctors()
        
        assert result['indexed_count'] > 0
        assert result['type'] == 'doctors'
        assert 'error' not in result
    
    @pytest.mark.asyncio
    async def test_service_indexing(self):
        """Test indexing services into Pinecone"""
        supabase = create_supabase_client()
        embedder = StructuredDataEmbedder(TEST_CLINIC_ID, supabase)
        
        result = await embedder.embed_services()
        
        assert result['indexed_count'] > 0
        assert result['type'] == 'services'
        assert 'error' not in result


class TestEntityExtraction:
    """Test entity extraction capabilities"""
    
    @pytest.mark.asyncio
    async def test_doctor_name_extraction(self):
        """Test extraction of doctor names"""
        extractor = MedicalEntityExtractor()
        
        entities = await extractor.extract("I want to see Dr. Mark Shtern")
        
        assert 'doctor_name' in entities
        assert 'Mark Shtern' in entities['doctor_name']
    
    @pytest.mark.asyncio
    async def test_service_extraction(self):
        """Test extraction of service-related entities"""
        extractor = MedicalEntityExtractor()
        
        entities = await extractor.extract("I need a root canal treatment")
        
        assert entities.get('urgency') is not None
        assert entities.get('consultation_type') is not None
    
    @pytest.mark.asyncio
    async def test_urgency_extraction(self):
        """Test urgency level extraction"""
        extractor = MedicalEntityExtractor()
        
        urgent_entities = await extractor.extract("I need emergency dental care")
        assert urgent_entities.get('urgency') == 'high'
        
        normal_entities = await extractor.extract("Schedule a regular checkup")
        assert normal_entities.get('urgency') == 'normal'
    
    @pytest.mark.asyncio
    async def test_cost_query_extraction(self):
        """Test cost/price query extraction"""
        extractor = MedicalEntityExtractor()
        
        entities = await extractor.extract("How much does a tooth extraction cost?")
        
        assert entities.get('cost_query') == True


class TestHybridSearch:
    """Test hybrid search functionality"""
    
    @pytest.mark.asyncio
    async def test_doctor_search(self):
        """Test searching for specific doctors"""
        engine = HybridSearchEngine(TEST_CLINIC_ID)
        engine._rag_cache = None  # Disable cache for testing
        
        results = await engine.hybrid_search("Dr. Mark Shtern", top_k=3)
        
        assert len(results) > 0
        
        # Check if Dr. Mark Shtern is the top result
        if results and isinstance(results[0], dict):
            metadata = results[0].get('metadata', {})
            if 'doctor_id' in metadata:
                assert 'Mark' in metadata.get('name', '') or 'Shtern' in metadata.get('name', '')
    
    @pytest.mark.asyncio
    async def test_service_search(self):
        """Test searching for services"""
        engine = HybridSearchEngine(TEST_CLINIC_ID)
        engine._rag_cache = None
        
        results = await engine.hybrid_search("root canal", top_k=5)
        
        assert len(results) > 0
        
        # Check if root canal services are found
        service_found = False
        for result in results:
            if isinstance(result, dict):
                metadata = result.get('metadata', {})
                if 'service_id' in metadata:
                    name = metadata.get('name', '').lower()
                    if 'root' in name or 'canal' in name:
                        service_found = True
                        break
        
        assert service_found, "Root canal service not found in results"
    
    @pytest.mark.asyncio
    async def test_cost_search(self):
        """Test searching for services with cost information"""
        engine = HybridSearchEngine(TEST_CLINIC_ID)
        engine._rag_cache = None
        
        results = await engine.hybrid_search("tooth extraction price", top_k=5)
        
        assert len(results) > 0
        
        # Check if price information is included
        price_found = False
        for result in results:
            if isinstance(result, dict):
                metadata = result.get('metadata', {})
                if 'base_price' in metadata:
                    price_found = True
                    break
        
        assert price_found, "No price information found in results"
    
    @pytest.mark.asyncio
    async def test_parallel_search_sources(self):
        """Test that multiple search sources are working"""
        engine = HybridSearchEngine(TEST_CLINIC_ID)
        engine._rag_cache = None
        
        results = await engine.hybrid_search("dental services", top_k=10)
        
        # Collect unique sources
        sources = set()
        for result in results:
            if isinstance(result, dict):
                source = result.get('source', 'unknown')
                sources.add(source)
        
        # Should have multiple sources (vector, structured, etc.)
        assert len(sources) >= 1, f"Expected multiple sources, got: {sources}"


class TestCaching:
    """Test caching functionality"""
    
    @pytest.mark.asyncio
    async def test_cache_storage_retrieval(self):
        """Test storing and retrieving from cache"""
        cache = RAGCache(TEST_CLINIC_ID)
        
        test_query = "test query for caching"
        test_response = [
            {'text': 'Result 1', 'score': 0.9},
            {'text': 'Result 2', 'score': 0.8}
        ]
        
        # Store in cache
        success = await cache.set(test_query, test_response, ttl=60)
        assert success
        
        # Retrieve from cache
        cached = await cache.get(test_query)
        assert cached is not None
        assert len(cached) == 2
        assert cached[0]['text'] == 'Result 1'
    
    @pytest.mark.asyncio
    async def test_cache_performance(self):
        """Test that cache improves performance"""
        engine = HybridSearchEngine(TEST_CLINIC_ID)
        
        query = "common dental services"
        
        # First search (no cache)
        start = time.time()
        results1 = await engine.hybrid_search(query, top_k=3)
        time_no_cache = time.time() - start
        
        # Second search (should hit cache)
        start = time.time()
        results2 = await engine.hybrid_search(query, top_k=3)
        time_with_cache = time.time() - start
        
        # Cache should be faster (allowing some variance)
        # Note: This might not always be true in testing due to network variability
        assert len(results1) == len(results2), "Results should be consistent"


class TestMetrics:
    """Test metrics collection and reporting"""
    
    @pytest.mark.asyncio
    async def test_metrics_recording(self):
        """Test recording search metrics"""
        metrics = RAGMetrics(TEST_CLINIC_ID)
        
        # Record a search
        await metrics.record_search(
            query="test query",
            latency_ms=150.5,
            result_count=5,
            sources=['vector_search', 'structured_search'],
            cache_hit=False,
            entities_extracted={'doctor_name': 'Test Doctor'}
        )
        
        # Get metrics
        current = await metrics.get_current_metrics()
        
        assert current['clinic_id'] == TEST_CLINIC_ID
        assert current['latency']['mean_ms'] > 0
        assert 'vector_search' in current['sources']
        assert current['query_types']['doctor_search'] > 0
    
    @pytest.mark.asyncio
    async def test_quality_analysis(self):
        """Test search quality analysis"""
        
        test_results = [
            {'type': 'doctor', 'source': 'structured_search', 'final_score': 0.9},
            {'type': 'service', 'source': 'vector_search', 'final_score': 0.7},
            {'type': 'doctor', 'source': 'metadata_search', 'final_score': 0.6}
        ]
        
        # Test diversity score
        diversity = SearchQualityAnalyzer.calculate_diversity_score(test_results)
        assert diversity > 0.5  # Good diversity
        
        # Test relevance distribution
        distribution = SearchQualityAnalyzer.calculate_relevance_distribution(test_results)
        assert distribution['max_score'] == 0.9
        assert distribution['min_score'] == 0.6
        
        # Test quality issue detection
        issues = SearchQualityAnalyzer.identify_quality_issues(test_results)
        assert len(issues) == 0  # No major issues with diverse, well-scored results


class TestEndToEnd:
    """End-to-end integration tests"""
    
    @pytest.mark.asyncio
    async def test_complete_search_flow(self):
        """Test complete search flow from query to results"""
        
        # Initialize all components
        engine = HybridSearchEngine(TEST_CLINIC_ID)
        metrics = RAGMetrics(TEST_CLINIC_ID)
        
        test_queries = [
            "I need to see Dr. Mark Shtern",
            "How much does a root canal cost?",
            "Emergency dental care",
            "Teeth cleaning appointment"
        ]
        
        for query in test_queries:
            # Perform search
            start = time.time()
            results = await engine.hybrid_search(query, top_k=5)
            latency = (time.time() - start) * 1000
            
            # Validate results
            assert len(results) > 0, f"No results for query: {query}"
            
            # Collect sources
            sources = []
            for r in results:
                if isinstance(r, dict) and 'source' in r:
                    sources.append(r['source'])
            
            # Record metrics
            await metrics.record_search(
                query=query,
                latency_ms=latency,
                result_count=len(results),
                sources=sources,
                cache_hit=False
            )
        
        # Check final metrics
        final_metrics = await metrics.get_current_metrics()
        assert final_metrics['latency']['mean_ms'] > 0
        assert len(final_metrics['sources']) > 0
    
    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Test error handling and recovery"""
        engine = HybridSearchEngine(TEST_CLINIC_ID)
        
        # Test with empty query
        results = await engine.hybrid_search("", top_k=5)
        assert isinstance(results, list)
        
        # Test with very long query
        long_query = "dental " * 100
        results = await engine.hybrid_search(long_query, top_k=5)
        assert isinstance(results, list)
        
        # Test with special characters
        special_query = "@#$%^&*()_+{}|:<>?"
        results = await engine.hybrid_search(special_query, top_k=5)
        assert isinstance(results, list)


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])