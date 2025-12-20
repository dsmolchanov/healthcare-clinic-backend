#!/usr/bin/env python3
"""
RAG Evaluation System
=====================
Comprehensive evaluation framework for testing and debugging RAG retrieval issues.

Features:
- Document ingestion testing
- Retrieval accuracy measurement
- Relevance scoring evaluation
- Performance benchmarking
- Debug mode with detailed logging
"""

import os
import sys
import json
import time
import asyncio
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass
import numpy as np
from pathlib import Path
import logging

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.knowledge_ingestion import KnowledgeIngestionPipeline
from app.api.multilingual_message_processor import PineconeKnowledgeBase
from app.memory.conversation_manager import ConversationContextManager
import pinecone
from openai import OpenAI
from dotenv import load_dotenv

# Load environment
load_dotenv('../.env')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class TestDocument:
    """Test document for evaluation"""
    content: str
    category: str
    expected_queries: List[str]
    metadata: Dict[str, Any]


@dataclass
class EvaluationResult:
    """Result of a single evaluation"""
    query: str
    expected_doc: str
    retrieved_docs: List[Dict]
    relevance_scores: List[float]
    hit_at_k: Dict[int, bool]  # k=1,3,5,10
    latency_ms: float
    success: bool
    error: Optional[str] = None


