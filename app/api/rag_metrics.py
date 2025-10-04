#!/usr/bin/env python3
"""
RAG System Metrics and Monitoring

Tracks performance, quality, and usage metrics for the enhanced RAG system.
"""

import os
import time
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from collections import defaultdict
import asyncio
import redis
import numpy as np

logger = logging.getLogger(__name__)


class RAGMetrics:
    """Collects and reports RAG system metrics"""
    
    def __init__(self, clinic_id: str, redis_url: Optional[str] = None):
        self.clinic_id = clinic_id
        redis_url = redis_url or os.environ.get('REDIS_URL', 'redis://localhost:6379')
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        
        # Metric buckets
        self.metrics = {
            'search_latency': [],
            'cache_hit_rate': [],
            'result_counts': [],
            'entity_extraction_accuracy': [],
            'source_distribution': defaultdict(int),
            'query_types': defaultdict(int),
            'errors': []
        }
        
        # Time windows for aggregation
        self.window_1min = 60
        self.window_5min = 300
        self.window_1hour = 3600
    
    async def record_search(
        self,
        query: str,
        latency_ms: float,
        result_count: int,
        sources: List[str],
        cache_hit: bool = False,
        entities_extracted: Optional[Dict] = None
    ):
        """Record metrics for a search operation"""
        
        timestamp = datetime.utcnow().isoformat()
        
        # Record to in-memory metrics
        self.metrics['search_latency'].append(latency_ms)
        self.metrics['result_counts'].append(result_count)
        
        # Track source distribution
        for source in sources:
            self.metrics['source_distribution'][source] += 1
        
        # Track query type based on entities
        if entities_extracted:
            if entities_extracted.get('doctor_name'):
                self.metrics['query_types']['doctor_search'] += 1
            elif entities_extracted.get('service_category'):
                self.metrics['query_types']['service_search'] += 1
            elif entities_extracted.get('availability_query'):
                self.metrics['query_types']['availability'] += 1
            else:
                self.metrics['query_types']['general'] += 1
        
        # Store in Redis for persistence
        metric_data = {
            'timestamp': timestamp,
            'clinic_id': self.clinic_id,
            'query': query[:100],  # Truncate long queries
            'latency_ms': latency_ms,
            'result_count': result_count,
            'sources': sources,
            'cache_hit': cache_hit,
            'entities': list(entities_extracted.keys()) if entities_extracted else []
        }
        
        # Store with expiration
        key = f"metrics:{self.clinic_id}:{timestamp}"
        self.redis_client.setex(
            key,
            self.window_1hour,
            json.dumps(metric_data)
        )
        
        # Update running averages
        await self._update_aggregates()
    
    async def record_error(
        self,
        error_type: str,
        error_message: str,
        query: Optional[str] = None
    ):
        """Record an error occurrence"""
        
        error_data = {
            'timestamp': datetime.utcnow().isoformat(),
            'type': error_type,
            'message': error_message[:500],
            'query': query[:100] if query else None
        }
        
        self.metrics['errors'].append(error_data)
        
        # Store in Redis
        key = f"errors:{self.clinic_id}:{datetime.utcnow().timestamp()}"
        self.redis_client.setex(
            key,
            86400,  # Keep errors for 24 hours
            json.dumps(error_data)
        )
    
    async def get_current_metrics(self) -> Dict[str, Any]:
        """Get current metrics snapshot"""
        
        # Calculate aggregates
        latencies = self.metrics['search_latency'][-100:]  # Last 100 searches
        result_counts = self.metrics['result_counts'][-100:]
        
        metrics = {
            'timestamp': datetime.utcnow().isoformat(),
            'clinic_id': self.clinic_id,
            'latency': {
                'mean_ms': np.mean(latencies) if latencies else 0,
                'median_ms': np.median(latencies) if latencies else 0,
                'p95_ms': np.percentile(latencies, 95) if latencies else 0,
                'p99_ms': np.percentile(latencies, 99) if latencies else 0
            },
            'results': {
                'mean_count': np.mean(result_counts) if result_counts else 0,
                'median_count': np.median(result_counts) if result_counts else 0,
                'zero_results_rate': sum(1 for c in result_counts if c == 0) / len(result_counts) if result_counts else 0
            },
            'sources': dict(self.metrics['source_distribution']),
            'query_types': dict(self.metrics['query_types']),
            'error_count': len(self.metrics['errors']),
            'recent_errors': self.metrics['errors'][-5:]  # Last 5 errors
        }
        
        # Get cache metrics from RAGCache
        cache_key = f"cache_metrics:{self.clinic_id}"
        cache_metrics = self.redis_client.get(cache_key)
        if cache_metrics:
            metrics['cache'] = json.loads(cache_metrics)
        
        return metrics
    
    async def _update_aggregates(self):
        """Update aggregate metrics"""
        
        # Clean old data from memory
        if len(self.metrics['search_latency']) > 1000:
            self.metrics['search_latency'] = self.metrics['search_latency'][-500:]
        if len(self.metrics['result_counts']) > 1000:
            self.metrics['result_counts'] = self.metrics['result_counts'][-500:]
        if len(self.metrics['errors']) > 100:
            self.metrics['errors'] = self.metrics['errors'][-50:]
    
    def get_performance_summary(self) -> Dict[str, Any]:
        """Get performance summary for reporting"""
        
        latencies = self.metrics['search_latency']
        
        if not latencies:
            return {'message': 'No data available yet'}
        
        total_queries = len(latencies)
        
        return {
            'total_queries': total_queries,
            'avg_latency_ms': np.mean(latencies),
            'latency_improvement': self._calculate_improvement(),
            'most_common_source': max(self.metrics['source_distribution'].items(), key=lambda x: x[1])[0] if self.metrics['source_distribution'] else None,
            'query_distribution': dict(self.metrics['query_types']),
            'health_status': self._calculate_health_status()
        }
    
    def _calculate_improvement(self) -> float:
        """Calculate latency improvement over time"""
        
        latencies = self.metrics['search_latency']
        if len(latencies) < 20:
            return 0.0
        
        # Compare first 10 vs last 10
        first_10 = np.mean(latencies[:10])
        last_10 = np.mean(latencies[-10:])
        
        if first_10 == 0:
            return 0.0
        
        improvement = (first_10 - last_10) / first_10 * 100
        return round(improvement, 2)
    
    def _calculate_health_status(self) -> str:
        """Calculate overall system health"""
        
        if not self.metrics['search_latency']:
            return 'unknown'
        
        recent_latency = np.mean(self.metrics['search_latency'][-10:]) if len(self.metrics['search_latency']) >= 10 else np.mean(self.metrics['search_latency'])
        recent_errors = len([e for e in self.metrics['errors'] if 
                           (datetime.utcnow() - datetime.fromisoformat(e['timestamp'])).seconds < 300])
        
        if recent_latency > 1000 or recent_errors > 5:
            return 'degraded'
        elif recent_latency > 500 or recent_errors > 2:
            return 'warning'
        else:
            return 'healthy'


