# Enhanced RAG System Documentation

## Overview

The Enhanced RAG (Retrieval-Augmented Generation) System provides intelligent knowledge retrieval for healthcare conversational AI, combining traditional vector search with structured data queries, entity extraction, caching, and comprehensive metrics.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    User Query                             │
└────────────────┬──────────────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────────────────────┐
│              Entity Extractor                             │
│   • Medical NER (SpaCy + Custom Rules)                    │
│   • Doctor names, services, symptoms, urgency             │
└────────────────┬──────────────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────────────────────┐
│              RAG Cache Layer                              │
│   • Redis with semantic similarity                        │
│   • TTL-based expiration                                  │
│   • LRU eviction                                          │
└────────────────┬──────────────────────────────────────────┘
                 │ (cache miss)
                 ▼
┌──────────────────────────────────────────────────────────┐
│           Hybrid Search Engine                            │
│                                                           │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│   │Vector Search │  │ Structured    │  │  Metadata    │  │
│   │  (Pinecone)  │  │   Search      │  │   Search     │  │
│   │              │  │  (Supabase)   │  │ (Doctors,    │  │
│   │  Embeddings  │  │               │  │  Services)   │  │
│   └──────────────┘  └──────────────┘  └──────────────┘  │
│                                                           │
│                    Parallel Execution                     │
└────────────────┬──────────────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────────────────────┐
│              Result Re-ranking                            │
│   • Multi-factor scoring                                  │
│   • Source diversity                                      │
│   • Recency weighting                                     │
└────────────────┬──────────────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────────────────────┐
│              Metrics & Analytics                          │
│   • Latency tracking                                      │
│   • Quality scoring                                       │
│   • Source distribution                                   │
└────────────────┬──────────────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────────────────────┐
│              Formatted Response                           │
└────────────────────────────────────────────────────────────┘
```

## Components

### 1. Entity Extractor (`app/api/entity_extractor.py`)

Intelligent extraction of medical entities from user queries.

**Features:**
- Medical NER using SpaCy with custom patterns
- Doctor name extraction with fuzzy matching
- Service category identification
- Urgency level detection
- Cost/price query recognition
- Symptom and body part extraction

**Example:**
```python
extractor = MedicalEntityExtractor()
entities = await extractor.extract("I need to see Dr. Mark Shtern for a root canal")
# Returns:
{
    "doctor_name": "Mark Shtern",
    "service_category": "endodontic",
    "consultation_type": "procedure",
    "urgency": "normal"
}
```

### 2. Structured Data Embedder (`app/api/structured_data_embedder.py`)

Indexes structured data from Supabase into Pinecone for vector search.

**Features:**
- Automatic doctor profile indexing
- Service catalog embedding
- Rich metadata preservation
- Incremental updates
- Batch processing

**API Endpoints:**
```python
POST /api/knowledge/index/doctors
POST /api/knowledge/index/services
POST /api/knowledge/index/all
```

### 3. Hybrid Search Engine (`app/api/hybrid_search_engine.py`)

Combines multiple search strategies for optimal retrieval.

**Search Strategies:**
1. **Vector Search**: Semantic similarity using embeddings
2. **Structured Search**: Direct database queries for exact matches
3. **Metadata Search**: Filtering by attributes (specialization, price range)
4. **Patient Context**: Personalized results based on history

**Features:**
- Parallel search execution
- Dynamic strategy selection based on entities
- Result de-duplication
- Multi-factor relevance scoring
- Source diversity optimization

**Example:**
```python
engine = HybridSearchEngine(clinic_id)
results = await engine.hybrid_search(
    query="root canal treatment cost",
    top_k=5,
    use_patient_context=True,
    patient_id="patient_123"
)
```

### 4. RAG Cache (`app/api/rag_cache.py`)

High-performance caching with semantic similarity.

**Features:**
- Redis-based storage
- Semantic similarity matching (cosine similarity > 0.95)
- TTL-based expiration (default 1 hour)
- LRU eviction policy
- Query normalization
- Cache invalidation patterns

**Cache Operations:**
```python
cache = RAGCache(clinic_id)

