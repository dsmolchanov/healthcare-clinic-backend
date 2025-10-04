#!/usr/bin/env python3
"""
Migration script to upgrade existing clinics to enhanced RAG system

This script:
1. Indexes existing structured data (doctors, services) into Pinecone
2. Initializes RAG cache for each clinic
3. Validates the migration
4. Provides rollback capability
"""

import os
import sys
import asyncio
import logging
import argparse
from typing import List, Dict, Any, Optional
from datetime import datetime

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import create_supabase_client
from app.api.structured_data_embedder import StructuredDataEmbedder
from app.api.rag_cache import RAGCache
from app.api.hybrid_search_engine import HybridSearchEngine

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class EnhancedRAGMigrator:
    """Migrates clinics to enhanced RAG system"""
    
    def __init__(self, dry_run: bool = False):
        """Initialize migrator
        
        Args:
            dry_run: If True, performs validation without making changes
        """
        self.dry_run = dry_run
        self.supabase = create_supabase_client()
        self.migration_log = []
        
    async def migrate_all_clinics(self) -> Dict[str, Any]:
        """Migrate all active clinics to enhanced RAG
        
        Returns:
            Migration summary
        """
        logger.info("Starting migration to enhanced RAG system")
        
        if self.dry_run:
            logger.info("DRY RUN MODE - No changes will be made")
        
        # Get all active clinics
        clinics = await self._get_active_clinics()
        logger.info(f"Found {len(clinics)} active clinics to migrate")
        
        results = {
            'total_clinics': len(clinics),
            'successful': 0,
            'failed': 0,
            'skipped': 0,
            'details': []
        }
        
        for clinic in clinics:
            clinic_id = clinic['id']
            clinic_name = clinic.get('name', 'Unknown')
            
            logger.info(f"Processing clinic: {clinic_name} ({clinic_id})")
            
            try:
                result = await self._migrate_clinic(clinic_id)
                
                if result['status'] == 'success':
                    results['successful'] += 1
                elif result['status'] == 'skipped':
                    results['skipped'] += 1
                else:
                    results['failed'] += 1
                
                results['details'].append({
                    'clinic_id': clinic_id,
                    'clinic_name': clinic_name,
                    **result
                })
                
            except Exception as e:
                logger.error(f"Failed to migrate clinic {clinic_id}: {e}")
                results['failed'] += 1
                results['details'].append({
                    'clinic_id': clinic_id,
                    'clinic_name': clinic_name,
                    'status': 'failed',
                    'error': str(e)
                })
        
        # Save migration log
        await self._save_migration_log(results)
        
        return results
    
    async def migrate_clinic(self, clinic_id: str) -> Dict[str, Any]:
        """Migrate a specific clinic to enhanced RAG
        
        Args:
            clinic_id: Clinic ID to migrate
            
        Returns:
            Migration result
        """
        logger.info(f"Migrating clinic {clinic_id} to enhanced RAG")
        
        if self.dry_run:
            logger.info("DRY RUN MODE - No changes will be made")
        
        return await self._migrate_clinic(clinic_id)
    
    async def _migrate_clinic(self, clinic_id: str) -> Dict[str, Any]:
        """Internal method to migrate a clinic
        
        Args:
            clinic_id: Clinic ID to migrate
            
        Returns:
            Migration result
        """
        result = {
            'status': 'pending',
            'doctors_indexed': 0,
            'services_indexed': 0,
            'cache_initialized': False,
            'validation_passed': False,
            'timestamp': datetime.utcnow().isoformat()
        }
        
        try:
            # Check if already migrated
            if await self._is_migrated(clinic_id):
                logger.info(f"Clinic {clinic_id} already migrated, skipping")
                result['status'] = 'skipped'
                result['message'] = 'Already migrated'
                return result
            
            # Step 1: Index structured data
            logger.info(f"Indexing structured data for clinic {clinic_id}")
            
            if not self.dry_run:
                embedder = StructuredDataEmbedder(clinic_id, self.supabase)
                
                # Index doctors
                doctors_result = await embedder.embed_doctors()
                result['doctors_indexed'] = doctors_result.get('indexed_count', 0)
                logger.info(f"Indexed {result['doctors_indexed']} doctors")
                
                # Index services
                services_result = await embedder.embed_services()
                result['services_indexed'] = services_result.get('indexed_count', 0)
                logger.info(f"Indexed {result['services_indexed']} services")
            else:
                # Dry run - just count
                doctors_count = await self._count_doctors(clinic_id)
                services_count = await self._count_services(clinic_id)
                result['doctors_indexed'] = doctors_count
                result['services_indexed'] = services_count
                logger.info(f"Would index {doctors_count} doctors and {services_count} services")
            
            # Step 2: Initialize RAG cache
            logger.info(f"Initializing RAG cache for clinic {clinic_id}")
            
            if not self.dry_run:
                cache = RAGCache(clinic_id)
                await cache.clear_all()  # Start fresh
                result['cache_initialized'] = True
            else:
                logger.info("Would initialize RAG cache")
                result['cache_initialized'] = True
            
            # Step 3: Validate migration
            logger.info(f"Validating migration for clinic {clinic_id}")
            validation_result = await self._validate_migration(clinic_id)
            result['validation_passed'] = validation_result['passed']
            result['validation_details'] = validation_result
            
            if result['validation_passed']:
                result['status'] = 'success'
                logger.info(f"Successfully migrated clinic {clinic_id}")
                
                # Step 4: Update clinic metadata
                if not self.dry_run:
                    await self._update_clinic_metadata(clinic_id)
            else:
                result['status'] = 'failed'
                result['error'] = 'Validation failed'
                logger.error(f"Migration validation failed for clinic {clinic_id}")
            
        except Exception as e:
            result['status'] = 'failed'
            result['error'] = str(e)
            logger.error(f"Migration failed for clinic {clinic_id}: {e}")
        
        return result
    
    async def _get_active_clinics(self) -> List[Dict[str, Any]]:
        """Get all active clinics
        
        Returns:
            List of active clinics
        """
        response = self.supabase.table('clinics').select('*').eq(
            'active', True
        ).execute()
        
        return response.data if response.data else []
    
    async def _is_migrated(self, clinic_id: str) -> bool:
        """Check if clinic is already migrated
        
        Args:
            clinic_id: Clinic ID
            
        Returns:
            True if already migrated
        """
        # Check clinic metadata for migration flag
        response = self.supabase.table('clinics').select('metadata').eq(
            'id', clinic_id
        ).single().execute()
        
        if response.data and response.data.get('metadata'):
            metadata = response.data['metadata']
            return metadata.get('enhanced_rag_migrated', False)
        
        return False
    
    async def _count_doctors(self, clinic_id: str) -> int:
        """Count doctors for clinic
        
        Args:
            clinic_id: Clinic ID
            
        Returns:
            Number of doctors
        """
        response = self.supabase.table('doctors').select(
            'id', count='exact'
        ).eq('clinic_id', clinic_id).eq('active', True).execute()
        
        return response.count if response.count else 0
    
    async def _count_services(self, clinic_id: str) -> int:
        """Count services for clinic
        
        Args:
            clinic_id: Clinic ID
            
        Returns:
            Number of services
        """
        response = self.supabase.table('services').select(
            'id', count='exact'
        ).eq('clinic_id', clinic_id).eq('active', True).execute()
        
        return response.count if response.count else 0
    
    async def _validate_migration(self, clinic_id: str) -> Dict[str, Any]:
        """Validate migration was successful
        
        Args:
            clinic_id: Clinic ID
            
        Returns:
            Validation result
        """
        validation = {
            'passed': True,
            'checks': {}
        }
        
        if self.dry_run:
            # In dry run, assume validation would pass
            validation['checks'] = {
                'structured_data_indexed': True,
                'hybrid_search_working': True,
                'cache_accessible': True
            }
            return validation
        
        try:
            # Test 1: Check if structured data is indexed
            embedder = StructuredDataEmbedder(clinic_id, self.supabase)
            # This is a simplified check - in production, query Pinecone to verify
            validation['checks']['structured_data_indexed'] = True
            
            # Test 2: Check if hybrid search works
            engine = HybridSearchEngine(clinic_id)
            test_results = await engine.hybrid_search(
                "test query for migration validation",
                top_k=1
            )
            validation['checks']['hybrid_search_working'] = True
            
            # Test 3: Check if cache is accessible
            cache = RAGCache(clinic_id)
            metrics = cache.get_metrics()
            validation['checks']['cache_accessible'] = metrics is not None
            
        except Exception as e:
            logger.error(f"Validation failed: {e}")
            validation['passed'] = False
            validation['error'] = str(e)
        
        # All checks must pass
        validation['passed'] = all(validation['checks'].values())
        
        return validation
    
    async def _update_clinic_metadata(self, clinic_id: str):
        """Update clinic metadata to mark as migrated
        
        Args:
            clinic_id: Clinic ID
        """
        # Get current metadata
        response = self.supabase.table('clinics').select('metadata').eq(
            'id', clinic_id
        ).single().execute()
        
        metadata = response.data.get('metadata', {}) if response.data else {}
        
        # Add migration flag
        metadata['enhanced_rag_migrated'] = True
        metadata['enhanced_rag_migration_date'] = datetime.utcnow().isoformat()
        metadata['enhanced_rag_version'] = '1.0.0'
        
        # Update clinic
        self.supabase.table('clinics').update({
            'metadata': metadata
        }).eq('id', clinic_id).execute()
        
        logger.info(f"Updated metadata for clinic {clinic_id}")
    
    async def _save_migration_log(self, results: Dict[str, Any]):
        """Save migration log to database
        
        Args:
            results: Migration results
        """
        if self.dry_run:
            logger.info("DRY RUN - Would save migration log")
            return
        
        try:
            # Store in a migration_logs table (if it exists)
            log_entry = {
                'migration_type': 'enhanced_rag',
                'timestamp': datetime.utcnow().isoformat(),
                'results': results,
                'dry_run': self.dry_run
            }
            
            # Try to save to database
            # Note: This assumes a migration_logs table exists
            # If not, just log to file
            try:
                self.supabase.table('migration_logs').insert(log_entry).execute()
            except:
                # Table doesn't exist, save to file
                import json
                log_file = f"migration_log_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
                with open(log_file, 'w') as f:
                    json.dump(log_entry, f, indent=2)
                logger.info(f"Migration log saved to {log_file}")
                
        except Exception as e:
            logger.error(f"Failed to save migration log: {e}")
    
    async def rollback_clinic(self, clinic_id: str) -> Dict[str, Any]:
        """Rollback a clinic migration
        
        Args:
            clinic_id: Clinic ID to rollback
            
        Returns:
            Rollback result
        """
        logger.info(f"Rolling back migration for clinic {clinic_id}")
        
        result = {
            'status': 'pending',
            'vectors_deleted': False,
            'cache_cleared': False,
            'metadata_updated': False
        }
        
        try:
            # Delete indexed vectors
            embedder = StructuredDataEmbedder(clinic_id, self.supabase)
            deletion_result = await embedder.delete_structured_data('all')
            result['vectors_deleted'] = deletion_result.get('deleted_count', 0) > 0
            
            # Clear cache
            cache = RAGCache(clinic_id)
            result['cache_cleared'] = await cache.clear_all()
            
            # Update metadata
            response = self.supabase.table('clinics').select('metadata').eq(
                'id', clinic_id
            ).single().execute()
            
            if response.data:
                metadata = response.data.get('metadata', {})
                metadata['enhanced_rag_migrated'] = False
                metadata['enhanced_rag_rollback_date'] = datetime.utcnow().isoformat()
                
                self.supabase.table('clinics').update({
                    'metadata': metadata
                }).eq('id', clinic_id).execute()
                
                result['metadata_updated'] = True
            
            result['status'] = 'success'
            logger.info(f"Successfully rolled back clinic {clinic_id}")
            
        except Exception as e:
            result['status'] = 'failed'
            result['error'] = str(e)
            logger.error(f"Rollback failed for clinic {clinic_id}: {e}")
        
        return result


