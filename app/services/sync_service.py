"""
Data Synchronization Service for Supabase-NocoDB Integration
Handles bi-directional sync with conflict resolution and real-time updates
"""

from typing import Dict, Any, List, Optional, Set
import asyncio
from datetime import datetime, timezone
import aiohttp
from supabase import Client
import logging
import json
import hashlib
from enum import Enum
from app.services.rule_engine import RuleEngine, RuleType, create_healthcare_rule_engine

logger = logging.getLogger(__name__)


class SyncDirection(Enum):
    """Sync direction options"""
    TO_NOCODB = "to_nocodb"
    TO_SUPABASE = "to_supabase"
    BIDIRECTIONAL = "bidirectional"


class ConflictResolution(Enum):
    """Conflict resolution strategies"""
    MOST_RECENT = "most_recent"  # Use the most recently updated record
    SUPABASE_WINS = "supabase_wins"  # Supabase is source of truth
    NOCODB_WINS = "nocodb_wins"  # NocoDB is source of truth
    MERGE = "merge"  # Merge non-conflicting fields


class DataSyncService:
    """
    Manages bi-directional data synchronization between Supabase and NocoDB
    """
    
    def __init__(
        self, 
        supabase_client: Client, 
        nocodb_url: str, 
        nocodb_token: str,
        conflict_strategy: ConflictResolution = ConflictResolution.MOST_RECENT,
        rule_engine: Optional[RuleEngine] = None
    ):
        self.supabase = supabase_client
        self.nocodb_url = nocodb_url
        self.nocodb_token = nocodb_token
        self.conflict_strategy = conflict_strategy
        self.sync_queue = asyncio.Queue()
        self.processing = False
        self.session: Optional[aiohttp.ClientSession] = None
        
        # Initialize rule engine for validation
        self.rule_engine = rule_engine or create_healthcare_rule_engine()
        
        # Track synced records to prevent infinite loops
        self.recently_synced: Set[str] = set()
        self.sync_lock = asyncio.Lock()
        
        # Table mappings between Supabase and NocoDB
        self.table_mappings = {
            "appointments": "t1_appointments",  # Maps to tier-based tables
            "doctors": "t1_doctors",
            "patients": "t1_patients",
            "services": "t1_services",
            "rooms": "t1_rooms",
            "schedules": "t2_schedules",
            "staff": "t2_staff",
            "wait_list": "t2_wait_list",
            "inventory": "t2_inventory",
            "time_off": "t2_time_off",
            "equipment": "t3_equipment",
            "insurance_plans": "t3_insurance_plans",
            "pricing_rules": "t3_pricing_rules",
            "specialties": "t3_specialties",
            "referrals": "t3_referrals"
        }
        
    async def __aenter__(self):
        """Async context manager entry"""
        self.session = aiohttp.ClientSession(
            headers={
                "xc-auth": self.nocodb_token,
                "Content-Type": "application/json"
            }
        )
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.session:
            await self.session.close()
            
    async def start_processing(self):
        """Start processing sync queue"""
        if self.processing:
            return
            
        self.processing = True
        asyncio.create_task(self._process_queue())
        
    async def stop_processing(self):
        """Stop processing sync queue"""
        self.processing = False
        
    async def _process_queue(self):
        """Process items from sync queue"""
        while self.processing:
            try:
                if not self.sync_queue.empty():
                    sync_item = await self.sync_queue.get()
                    await self._sync_single_item(sync_item)
                else:
                    await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"Error processing sync queue: {e}")
                await asyncio.sleep(1)
                
    def _generate_sync_key(self, table: str, record_id: str) -> str:
        """Generate unique key for sync tracking"""
        return f"{table}:{record_id}"
        
    def _is_recently_synced(self, table: str, record_id: str) -> bool:
        """Check if record was recently synced to prevent loops"""
        sync_key = self._generate_sync_key(table, record_id)
        return sync_key in self.recently_synced
        
    async def _mark_as_synced(self, table: str, record_id: str):
        """Mark record as recently synced"""
        sync_key = self._generate_sync_key(table, record_id)
        self.recently_synced.add(sync_key)
        
        # Clear after 5 seconds to allow future syncs
        await asyncio.sleep(5)
        self.recently_synced.discard(sync_key)
        
    async def sync_table_data(
        self, 
        table_name: str, 
        clinic_id: str,
        direction: SyncDirection = SyncDirection.BIDIRECTIONAL
    ):
        """
        Synchronize table data between Supabase and NocoDB
        
        Args:
            table_name: Name of the table to sync
            clinic_id: Clinic ID for filtering
            direction: Sync direction (to_nocodb, to_supabase, bidirectional)
        """
        logger.info(f"Starting sync for table {table_name}, clinic {clinic_id}, direction {direction.value}")
        
        async with self.sync_lock:
            if direction in [SyncDirection.TO_NOCODB, SyncDirection.BIDIRECTIONAL]:
                await self._sync_to_nocodb(table_name, clinic_id)
                
            if direction in [SyncDirection.TO_SUPABASE, SyncDirection.BIDIRECTIONAL]:
                await self._sync_to_supabase(table_name, clinic_id)
                
    async def _sync_to_nocodb(self, table_name: str, clinic_id: str):
        """Sync data from Supabase to NocoDB"""
        try:
            # Fetch data from Supabase
            response = self.supabase.table(f"healthcare.{table_name}").select("*").eq(
                "clinic_id", clinic_id
            ).execute()
            
            if not response.data:
                logger.info(f"No data to sync for {table_name}")
                return
                
            # Get corresponding NocoDB table name
            nocodb_table = self.table_mappings.get(table_name, table_name)
            
            # Sync each record
            for record in response.data:
                if not self._is_recently_synced(table_name, record.get("id")):
                    await self._upsert_to_nocodb(nocodb_table, record, clinic_id)
                    await self._mark_as_synced(table_name, record.get("id"))
                    
        except Exception as e:
            logger.error(f"Error syncing to NocoDB: {e}")
            raise
            
    async def _sync_to_supabase(self, table_name: str, clinic_id: str):
        """Sync data from NocoDB to Supabase"""
        try:
            # Get corresponding NocoDB table name
            nocodb_table = self.table_mappings.get(table_name, table_name)
            
            # Fetch data from NocoDB
            async with self.session.get(
                f"{self.nocodb_url}/api/v1/db/data/noco/healthcare/{nocodb_table}",
                params={"where": f"(clinic_id,eq,{clinic_id})"}
            ) as response:
                response.raise_for_status()
                data = await response.json()
                
            records = data.get("list", [])
            
            if not records:
                logger.info(f"No data to sync from NocoDB for {table_name}")
                return
                
            # Sync each record
            for record in records:
                if not self._is_recently_synced(table_name, record.get("id")):
                    await self._upsert_to_supabase(table_name, record, clinic_id)
                    await self._mark_as_synced(table_name, record.get("id"))
                    
        except Exception as e:
            logger.error(f"Error syncing to Supabase: {e}")
            raise
            
    async def _upsert_to_nocodb(self, table: str, record: Dict, clinic_id: str):
        """Insert or update record in NocoDB"""
        try:
            record_id = record.get("id")
            
            # Check if record exists
            async with self.session.get(
                f"{self.nocodb_url}/api/v1/db/data/noco/healthcare/{table}/{record_id}"
            ) as response:
                exists = response.status == 200
                
            if exists:
                # Update existing record
                async with self.session.patch(
                    f"{self.nocodb_url}/api/v1/db/data/noco/healthcare/{table}/{record_id}",
                    json=record
                ) as response:
                    response.raise_for_status()
                    logger.debug(f"Updated record {record_id} in NocoDB {table}")
            else:
                # Insert new record
                async with self.session.post(
                    f"{self.nocodb_url}/api/v1/db/data/noco/healthcare/{table}",
                    json=record
                ) as response:
                    response.raise_for_status()
                    logger.debug(f"Inserted record {record_id} in NocoDB {table}")
                    
        except Exception as e:
            logger.error(f"Error upserting to NocoDB: {e}")
            raise
            
    async def _upsert_to_supabase(self, table: str, record: Dict, clinic_id: str):
        """Insert or update record in Supabase"""
        try:
            record["clinic_id"] = clinic_id
            
            # Upsert to Supabase (will handle insert/update automatically)
            response = self.supabase.table(f"healthcare.{table}").upsert(
                record,
                on_conflict="id"
            ).execute()
            
            logger.debug(f"Upserted record {record.get('id')} to Supabase {table}")
            
        except Exception as e:
            logger.error(f"Error upserting to Supabase: {e}")
            raise
            
    async def handle_conflict(
        self,
        table: str,
        record_id: str,
        supabase_data: Dict,
        nocodb_data: Dict
    ) -> Dict:
        """
        Resolve conflicts based on configured strategy
        
        Args:
            table: Table name
            record_id: Record ID
            supabase_data: Data from Supabase
            nocodb_data: Data from NocoDB
            
        Returns:
            Resolved data dictionary
        """
        logger.info(f"Resolving conflict for {table}:{record_id} using {self.conflict_strategy.value}")
        
        if self.conflict_strategy == ConflictResolution.MOST_RECENT:
            # Compare updated_at timestamps
            supabase_updated = datetime.fromisoformat(
                supabase_data.get("updated_at", "1970-01-01")
            )
            nocodb_updated = datetime.fromisoformat(
                nocodb_data.get("updated_at", "1970-01-01")
            )
            
            if supabase_updated > nocodb_updated:
                return supabase_data
            else:
                return nocodb_data
                
        elif self.conflict_strategy == ConflictResolution.SUPABASE_WINS:
            return supabase_data
            
        elif self.conflict_strategy == ConflictResolution.NOCODB_WINS:
            return nocodb_data
            
        elif self.conflict_strategy == ConflictResolution.MERGE:
            # Merge non-conflicting fields
            merged = supabase_data.copy()
            
            for key, value in nocodb_data.items():
                if key not in ["id", "clinic_id", "created_at"]:
                    # Use NocoDB value if Supabase value is None or empty
                    if not supabase_data.get(key):
                        merged[key] = value
                    # If both have values and they differ, use most recent
                    elif supabase_data.get(key) != value:
                        supabase_updated = datetime.fromisoformat(
                            supabase_data.get("updated_at", "1970-01-01")
                        )
                        nocodb_updated = datetime.fromisoformat(
                            nocodb_data.get("updated_at", "1970-01-01")
                        )
                        if nocodb_updated > supabase_updated:
                            merged[key] = value
                            
            return merged
            
        else:
            # Default to most recent
            return await self.handle_conflict(
                table, record_id, supabase_data, nocodb_data
            )
            
    async def queue_sync(self, sync_item: Dict):
        """Add sync item to queue for processing"""
        await self.sync_queue.put(sync_item)
        logger.debug(f"Queued sync item: {sync_item}")
        
    async def _sync_single_item(self, sync_item: Dict):
        """Process a single sync item"""
        try:
            table = sync_item.get("table")
            record_id = sync_item.get("record_id")
            operation = sync_item.get("operation")
            clinic_id = sync_item.get("clinic_id")
            data = sync_item.get("data")
            source = sync_item.get("source", "supabase")
            
            logger.info(f"Processing sync: {operation} on {table}:{record_id} from {source}")
            
            if operation == "DELETE":
                await self._handle_delete(table, record_id, source, clinic_id)
            else:
                await self._handle_upsert(table, record_id, data, source, clinic_id)
                
        except Exception as e:
            logger.error(f"Error syncing item {sync_item}: {e}")
            # Could implement retry logic here
            
    async def _handle_delete(self, table: str, record_id: str, source: str, clinic_id: str):
        """Handle delete synchronization"""
        try:
            if source == "supabase":
                # Delete from NocoDB
                nocodb_table = self.table_mappings.get(table, table)
                async with self.session.delete(
                    f"{self.nocodb_url}/api/v1/db/data/noco/healthcare/{nocodb_table}/{record_id}"
                ) as response:
                    if response.status == 200:
                        logger.info(f"Deleted {record_id} from NocoDB {nocodb_table}")
            else:
                # Delete from Supabase
                self.supabase.table(f"healthcare.{table}").delete().eq(
                    "id", record_id
                ).eq("clinic_id", clinic_id).execute()
                logger.info(f"Deleted {record_id} from Supabase {table}")
                
        except Exception as e:
            logger.error(f"Error handling delete: {e}")
            
    async def _handle_upsert(self, table: str, record_id: str, data: Dict, source: str, clinic_id: str):
        """Handle insert/update synchronization with rule validation"""
        try:
            # Validate data against business rules
            validation_context = {
                "table": table,
                "record_id": record_id,
                "data": data,
                "clinic_id": clinic_id,
                "operation": "upsert",
                "source": source
            }
            
            # Add table-specific context
            if table == "appointments":
                validation_context["appointment"] = data
                validation_context["action"] = "create_appointment" if not record_id else "update_appointment"
            elif table == "services":
                validation_context["service"] = data
            
            # Run validation rules
            validation_result = await self.rule_engine.validate(validation_context)
            
            if not validation_result["valid"]:
                logger.warning(f"Validation failed for {table}:{record_id}: {validation_result['errors']}")
                # You might want to handle this differently based on your requirements
                # For now, we'll log and continue with sync
                
            # Apply any calculation rules (e.g., price calculation)
            calc_results = await self.rule_engine.evaluate(
                validation_context,
                rule_type=RuleType.CALCULATION
            )
            
            # Update data with calculated values if any
            for result in calc_results:
                if result.get("matched") and "results" in result:
                    for action_result in result["results"]:
                        if isinstance(action_result, dict):
                            # Apply calculated fields to data
                            if "calculated_price" in action_result:
                                data["calculated_price"] = action_result["calculated_price"]
            
            # Proceed with sync
            if source == "supabase":
                # Sync to NocoDB
                nocodb_table = self.table_mappings.get(table, table)
                await self._upsert_to_nocodb(nocodb_table, data, clinic_id)
            else:
                # Sync to Supabase
                await self._upsert_to_supabase(table, data, clinic_id)
                
        except Exception as e:
            logger.error(f"Error handling upsert: {e}")
            
    async def get_sync_status(self) -> Dict:
        """Get current sync status and metrics"""
        return {
            "queue_size": self.sync_queue.qsize(),
            "processing": self.processing,
            "recently_synced_count": len(self.recently_synced),
            "conflict_strategy": self.conflict_strategy.value
        }


# Helper functions for webhook processing
async def sync_to_nocodb(payload: Dict):
    """Process Supabase webhook and sync to NocoDB"""
    # This would be called from the webhook handler
    # Implementation depends on webhook payload structure
    pass


async def sync_to_supabase(payload: Dict):
    """Process NocoDB webhook and sync to Supabase"""
    # This would be called from the webhook handler
    # Implementation depends on webhook payload structure
    pass