# Store result
await cache.set(query, response, ttl=3600)

# Retrieve with semantic matching
result = await cache.get(query, use_semantic=True)

# Invalidate pattern
await cache.invalidate_pattern("doctor*")

# Clear all
await cache.clear_all()
```

### 5. RAG Metrics (`app/api/rag_metrics.py`)

Comprehensive monitoring and quality analysis.

**Tracked Metrics:**
- Search latency (P50, P95, P99)
- Cache hit rates
- Result quality scores
- Source distribution
- Query type classification
- Error tracking

**Quality Indicators:**
- Result diversity score
- Relevance distribution
- Score consistency
- Zero-result rate

**Example Dashboard:**
```python
metrics = RAGMetrics(clinic_id)
summary = await metrics.get_current_metrics()
# Returns:
{
    "latency": {
        "mean_ms": 150.5,
        "p95_ms": 280.0
    },
    "cache": {
        "hit_rate": 0.65,
        "semantic_hits": 120
    },
    "sources": {
        "vector_search": 450,
        "structured_search": 230,
        "metadata_search": 180
    }
}
```

## Integration

### Message Processor Integration

The enhanced RAG system integrates with the message processor:

```python
# In multilingual_message_processor.py
from app.api.hybrid_search_engine import HybridSearchEngine

class MultilingualMessageProcessor:
    async def _get_knowledge_context(self, message: str, patient_id: str):
        # Use hybrid search instead of basic vector search
        engine = HybridSearchEngine(self.clinic_id)
        results = await engine.hybrid_search(
            query=message,
            top_k=5,
            use_patient_context=True,
            patient_id=patient_id
        )
        return self._format_knowledge_context(results)
```

## Configuration

### Environment Variables

```bash
# OpenAI for embeddings
OPENAI_API_KEY=sk-xxx

# Pinecone vector database
PINECONE_API_KEY=xxx
PINECONE_ENV=us-east1-gcp

# Redis for caching
REDIS_URL=redis://localhost:6379

# Supabase for structured data
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_ANON_KEY=xxx

# Performance tuning
RAG_CACHE_TTL=3600
RAG_CACHE_MAX_SIZE=1000
RAG_SIMILARITY_THRESHOLD=0.95
```

### Database Requirements

Required tables in Supabase:
- `doctors`: Doctor profiles with specializations
- `services`: Service catalog with pricing
- `appointments`: Appointment history
- `conversation_sessions`: Patient conversation history

## Usage Examples

### 1. Basic Query

```python
from app.api.hybrid_search_engine import HybridSearchEngine

engine = HybridSearchEngine(clinic_id="e0c84f56-235d-49f2-9a44-37c1be579afc")
results = await engine.hybrid_search("I need a dentist", top_k=3)
```

### 2. Doctor-Specific Search

```python
results = await engine.hybrid_search("Dr. Mark Shtern availability", top_k=1)
# Returns doctor profile with metadata
```

### 3. Service with Pricing

```python
results = await engine.hybrid_search("How much does a root canal cost?", top_k=5)
# Returns services with base_price in metadata
```

### 4. Emergency Query

```python
results = await engine.hybrid_search("Emergency dental care needed", top_k=3)
# Entities include urgency="high", results prioritize emergency services
```

## Performance Optimization

### Caching Strategy

1. **Query Normalization**: Standardize queries for better cache hits
2. **Semantic Matching**: Find similar cached queries
3. **TTL Management**: Shorter TTL for frequently changing data
4. **Invalidation Patterns**: Clear related entries on data updates

### Search Optimization

1. **Parallel Execution**: All search strategies run concurrently
2. **Early Termination**: Stop when confidence threshold met
3. **Batch Processing**: Index updates in batches
4. **Connection Pooling**: Reuse database connections

### Scaling Considerations

1. **Redis Cluster**: For high-volume caching
2. **Read Replicas**: Distribute database queries
3. **Index Sharding**: Partition Pinecone indexes by clinic
4. **Async Processing**: Non-blocking I/O throughout

## Monitoring

### Key Metrics to Track

```yaml
Performance:
  - Search latency (target < 200ms P95)
  - Cache hit rate (target > 60%)
  - Zero result rate (target < 5%)

