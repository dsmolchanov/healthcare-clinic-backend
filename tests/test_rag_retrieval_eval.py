#!/usr/bin/env python3
"""
RAG Retrieval Evaluation Framework

Comprehensive evaluation suite for testing the enhanced RAG system's retrieval
quality, including precision, recall, MRR, and NDCG metrics.
"""

import os
import sys
import json
import asyncio
import logging
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass, asdict
import numpy as np
from collections import defaultdict

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.hybrid_search_engine import HybridSearchEngine
from app.api.structured_data_embedder import StructuredDataEmbedder
from app.database import create_supabase_client

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class EvalQuery:
    """Evaluation query with ground truth"""
    query_id: str
    query: str
    category: str  # 'doctor', 'service', 'availability', 'general'
    relevant_docs: List[str]  # IDs of relevant documents
    expected_entities: Dict[str, Any]  # Expected extracted entities
    notes: Optional[str] = None


@dataclass
class EvalResult:
    """Result for a single query evaluation"""
    query_id: str
    query: str
    category: str
    retrieved_docs: List[str]
    relevant_docs: List[str]
    precision_at_k: Dict[int, float]
    recall_at_k: Dict[int, float]
    mrr: float
    ndcg: float
    entity_extraction_accuracy: float
    latency_ms: float
    used_cache: bool


@dataclass
class EvalReport:
    """Overall evaluation report"""
    timestamp: str
    total_queries: int
    results_by_category: Dict[str, Dict[str, float]]
    overall_metrics: Dict[str, float]
    cache_performance: Dict[str, float]
    latency_stats: Dict[str, float]
    failed_queries: List[str]
    recommendations: List[str]