class PerformanceMonitor:
    """Context manager for performance monitoring"""
    
    def __init__(self, metrics: RAGMetrics, operation: str):
        self.metrics = metrics
        self.operation = operation
        self.start_time = None
    
    def __enter__(self):
        self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            elapsed_ms = (time.time() - self.start_time) * 1000
            logger.debug(f"{self.operation} completed in {elapsed_ms:.2f}ms")
        else:
            asyncio.create_task(
                self.metrics.record_error(
                    error_type=exc_type.__name__,
                    error_message=str(exc_val),
                    query=self.operation
                )
            )


class SearchQualityAnalyzer:
    """Analyzes search result quality"""
    
    @staticmethod
    def calculate_diversity_score(results: List[Dict]) -> float:
        """Calculate diversity of search results"""
        
        if not results:
            return 0.0
        
        # Check source diversity
        sources = [r.get('source', 'unknown') for r in results]
        unique_sources = len(set(sources))
        source_diversity = unique_sources / len(sources)
        
        # Check type diversity
        types = [r.get('type', 'unknown') for r in results]
        unique_types = len(set(types))
        type_diversity = unique_types / len(types)
        
        # Combined diversity score
        diversity = (source_diversity + type_diversity) / 2
        return round(diversity, 3)
    
    @staticmethod
    def calculate_relevance_distribution(results: List[Dict]) -> Dict[str, float]:
        """Analyze score distribution of results"""
        
        if not results:
            return {}
        
        scores = [r.get('final_score', r.get('retrieval_score', 0)) for r in results]
        
        return {
            'max_score': max(scores),
            'min_score': min(scores),
            'mean_score': np.mean(scores),
            'std_dev': np.std(scores),
            'score_range': max(scores) - min(scores)
        }
    
    @staticmethod
    def identify_quality_issues(results: List[Dict]) -> List[str]:
        """Identify potential quality issues"""
        
        issues = []
        
        if not results:
            issues.append("No results returned")
            return issues
        
        # Check for low diversity
        diversity = SearchQualityAnalyzer.calculate_diversity_score(results)
        if diversity < 0.3:
            issues.append(f"Low result diversity: {diversity}")
        
        # Check for uniform scores (might indicate ranking issues)
        scores = [r.get('final_score', r.get('retrieval_score', 0)) for r in results]
        if len(set(scores)) == 1:
            issues.append("All results have identical scores")
        
        # Check for too many low-score results
        low_score_count = sum(1 for s in scores if s < 0.5)
        if low_score_count > len(scores) / 2:
            issues.append(f"Majority of results have low scores (<0.5)")
        
        return issues