Quality:
  - Result diversity score (target > 0.5)
  - Relevance score distribution
  - Entity extraction accuracy

Usage:
  - Queries per minute
  - Source distribution
  - Query type breakdown
```

### Health Checks

```python
GET /api/knowledge/health
Response: {
    "status": "healthy",
    "cache_connected": true,
    "pinecone_connected": true,
    "database_connected": true,
    "indexed_doctors": 25,
    "indexed_services": 150
}
```

## Testing

### Unit Tests

```bash
# Test individual components
pytest tests/test_entity_extractor.py
pytest tests/test_hybrid_search.py
pytest tests/test_rag_cache.py
```

### Integration Tests

```bash
# Test complete flow
pytest tests/test_enhanced_rag_comprehensive.py
```

### Load Testing

```python
# Simulate concurrent queries
python tests/load/test_rag_load.py --users 100 --duration 60
```

## Troubleshooting

### Common Issues

1. **Low Cache Hit Rate**
   - Check query normalization
   - Increase similarity threshold
   - Review TTL settings

2. **Slow Search Performance**
   - Check Pinecone index size
   - Review database indexes
   - Enable connection pooling

3. **Poor Result Quality**
   - Update entity extraction patterns
   - Retrain embeddings
   - Adjust scoring weights

4. **Memory Issues**
   - Implement cache size limits
   - Use streaming for large results
   - Enable Redis eviction policy

### Debug Commands

```python
# Check entity extraction
python -m app.api.entity_extractor "test query"

# Test search strategies
python -m app.api.hybrid_search_engine --debug "test query"

# Analyze cache performance
python -m app.api.rag_cache --stats

# Review metrics
python -m app.api.rag_metrics --report
```

## API Reference

### Endpoints

```python
# Index structured data
POST /api/knowledge/index/doctors
POST /api/knowledge/index/services
POST /api/knowledge/index/all

# Query knowledge base
POST /api/knowledge/query
Body: {
    "query": "string",
    "top_k": 5,
    "use_cache": true,
    "patient_id": "optional"
}

# Get metrics
GET /api/knowledge/metrics

# Health check
GET /api/knowledge/health
```

## Migration Guide

### From Basic RAG to Enhanced RAG

1. **Update imports:**
```python
# Old
from app.api.improved_knowledge_base import ImprovedPineconeKnowledgeBase

# New
from app.api.hybrid_search_engine import HybridSearchEngine
```

2. **Update search calls:**
```python
# Old
kb = ImprovedPineconeKnowledgeBase(clinic_id)
results = await kb.search(query)

# New
engine = HybridSearchEngine(clinic_id)
results = await engine.hybrid_search(query, top_k=5)
```

3. **Index existing data:**
```bash
# Run indexing scripts
python scripts/index_structured_data.py
```

## Best Practices

1. **Entity Extraction**
   - Keep patterns updated with common queries
   - Test extraction accuracy regularly
   - Add domain-specific terms

2. **Caching**
   - Cache expensive queries
   - Invalidate on data changes
   - Monitor hit rates

3. **Search Strategies**
   - Balance between precision and recall
   - Use appropriate top_k values
   - Consider user context

4. **Monitoring**
   - Track key metrics
   - Set up alerts for anomalies
   - Regular performance reviews

## Future Enhancements

1. **Multi-language Support**
   - Embed in multiple languages
   - Cross-lingual search
   - Language-specific entity extraction

2. **Advanced Personalization**
   - User preference learning
   - Collaborative filtering
   - Contextual bandits

3. **Active Learning**
   - Query expansion
   - Relevance feedback
   - Model fine-tuning

4. **Graph-based Retrieval**
   - Knowledge graph integration
   - Relationship-aware search
   - Multi-hop reasoning

---

*Version: 1.0.0*
*Last Updated: 2025-01-27*