class RAGRetrievalEvaluator:
    """Evaluates RAG retrieval performance"""
    
    def __init__(self, clinic_id: str, use_cache: bool = True):
        self.clinic_id = clinic_id
        self.use_cache = use_cache
        self.search_engine = HybridSearchEngine(clinic_id)
        self.supabase = create_supabase_client()
        self.results: List[EvalResult] = []
        
    def create_test_dataset(self) -> List[EvalQuery]:
        """Create comprehensive test dataset with ground truth"""
        
        test_queries = []
        
        # Doctor-related queries
        test_queries.extend([
            EvalQuery(
                query_id="doc_001",
                query="I need to see a cardiologist",
                category="doctor",
                relevant_docs=["doctor_1", "doctor_2"],  # IDs of cardiologists
                expected_entities={
                    "specialization": "cardiology",
                    "intent": "appointment"
                }
            ),
            EvalQuery(
                query_id="doc_002",
                query="Is Dr. Smith available tomorrow morning?",
                category="doctor",
                relevant_docs=["doctor_smith"],
                expected_entities={
                    "doctor_name": "Smith",
                    "time_preference": "morning",
                    "date": "tomorrow",
                    "availability_query": True
                }
            ),
            EvalQuery(
                query_id="doc_003",
                query="Which doctors speak Spanish?",
                category="doctor",
                relevant_docs=["doctor_3", "doctor_5"],  # Spanish-speaking doctors
                expected_entities={
                    "language": "spanish"
                }
            ),
            EvalQuery(
                query_id="doc_004",
                query="I need a female pediatrician for my child",
                category="doctor",
                relevant_docs=["doctor_7"],
                expected_entities={
                    "specialization": "pediatrics",
                    "doctor_gender_preference": "female"
                }
            ),
        ])
        
        # Service-related queries
        test_queries.extend([
            EvalQuery(
                query_id="svc_001",
                query="How much does an MRI cost?",
                category="service",
                relevant_docs=["service_mri"],
                expected_entities={
                    "service_category": "mri",
                    "cost_query": True
                }
            ),
            EvalQuery(
                query_id="svc_002",
                query="Do you offer blood tests?",
                category="service",
                relevant_docs=["service_blood_test", "service_lab"],
                expected_entities={
                    "service_category": "blood test"
                }
            ),
            EvalQuery(
                query_id="svc_003",
                query="What vaccinations are available for children?",
                category="service",
                relevant_docs=["service_vaccination_child"],
                expected_entities={
                    "service_category": "vaccination"
                }
            ),
            EvalQuery(
                query_id="svc_004",
                query="Is dental cleaning covered by insurance?",
                category="service",
                relevant_docs=["service_dental_cleaning"],
                expected_entities={
                    "service_category": "dental",
                    "insurance_query": True
                }
            ),
        ])
        
        # Availability queries
        test_queries.extend([
            EvalQuery(
                query_id="avl_001",
                query="What appointments are available this week?",
                category="availability",
                relevant_docs=["availability_week"],
                expected_entities={
                    "availability_query": True,
                    "time_preference": "soon"
                }
            ),
            EvalQuery(
                query_id="avl_002",
                query="Do you have weekend appointments?",
                category="availability",
                relevant_docs=["availability_weekend"],
                expected_entities={
                    "time_preference": "weekend",
                    "availability_query": True
                }
            ),
        ])
        
        # General/Complex queries
        test_queries.extend([
            EvalQuery(
                query_id="gen_001",
                query="I have chest pain and need to see someone urgently",
                category="general",
                relevant_docs=["doctor_emergency", "service_urgent_care"],
                expected_entities={
                    "symptoms": ["pain"],
                    "body_parts": ["chest"],
                    "urgency": "high",
                    "time_preference": "urgent"
                }
            ),
            EvalQuery(
                query_id="gen_002",
                query="My child has a fever and rash, which doctor should I see?",
                category="general",
                relevant_docs=["doctor_pediatrician", "service_pediatric_consultation"],
                expected_entities={
                    "symptoms": ["fever", "rash"],
                    "specialization": "pediatrics"
                }
            ),
            EvalQuery(
                query_id="gen_003",
                query="What are your clinic hours and location?",
                category="general",
                relevant_docs=["clinic_info"],
                expected_entities={
                    "location_query": True
                }
            ),
        ])
        
        return test_queries
    
    async def evaluate(self, test_queries: Optional[List[EvalQuery]] = None) -> EvalReport:
        """Run evaluation on test queries"""
        
        if test_queries is None:
            test_queries = self.create_test_dataset()
        
        logger.info(f"Starting evaluation with {len(test_queries)} queries")
        
        # Clear results
        self.results = []
        
        # Run evaluation for each query
        for query in test_queries:
            try:
                result = await self._evaluate_single_query(query)
                self.results.append(result)
            except Exception as e:
                logger.error(f"Failed to evaluate query {query.query_id}: {e}")
        
        # Generate report
        report = self._generate_report()
        
        return report
    
    async def _evaluate_single_query(self, eval_query: EvalQuery) -> EvalResult:
        """Evaluate a single query"""
        
        import time
        
        # Measure retrieval time
        start_time = time.time()
        
        # Perform search
        search_results = await self.search_engine.hybrid_search(
            query=eval_query.query,
            top_k=10
        )
        
        end_time = time.time()
        latency_ms = (end_time - start_time) * 1000
        
        # Extract retrieved document IDs
        retrieved_docs = []
        for result in search_results:
            # Extract document ID from metadata or generate from content
            if result.get('metadata', {}).get('doctor_id'):
                retrieved_docs.append(f"doctor_{result['metadata']['doctor_id']}")
            elif result.get('metadata', {}).get('service_id'):
                retrieved_docs.append(f"service_{result['metadata']['service_id']}")
            else:
                # Generate ID from content hash for other documents
                import hashlib
                doc_id = hashlib.md5(result.get('text', '').encode()).hexdigest()[:8]
                retrieved_docs.append(doc_id)
        
        # Calculate metrics
        precision_at_k = self._calculate_precision_at_k(
            retrieved_docs, eval_query.relevant_docs
        )
        recall_at_k = self._calculate_recall_at_k(
            retrieved_docs, eval_query.relevant_docs
        )
        mrr = self._calculate_mrr(retrieved_docs, eval_query.relevant_docs)
        ndcg = self._calculate_ndcg(retrieved_docs, eval_query.relevant_docs)
        
        # Check entity extraction accuracy
        entities = search_results[0].get('extracted_entities', {}) if search_results else {}
        entity_accuracy = self._calculate_entity_accuracy(
            entities, eval_query.expected_entities
        )
        
        # Check if cache was used
        used_cache = any(
            result.get('source') == 'cache' for result in search_results
        ) if search_results else False
        
        return EvalResult(
            query_id=eval_query.query_id,
            query=eval_query.query,
            category=eval_query.category,
            retrieved_docs=retrieved_docs,
            relevant_docs=eval_query.relevant_docs,
            precision_at_k=precision_at_k,
            recall_at_k=recall_at_k,
            mrr=mrr,
            ndcg=ndcg,
            entity_extraction_accuracy=entity_accuracy,
            latency_ms=latency_ms,
            used_cache=used_cache
        )
    
    def _calculate_precision_at_k(
        self, 
        retrieved: List[str], 
        relevant: List[str]
    ) -> Dict[int, float]:
        """Calculate precision at different k values"""
        
        precision = {}
        for k in [1, 3, 5, 10]:
            if k > len(retrieved):
                k = len(retrieved)
            if k == 0:
                precision[k] = 0.0
            else:
                relevant_in_top_k = len(
                    set(retrieved[:k]).intersection(set(relevant))
                )
                precision[k] = relevant_in_top_k / k
        
        return precision
    
    def _calculate_recall_at_k(
        self, 
        retrieved: List[str], 
        relevant: List[str]
    ) -> Dict[int, float]:
        """Calculate recall at different k values"""
        
        recall = {}
        total_relevant = len(relevant)
        
        for k in [1, 3, 5, 10]:
            if k > len(retrieved):
                k = len(retrieved)
            if total_relevant == 0:
                recall[k] = 0.0
            else:
                relevant_in_top_k = len(
                    set(retrieved[:k]).intersection(set(relevant))
                )
                recall[k] = relevant_in_top_k / total_relevant
        
        return recall
    
    def _calculate_mrr(self, retrieved: List[str], relevant: List[str]) -> float:
        """Calculate Mean Reciprocal Rank"""
        
        for i, doc_id in enumerate(retrieved):
            if doc_id in relevant:
                return 1.0 / (i + 1)
        return 0.0
    
    def _calculate_ndcg(self, retrieved: List[str], relevant: List[str]) -> float:
        """Calculate Normalized Discounted Cumulative Gain"""
        
        def dcg(scores: List[float]) -> float:
            return sum(
                score / np.log2(i + 2) for i, score in enumerate(scores)
            )
        
        # Create relevance scores (1 if relevant, 0 otherwise)
        scores = [1.0 if doc in relevant else 0.0 for doc in retrieved]
        
        if not scores:
            return 0.0
        
        # Calculate DCG
        dcg_value = dcg(scores)
        
        # Calculate ideal DCG (all relevant docs at the top)
        ideal_scores = [1.0] * len(relevant) + [0.0] * (len(retrieved) - len(relevant))
        idcg_value = dcg(ideal_scores[:len(retrieved)])
        
        if idcg_value == 0:
            return 0.0
        
        return dcg_value / idcg_value
    
    def _calculate_entity_accuracy(
        self, 
        extracted: Dict[str, Any], 
        expected: Dict[str, Any]
    ) -> float:
        """Calculate entity extraction accuracy"""
        
        if not expected:
            return 1.0
        
        correct = 0
        total = len(expected)
        
        for key, expected_value in expected.items():
            if key in extracted:
                if isinstance(expected_value, list):
                    # For lists, check overlap
                    overlap = set(expected_value).intersection(set(extracted.get(key, [])))
                    if overlap:
                        correct += len(overlap) / len(expected_value)
                elif extracted[key] == expected_value:
                    correct += 1
                elif isinstance(expected_value, str) and isinstance(extracted[key], str):
                    # Fuzzy match for strings
                    if expected_value.lower() in extracted[key].lower():
                        correct += 0.5
        
        return correct / total if total > 0 else 0.0
    
    def _generate_report(self) -> EvalReport:
        """Generate evaluation report from results"""
        
        # Group results by category
        results_by_category = defaultdict(list)
        for result in self.results:
            results_by_category[result.category].append(result)
        
        # Calculate metrics by category
        category_metrics = {}
        for category, results in results_by_category.items():
            category_metrics[category] = self._calculate_aggregate_metrics(results)
        
        # Calculate overall metrics
        overall_metrics = self._calculate_aggregate_metrics(self.results)
        
        # Calculate cache performance
        cache_results = [r for r in self.results if r.used_cache]
        no_cache_results = [r for r in self.results if not r.used_cache]
        
        cache_performance = {
            "cache_hit_rate": len(cache_results) / len(self.results) if self.results else 0,
            "avg_latency_with_cache_ms": np.mean([r.latency_ms for r in cache_results]) if cache_results else 0,
            "avg_latency_without_cache_ms": np.mean([r.latency_ms for r in no_cache_results]) if no_cache_results else 0,
        }
        
        # Calculate latency statistics
        all_latencies = [r.latency_ms for r in self.results]
        latency_stats = {
            "mean_ms": np.mean(all_latencies) if all_latencies else 0,
            "median_ms": np.median(all_latencies) if all_latencies else 0,
            "p95_ms": np.percentile(all_latencies, 95) if all_latencies else 0,
            "p99_ms": np.percentile(all_latencies, 99) if all_latencies else 0,
        }
        
        # Identify failed queries (MRR = 0)
        failed_queries = [r.query_id for r in self.results if r.mrr == 0]
        
        # Generate recommendations
        recommendations = self._generate_recommendations(
            overall_metrics, category_metrics, cache_performance
        )
        
        return EvalReport(
            timestamp=datetime.utcnow().isoformat(),
            total_queries=len(self.results),
            results_by_category=category_metrics,
            overall_metrics=overall_metrics,
            cache_performance=cache_performance,
            latency_stats=latency_stats,
            failed_queries=failed_queries,
            recommendations=recommendations
        )
    
    def _calculate_aggregate_metrics(self, results: List[EvalResult]) -> Dict[str, float]:
        """Calculate aggregate metrics for a set of results"""
        
        if not results:
            return {}
        
        metrics = {
            "precision@1": np.mean([r.precision_at_k[1] for r in results]),
            "precision@3": np.mean([r.precision_at_k[3] for r in results]),
            "precision@5": np.mean([r.precision_at_k[5] for r in results]),
            "recall@1": np.mean([r.recall_at_k[1] for r in results]),
            "recall@3": np.mean([r.recall_at_k[3] for r in results]),
            "recall@5": np.mean([r.recall_at_k[5] for r in results]),
            "mrr": np.mean([r.mrr for r in results]),
            "ndcg": np.mean([r.ndcg for r in results]),
            "entity_accuracy": np.mean([r.entity_extraction_accuracy for r in results]),
            "avg_latency_ms": np.mean([r.latency_ms for r in results]),
        }
        
        return metrics
    
    def _generate_recommendations(self, 
        overall_metrics: Dict[str, float],
        category_metrics: Dict[str, Dict[str, float]],
        cache_performance: Dict[str, float]
    ) -> List[str]:
        """Generate recommendations based on evaluation results"""
        
        recommendations = []
        
        # Check overall performance
        if overall_metrics.get("precision@1", 0) < 0.7:
            recommendations.append(
                "Low precision@1 (< 70%). Consider improving ranking algorithms or "
                "adding more relevant training data."
            )
        
        if overall_metrics.get("mrr", 0) < 0.6:
            recommendations.append(
                "Low MRR (< 60%). The most relevant documents are not appearing "
                "at the top. Review the reranking logic."
            )
        
        if overall_metrics.get("entity_accuracy", 0) < 0.8:
            recommendations.append(
                "Entity extraction accuracy is below 80%. Consider improving "
                "the NER model or adding more training examples."
            )
        
        # Check category-specific performance
        for category, metrics in category_metrics.items():
            if metrics.get("precision@3", 0) < 0.5:
                recommendations.append(
                    f"Low precision for {category} queries. Consider adding "
                    f"category-specific optimizations."
                )
        
        # Check cache performance
        if cache_performance.get("cache_hit_rate", 0) < 0.3:
            recommendations.append(
                "Low cache hit rate (< 30%). Consider implementing query "
                "normalization or semantic caching."
            )
        
        cache_speedup = (
            cache_performance.get("avg_latency_without_cache_ms", 1) / 
            max(cache_performance.get("avg_latency_with_cache_ms", 1), 0.1)
        )
        if cache_speedup < 2:
            recommendations.append(
                "Cache is not providing significant speedup. Review cache "
                "implementation and storage backend."
            )
        
        # Check latency
        if overall_metrics.get("avg_latency_ms", 0) > 500:
            recommendations.append(
                "Average latency exceeds 500ms. Consider optimizing search "
                "queries or adding more caching."
            )
        
        if not recommendations:
            recommendations.append("System is performing well within expected parameters.")
        
        return recommendations
    
    def print_report(self, report: EvalReport):
        """Print formatted evaluation report"""
        
        print("\n" + "="*80)
        print("RAG RETRIEVAL EVALUATION REPORT")
        print("="*80)
        print(f"Timestamp: {report.timestamp}")
        print(f"Total Queries: {report.total_queries}")
        
        print("\n" + "-"*40)
        print("OVERALL METRICS")
        print("-"*40)
        for metric, value in report.overall_metrics.items():
            print(f"{metric:20s}: {value:.3f}")
        
        print("\n" + "-"*40)
        print("METRICS BY CATEGORY")
        print("-"*40)
        for category, metrics in report.results_by_category.items():
            print(f"\n{category.upper()}:")
            for metric, value in metrics.items():
                print(f"  {metric:20s}: {value:.3f}")
        
        print("\n" + "-"*40)
        print("CACHE PERFORMANCE")
        print("-"*40)
        for metric, value in report.cache_performance.items():
            if "rate" in metric:
                print(f"{metric:30s}: {value:.1%}")
            else:
                print(f"{metric:30s}: {value:.1f} ms")
        
        print("\n" + "-"*40)
        print("LATENCY STATISTICS")
        print("-"*40)
        for metric, value in report.latency_stats.items():
            print(f"{metric:20s}: {value:.1f} ms")
        
        if report.failed_queries:
            print("\n" + "-"*40)
            print("FAILED QUERIES")
            print("-"*40)
            for query_id in report.failed_queries:
                print(f"  - {query_id}")
        
        print("\n" + "-"*40)
        print("RECOMMENDATIONS")
        print("-"*40)
        for i, rec in enumerate(report.recommendations, 1):
            print(f"{i}. {rec}")
        
        print("\n" + "="*80)
    
    def save_report(self, report: EvalReport, filename: Optional[str] = None):
        """Save evaluation report to file"""
        
        if filename is None:
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            filename = f"rag_eval_report_{timestamp}.json"
        
        with open(filename, 'w') as f:
            json.dump(asdict(report), f, indent=2, default=str)
        
        logger.info(f"Report saved to {filename}")