class RAGEvaluator:
    """Main RAG evaluation system"""
    
    def __init__(self, clinic_id: str = "3e411ecb-3411-4add-91e2-8fa897310cb0"):
        self.clinic_id = clinic_id
        self.kb = PineconeKnowledgeBase(clinic_id)
        self.ingestion = KnowledgeIngestionPipeline(clinic_id)
        self.openai = OpenAI()
        self.results = []
        
        # Test data
        self.test_documents = self._create_test_documents()
        
    def _create_test_documents(self) -> List[TestDocument]:
        """Create test documents with known content and expected queries"""
        return [
            TestDocument(
                content="""
                Dr. Shtern Dental Clinic Services:
                - Professional teeth cleaning: $150
                - Dental examination and X-rays: $200
                - Tooth extraction: $300-500
                - Root canal treatment: $800-1200
                - Dental crowns: $1000-1500
                - Teeth whitening: $400
                All prices include consultation.
                """,
                category="services",
                expected_queries=[
                    "What services do you offer?",
                    "How much does teeth cleaning cost?",
                    "What are your prices?",
                    "Do you do root canals?"
                ],
                metadata={"type": "pricing", "language": "en"}
            ),
            TestDocument(
                content="""
                Insurance Information:
                We accept the following insurance plans:
                - Delta Dental
                - MetLife  
                - Cigna
                - Aetna
                - Blue Cross Blue Shield
                - Guardian
                - United Healthcare
                Please bring your insurance card to your appointment.
                We will verify coverage before treatment.
                """,
                category="insurance",
                expected_queries=[
                    "What insurance do you accept?",
                    "Do you take Delta Dental?",
                    "Is my insurance accepted?",
                    "Insurance coverage"
                ],
                metadata={"type": "insurance", "language": "en"}
            ),
            TestDocument(
                content="""
                Appointment Policy:
                - New patients should arrive 15 minutes early
                - Please call 24 hours in advance to cancel
                - Late cancellations may incur a $50 fee
                - Emergency appointments available same day
                - Online booking available 24/7
                - Weekend appointments available
                """,
                category="policies",
                expected_queries=[
                    "How do I book an appointment?",
                    "What's your cancellation policy?",
                    "Do you have weekend appointments?",
                    "Emergency appointment"
                ],
                metadata={"type": "policy", "language": "en"}
            ),
            TestDocument(
                content="""
                Post-Extraction Care Instructions:
                After tooth extraction, follow these steps:
                1. Bite on gauze for 30-45 minutes
                2. Apply ice packs to reduce swelling
                3. Avoid hot liquids for 24 hours
                4. No straws for 48 hours
                5. Eat soft foods for 2-3 days
                6. Take prescribed pain medication
                7. Rinse gently with salt water after 24 hours
                Contact us if you experience excessive bleeding or severe pain.
                """,
                category="aftercare",
                expected_queries=[
                    "Post extraction care",
                    "What to do after tooth extraction?",
                    "Aftercare instructions",
                    "Can I use a straw after extraction?"
                ],
                metadata={"type": "medical", "language": "en"}
            ),
            TestDocument(
                content="""
                Información en Español:
                Clínica Dental Dr. Shtern
                Servicios disponibles:
                - Limpieza dental profesional
                - Exámenes y radiografías
                - Extracciones dentales
                - Tratamiento de conducto
                - Coronas dentales
                - Blanqueamiento dental
                Aceptamos la mayoría de seguros dentales.
                Citas de emergencia disponibles.
                """,
                category="services",
                expected_queries=[
                    "¿Qué servicios ofrecen?",
                    "Información en español",
                    "¿Aceptan seguro dental?",
                    "Servicios dentales"
                ],
                metadata={"type": "services", "language": "es"}
            )
        ]
    
    async def setup_test_data(self) -> bool:
        """Ingest test documents into Pinecone"""
        logger.info("Setting up test data...")
        
        try:
            # Clear existing data for clean test
            await self._clear_index()
            
            # Ingest each test document
            for i, doc in enumerate(self.test_documents):
                logger.info(f"Ingesting document {i+1}/{len(self.test_documents)}: {doc.category}")
                
                result = await self.ingestion.ingest_document(
                    content=doc.content,
                    category=doc.category,
                    metadata=doc.metadata
                )
                
                if result.get('status') not in ['indexed', 'already_indexed']:
                    logger.error(f"Failed to ingest document: {result}")
                    return False
                
                logger.info(f"Document indexed: {result}")
                
                # Small delay to ensure indexing
                await asyncio.sleep(1)
            
            logger.info("Test data setup complete")
            return True
            
        except Exception as e:
            logger.error(f"Setup failed: {e}")
            return False
    
    async def _clear_index(self):
        """Clear the Pinecone index"""
        try:
            if self.kb.index:
                # Delete all vectors (be careful in production!)
                self.kb.index.delete(delete_all=True, namespace="")
                logger.info("Index cleared")
        except Exception as e:
            logger.warning(f"Could not clear index: {e}")
    
    async def evaluate_retrieval(self) -> Dict[str, Any]:
        """Run comprehensive retrieval evaluation"""
        logger.info("Starting retrieval evaluation...")
        
        total_queries = 0
        successful_retrievals = 0
        
        for doc in self.test_documents:
            for query in doc.expected_queries:
                total_queries += 1
                result = await self._evaluate_single_query(query, doc)
                self.results.append(result)
                
                if result.success:
                    successful_retrievals += 1
                
                # Log result
                logger.info(f"Query: {query[:50]}... | Success: {result.success} | "
                          f"Latency: {result.latency_ms:.2f}ms")
        
        # Calculate metrics
        metrics = self._calculate_metrics()
        
        return metrics
    
    async def _evaluate_single_query(
        self, 
        query: str, 
        expected_doc: TestDocument
    ) -> EvaluationResult:
        """Evaluate a single query"""
        
        start_time = time.time()
        
        try:
            # Perform search
            retrieved = await self.kb.search(query)
            
            latency_ms = (time.time() - start_time) * 1000
            
            # Check if expected content is retrieved
            success = False
            relevance_scores = []
            
            for i, result in enumerate(retrieved):
                # Calculate relevance (simple text overlap for now)
                relevance = self._calculate_relevance(
                    result, 
                    expected_doc.content
                )
                relevance_scores.append(relevance)
                
                if relevance > 0.5:  # Threshold for success
                    success = True
            
            # Calculate hit@k metrics
            hit_at_k = {
                1: len(retrieved) > 0 and relevance_scores[0] > 0.5 if relevance_scores else False,
                3: any(score > 0.5 for score in relevance_scores[:3]),
                5: any(score > 0.5 for score in relevance_scores[:5]),
                10: any(score > 0.5 for score in relevance_scores[:10])
            }
            
            return EvaluationResult(
                query=query,
                expected_doc=expected_doc.content[:100],
                retrieved_docs=retrieved,
                relevance_scores=relevance_scores,
                hit_at_k=hit_at_k,
                latency_ms=latency_ms,
                success=success
            )
            
        except Exception as e:
            return EvaluationResult(
                query=query,
                expected_doc=expected_doc.content[:100],
                retrieved_docs=[],
                relevance_scores=[],
                hit_at_k={1: False, 3: False, 5: False, 10: False},
                latency_ms=(time.time() - start_time) * 1000,
                success=False,
                error=str(e)
            )
    
    def _calculate_relevance(self, retrieved: str, expected: str) -> float:
        """Calculate relevance score between retrieved and expected content"""
        # Simple word overlap for now (can be improved with embeddings)
        retrieved_words = set(retrieved.lower().split())
        expected_words = set(expected.lower().split())
        
        if not expected_words:
            return 0.0
        
        overlap = len(retrieved_words & expected_words)
        return overlap / len(expected_words)
    
    def _calculate_metrics(self) -> Dict[str, Any]:
        """Calculate evaluation metrics"""
        
        if not self.results:
            return {"error": "No results to evaluate"}
        
        # Basic metrics
        total = len(self.results)
        successful = sum(1 for r in self.results if r.success)
        
        # Hit@k metrics
        hit_at_1 = sum(1 for r in self.results if r.hit_at_k.get(1, False)) / total
        hit_at_3 = sum(1 for r in self.results if r.hit_at_k.get(3, False)) / total
        hit_at_5 = sum(1 for r in self.results if r.hit_at_k.get(5, False)) / total
        
        # Latency metrics
        latencies = [r.latency_ms for r in self.results]
        
        # Error analysis
        errors = [r.error for r in self.results if r.error]
        
        return {
            "total_queries": total,
            "successful_retrievals": successful,
            "success_rate": successful / total,
            "hit_at_1": hit_at_1,
            "hit_at_3": hit_at_3,
            "hit_at_5": hit_at_5,
            "avg_latency_ms": np.mean(latencies),
            "p50_latency_ms": np.percentile(latencies, 50),
            "p95_latency_ms": np.percentile(latencies, 95),
            "p99_latency_ms": np.percentile(latencies, 99),
            "total_errors": len(errors),
            "error_types": list(set(errors))[:5]  # Top 5 error types
        }
    
    async def debug_retrieval_issues(self):
        """Debug mode to identify specific issues"""
        logger.info("Running debug diagnostics...")
        
        diagnostics = {
            "index_status": await self._check_index_status(),
            "embedding_test": await self._test_embedding_generation(),
            "metadata_structure": await self._check_metadata_structure(),
            "filter_test": await self._test_filtering(),
            "similarity_threshold": await self._test_similarity_thresholds()
        }
        
        return diagnostics
    
    async def _check_index_status(self) -> Dict:
        """Check Pinecone index status"""
        try:
            stats = self.kb.index.describe_index_stats()
            return {
                "status": "connected",
                "total_vectors": stats.total_vector_count,
                "dimensions": stats.dimension,
                "index_fullness": stats.index_fullness
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    async def _test_embedding_generation(self) -> Dict:
        """Test embedding generation"""
        try:
            test_text = "test query for embedding"
            response = self.openai.embeddings.create(
                model="text-embedding-3-small",
                input=test_text
            )
            embedding = response.data[0].embedding
            
            return {
                "status": "success",
                "dimensions": len(embedding),
                "model": "text-embedding-3-small"
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    async def _check_metadata_structure(self) -> Dict:
        """Check metadata structure in index"""
        try:
            # Fetch a sample vector
            sample = self.kb.index.query(
                vector=[0.0] * 1536,  # Dummy vector
                top_k=1,
                include_metadata=True
            )
            
            if sample.matches:
                metadata = sample.matches[0].metadata
                return {
                    "status": "success",
                    "sample_metadata": metadata,
                    "fields": list(metadata.keys())
                }
            else:
                return {"status": "empty", "message": "No vectors in index"}
                
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    async def _test_filtering(self) -> Dict:
        """Test metadata filtering"""
        results = {}
        
        # Test different filter formats
        filters = [
            {"clinic_id": self.clinic_id},
            {"clinic_id": {"$eq": self.clinic_id}},
            {}  # No filter
        ]
        
        for i, filter_dict in enumerate(filters):
            try:
                query_result = self.kb.index.query(
                    vector=[0.0] * 1536,
                    filter=filter_dict if filter_dict else None,
                    top_k=5
                )
                results[f"filter_{i}"] = {
                    "filter": filter_dict,
                    "matches": len(query_result.matches),
                    "success": True
                }
            except Exception as e:
                results[f"filter_{i}"] = {
                    "filter": filter_dict,
                    "error": str(e),
                    "success": False
                }
        
        return results
    
    async def _test_similarity_thresholds(self) -> Dict:
        """Test different similarity thresholds"""
        test_query = "What insurance do you accept?"
        
        thresholds = [0.3, 0.5, 0.7, 0.9]
        results = {}
        
        for threshold in thresholds:
            # Temporarily modify threshold
            original_threshold = 0.5  # Default from code analysis
            
            # Search with different threshold
            retrieved = await self.kb.search(test_query)
            
            # Filter by threshold
            filtered = [
                r for r in retrieved 
                if self._calculate_relevance(r, "insurance") > threshold
            ]
            
            results[f"threshold_{threshold}"] = {
                "retrieved_count": len(retrieved),
                "filtered_count": len(filtered),
                "sample": filtered[0][:100] if filtered else None
            }
        
        return results
    
    def generate_report(self, metrics: Dict, diagnostics: Dict) -> str:
        """Generate evaluation report"""
        
        report = f"""
RAG EVALUATION REPORT
=====================
Generated: {datetime.now().isoformat()}
Clinic ID: {self.clinic_id}

PERFORMANCE METRICS
-------------------
Total Queries: {metrics.get('total_queries', 0)}
Success Rate: {metrics.get('success_rate', 0):.2%}
Hit@1: {metrics.get('hit_at_1', 0):.2%}
Hit@3: {metrics.get('hit_at_3', 0):.2%}
Hit@5: {metrics.get('hit_at_5', 0):.2%}

LATENCY METRICS
---------------
Average: {metrics.get('avg_latency_ms', 0):.2f}ms
P50: {metrics.get('p50_latency_ms', 0):.2f}ms
P95: {metrics.get('p95_latency_ms', 0):.2f}ms
P99: {metrics.get('p99_latency_ms', 0):.2f}ms

ERROR ANALYSIS
--------------
Total Errors: {metrics.get('total_errors', 0)}
Error Types: {', '.join(metrics.get('error_types', []))}

DIAGNOSTICS
-----------
Index Status: {json.dumps(diagnostics.get('index_status', {}), indent=2)}
Embedding Test: {diagnostics.get('embedding_test', {}).get('status')}
Metadata Fields: {', '.join(diagnostics.get('metadata_structure', {}).get('fields', []))}

RECOMMENDATIONS
---------------
"""
        
        # Add recommendations based on results
        if metrics.get('success_rate', 0) < 0.5:
            report += "⚠️ Low success rate detected. Check:\n"
            report += "  - Similarity threshold (currently 0.5)\n"
            report += "  - Metadata filtering logic\n"
            report += "  - Index naming convention\n"
        
        if metrics.get('avg_latency_ms', 0) > 500:
            report += "⚠️ High latency detected. Consider:\n"
            report += "  - Implementing caching\n"
            report += "  - Connection pooling\n"
            report += "  - Reducing embedding dimensions\n"
        
        if diagnostics.get('index_status', {}).get('total_vectors', 0) == 0:
            report += "⚠️ Empty index detected. Ensure:\n"
            report += "  - Documents are being ingested\n"
            report += "  - Index name is correct\n"
            report += "  - No deletion errors\n"
        
        return report


async def main():
    """Main evaluation runner"""
    
    print("🚀 RAG Evaluation System")
    print("=" * 50)
    
    # Initialize evaluator
    evaluator = RAGEvaluator()
    
    # Setup test data
    print("\n📝 Setting up test data...")
    setup_success = await evaluator.setup_test_data()
    
    if not setup_success:
        print("❌ Failed to setup test data")
        return
    
    # Wait for indexing
    print("⏳ Waiting for indexing (5 seconds)...")
    await asyncio.sleep(5)
    
    # Run evaluation
    print("\n🔍 Running retrieval evaluation...")
    metrics = await evaluator.evaluate_retrieval()
    
    # Run diagnostics
    print("\n🔧 Running diagnostics...")
    diagnostics = await evaluator.debug_retrieval_issues()
    
    # Generate report
    report = evaluator.generate_report(metrics, diagnostics)
    
    # Print report
    print(report)
    
    # Save report
    report_path = f"rag_evaluation_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(report_path, 'w') as f:
        f.write(report)
    
    print(f"\n📊 Report saved to: {report_path}")
    
    # Print quick summary
    print("\n✅ Evaluation Complete!")
    print(f"Success Rate: {metrics.get('success_rate', 0):.2%}")
    print(f"Average Latency: {metrics.get('avg_latency_ms', 0):.2f}ms")


if __name__ == "__main__":
    asyncio.run(main())