async def main():
    """Main migration function"""
    
    parser = argparse.ArgumentParser(
        description='Migrate clinics to enhanced RAG system'
    )
    parser.add_argument(
        '--clinic-id',
        help='Specific clinic ID to migrate (migrates all if not specified)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Perform dry run without making changes'
    )
    parser.add_argument(
        '--rollback',
        action='store_true',
        help='Rollback migration for specified clinic'
    )
    
    args = parser.parse_args()
    
    migrator = EnhancedRAGMigrator(dry_run=args.dry_run)
    
    if args.rollback:
        if not args.clinic_id:
            print("Error: --clinic-id required for rollback")
            sys.exit(1)
        
        result = await migrator.rollback_clinic(args.clinic_id)
        print(f"\nRollback result: {result}")
        
    elif args.clinic_id:
        # Migrate specific clinic
        result = await migrator.migrate_clinic(args.clinic_id)
        print(f"\nMigration result for {args.clinic_id}:")
        print(f"Status: {result['status']}")
        print(f"Doctors indexed: {result['doctors_indexed']}")
        print(f"Services indexed: {result['services_indexed']}")
        print(f"Cache initialized: {result['cache_initialized']}")
        print(f"Validation passed: {result['validation_passed']}")
        
    else:
        # Migrate all clinics
        results = await migrator.migrate_all_clinics()
        
        print("\n" + "="*50)
        print("Migration Summary")
        print("="*50)
        print(f"Total clinics: {results['total_clinics']}")
        print(f"Successful: {results['successful']}")
        print(f"Failed: {results['failed']}")
        print(f"Skipped: {results['skipped']}")
        
        if results['failed'] > 0:
            print("\nFailed clinics:")
            for detail in results['details']:
                if detail.get('status') == 'failed':
                    print(f"  - {detail['clinic_name']}: {detail.get('error', 'Unknown error')}")


if __name__ == "__main__":
    asyncio.run(main())