async def main():
    """Main evaluation function"""
    
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Evaluate RAG retrieval performance'
    )
    parser.add_argument(
        '--clinic-id',
        default='test_clinic',
        help='Clinic ID to evaluate'
    )
    parser.add_argument(
        '--no-cache',
        action='store_true',
        help='Disable cache for evaluation'
    )
    parser.add_argument(
        '--save-report',
        help='Save report to file'
    )
    parser.add_argument(
        '--custom-queries',
        help='Path to JSON file with custom test queries'
    )
    
    args = parser.parse_args()
    
    # Initialize evaluator
    evaluator = RAGRetrievalEvaluator(
        clinic_id=args.clinic_id,
        use_cache=not args.no_cache
    )
    
    # Load custom queries if provided
    test_queries = None
    if args.custom_queries:
        with open(args.custom_queries, 'r') as f:
            query_data = json.load(f)
            test_queries = [
                EvalQuery(**q) for q in query_data
            ]
    
    # Run evaluation
    print("Starting RAG retrieval evaluation...")
    report = await evaluator.evaluate(test_queries)
    
    # Print report
    evaluator.print_report(report)
    
    # Save report if requested
    if args.save_report:
        evaluator.save_report(report, args.save_report)


if __name__ == "__main__":
    asyncio.run(main())