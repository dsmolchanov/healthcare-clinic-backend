"""
Data Retention and Purging Policies
Phase 5: HIPAA Compliance Restoration

Implements HIPAA-compliant data retention, archival, and secure purging
Manages lifecycle of PHI data according to legal and regulatory requirements
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from enum import Enum

from supabase import Client
from .hipaa_audit_system import HIPAAAuditSystem, AuditEventType, AuditResult
from .phi_encryption import PHIEncryptionSystem

logger = logging.getLogger(__name__)

class RetentionPolicy(str, Enum):
    """Data retention policy types"""
    PATIENT_RECORDS = "patient_records"          # 7-10 years after last visit
    APPOINTMENT_HISTORY = "appointment_history"  # 7 years
    AUDIT_LOGS = "audit_logs"                   # 6 years minimum
    BILLING_RECORDS = "billing_records"         # 7 years
    PRESCRIPTION_RECORDS = "prescription_records" # 7 years
    INSURANCE_CLAIMS = "insurance_claims"       # 7 years
    COMMUNICATION_LOGS = "communication_logs"   # 3 years
    SYSTEM_LOGS = "system_logs"                # 1 year
    BACKUP_ARCHIVES = "backup_archives"        # 7 years
    TEMPORARY_DATA = "temporary_data"          # 30 days

class PurgeMethod(str, Enum):
    """Methods for data purging"""
    SOFT_DELETE = "soft_delete"           # Mark as deleted, keep encrypted
    HARD_DELETE = "hard_delete"           # Remove from database
    ARCHIVE = "archive"                   # Move to long-term storage
    SECURE_DESTROY = "secure_destroy"     # Cryptographic destruction
    ANONYMIZE = "anonymize"               # Remove identifying information

class DataClassification(str, Enum):
    """Data classification levels"""
    PHI = "phi"                          # Protected Health Information
    PII = "pii"                          # Personally Identifiable Information
    CONFIDENTIAL = "confidential"        # Business confidential
    INTERNAL = "internal"                # Internal use only
    PUBLIC = "public"                    # Public information

@dataclass
class RetentionRule:
    """Data retention rule configuration"""
    policy_name: RetentionPolicy
    classification: DataClassification
    retention_years: int
    grace_period_days: int  # Additional time before automatic purging
    purge_method: PurgeMethod
    table_name: str
    date_column: str  # Column to check for retention calculation
    conditions: Dict[str, Any]  # Additional conditions for rule application
    approval_required: bool  # Whether manual approval is required for purging
    legal_hold_exempt: bool  # Whether this data can be purged during legal holds

@dataclass
class PurgeCandidate:
    """Data identified for potential purging"""
    table_name: str
    record_id: str
    patient_id: Optional[str]
    classification: DataClassification
    retention_rule: RetentionRule
    last_activity_date: datetime
    eligible_purge_date: datetime
    risk_assessment: str
    estimated_size_mb: float

@dataclass
class PurgeOperation:
    """Record of a purge operation"""
    operation_id: str
    initiated_by: str
    initiated_at: datetime
    completed_at: Optional[datetime]
    status: str  # pending, completed, failed, cancelled
    records_processed: int
    records_purged: int
    total_size_mb: float
    purge_method: PurgeMethod
    approval_chain: List[str]
    verification_hash: str

class DataRetentionManager:
    """
    Manages data retention policies and automated purging
    Ensures HIPAA-compliant data lifecycle management
    """

    def __init__(self, supabase: Client, audit_system: HIPAAAuditSystem, encryption_system: PHIEncryptionSystem):
        self.supabase = supabase
        self.audit_system = audit_system
        self.encryption_system = encryption_system

        # Define retention rules
        self.retention_rules = self._initialize_retention_rules()

        # Legal hold status
        self.legal_holds: Dict[str, datetime] = {}

        # Purge operation tracking
        self.active_purge_operations: Dict[str, PurgeOperation] = {}

    def _initialize_retention_rules(self) -> List[RetentionRule]:
        """Initialize standard HIPAA retention rules"""
        return [
            RetentionRule(
                policy_name=RetentionPolicy.PATIENT_RECORDS,
                classification=DataClassification.PHI,
                retention_years=10,
                grace_period_days=90,
                purge_method=PurgeMethod.ARCHIVE,
                table_name="patients",
                date_column="last_visit_date",
                conditions={"active": False},
                approval_required=True,
                legal_hold_exempt=False
            ),
            RetentionRule(
                policy_name=RetentionPolicy.APPOINTMENT_HISTORY,
                classification=DataClassification.PHI,
                retention_years=7,
                grace_period_days=30,
                purge_method=PurgeMethod.ARCHIVE,
                table_name="appointments",
                date_column="appointment_date",
                conditions={"status": "completed"},
                approval_required=False,
                legal_hold_exempt=False
            ),
            RetentionRule(
                policy_name=RetentionPolicy.AUDIT_LOGS,
                classification=DataClassification.CONFIDENTIAL,
                retention_years=6,
                grace_period_days=0,
                purge_method=PurgeMethod.ARCHIVE,
                table_name="hipaa_audit_log",
                date_column="timestamp",
                conditions={},
                approval_required=False,
                legal_hold_exempt=True
            ),
            RetentionRule(
                policy_name=RetentionPolicy.BILLING_RECORDS,
                classification=DataClassification.PHI,
                retention_years=7,
                grace_period_days=30,
                purge_method=PurgeMethod.ARCHIVE,
                table_name="billing_records",
                date_column="created_at",
                conditions={"status": "paid"},
                approval_required=True,
                legal_hold_exempt=False
            ),
            RetentionRule(
                policy_name=RetentionPolicy.COMMUNICATION_LOGS,
                classification=DataClassification.PHI,
                retention_years=3,
                grace_period_days=30,
                purge_method=PurgeMethod.HARD_DELETE,
                table_name="whatsapp_sessions",
                date_column="last_message_at",
                conditions={"status": "inactive"},
                approval_required=False,
                legal_hold_exempt=False
            ),
            RetentionRule(
                policy_name=RetentionPolicy.SYSTEM_LOGS,
                classification=DataClassification.INTERNAL,
                retention_years=1,
                grace_period_days=7,
                purge_method=PurgeMethod.HARD_DELETE,
                table_name="system_logs",
                date_column="created_at",
                conditions={},
                approval_required=False,
                legal_hold_exempt=True
            ),
            RetentionRule(
                policy_name=RetentionPolicy.TEMPORARY_DATA,
                classification=DataClassification.INTERNAL,
                retention_years=0,
                grace_period_days=30,
                purge_method=PurgeMethod.HARD_DELETE,
                table_name="temporary_data",
                date_column="created_at",
                conditions={},
                approval_required=False,
                legal_hold_exempt=True
            )
        ]

    async def scan_for_purge_candidates(
        self,
        policy_filter: Optional[List[RetentionPolicy]] = None,
        dry_run: bool = True
    ) -> List[PurgeCandidate]:
        """
        Scan database for records eligible for purging

        Args:
            policy_filter: Only check specific policies
            dry_run: If True, only identify candidates without purging

        Returns:
            List of purge candidates
        """
        logger.info(f"Scanning for purge candidates (dry_run={dry_run})")

        candidates = []
        current_date = datetime.utcnow()

        # Filter rules if specified
        rules_to_check = self.retention_rules
        if policy_filter:
            rules_to_check = [rule for rule in self.retention_rules if rule.policy_name in policy_filter]

        for rule in rules_to_check:
            try:
                # Calculate cutoff date
                cutoff_date = current_date - timedelta(
                    days=(rule.retention_years * 365) + rule.grace_period_days
                )

                logger.info(f"Checking {rule.table_name} for records older than {cutoff_date}")

                # Build query conditions
                query = self.supabase.table(rule.table_name).select("*")

                # Add date condition
                query = query.lt(rule.date_column, cutoff_date.isoformat())

                # Add additional conditions
                for key, value in rule.conditions.items():
                    query = query.eq(key, value)

                # Execute query
                result = query.execute()
                records = result.data or []

                logger.info(f"Found {len(records)} potential candidates in {rule.table_name}")

                # Process each record
                for record in records:
                    # Check for legal holds
                    patient_id = record.get("patient_id") or record.get("id")
                    if patient_id in self.legal_holds and not rule.legal_hold_exempt:
                        logger.info(f"Skipping record {record.get('id')} due to legal hold")
                        continue

                    # Calculate risk assessment
                    risk_assessment = await self._assess_purge_risk(record, rule)

                    # Estimate size
                    estimated_size = self._estimate_record_size(record)

                    candidate = PurgeCandidate(
                        table_name=rule.table_name,
                        record_id=str(record.get("id", "")),
                        patient_id=patient_id,
                        classification=rule.classification,
                        retention_rule=rule,
                        last_activity_date=datetime.fromisoformat(record[rule.date_column]) if record.get(rule.date_column) else current_date,
                        eligible_purge_date=cutoff_date,
                        risk_assessment=risk_assessment,
                        estimated_size_mb=estimated_size
                    )

                    candidates.append(candidate)

            except Exception as e:
                logger.error(f"Error scanning {rule.table_name}: {str(e)}")

        logger.info(f"Total purge candidates found: {len(candidates)}")
        return candidates

    async def _assess_purge_risk(self, record: Dict[str, Any], rule: RetentionRule) -> str:
        """Assess the risk level of purging a specific record"""
        risk_factors = []

        # Check for recent access
        if rule.classification == DataClassification.PHI:
            # Check audit logs for recent access
            try:
                recent_access = self.supabase.table("hipaa_audit_log") \
                    .select("*") \
                    .eq("patient_id", record.get("patient_id")) \
                    .gte("timestamp", (datetime.utcnow() - timedelta(days=30)).isoformat()) \
                    .execute()

                if recent_access.data:
                    risk_factors.append("recent_access")
            except Exception:
                pass

        # Check for outstanding billing
        if "billing" in rule.table_name.lower():
            if record.get("status") != "paid":
                risk_factors.append("unpaid_billing")

        # Check for active relationships
        if rule.table_name == "patients":
            # Check for recent appointments
            try:
                future_appointments = self.supabase.table("appointments") \
                    .select("*") \
                    .eq("patient_id", record.get("id")) \
                    .gte("appointment_date", datetime.utcnow().isoformat()) \
                    .execute()

                if future_appointments.data:
                    risk_factors.append("future_appointments")
            except Exception:
                pass

        # Determine overall risk level
        if not risk_factors:
            return "low"
        elif len(risk_factors) == 1:
            return "medium"
        else:
            return "high"

    def _estimate_record_size(self, record: Dict[str, Any]) -> float:
        """Estimate the size of a record in MB"""
        try:
            # Simple estimation based on JSON size
            record_json = json.dumps(record)
            size_bytes = len(record_json.encode('utf-8'))
            return size_bytes / (1024 * 1024)  # Convert to MB
        except Exception:
            return 0.001  # Default 1KB

    async def execute_purge_operation(
        self,
        candidates: List[PurgeCandidate],
        initiated_by: str,
        approved_by: List[str] = None,
        force: bool = False
    ) -> PurgeOperation:
        """
        Execute a purge operation on selected candidates

        Args:
            candidates: Records to purge
            initiated_by: User initiating the purge
            approved_by: Users who approved the purge
            force: Override safety checks

        Returns:
            PurgeOperation record
        """
        operation_id = f"purge_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

        logger.info(f"Starting purge operation {operation_id} for {len(candidates)} candidates")

        # Create purge operation record
        operation = PurgeOperation(
            operation_id=operation_id,
            initiated_by=initiated_by,
            initiated_at=datetime.utcnow(),
            completed_at=None,
            status="pending",
            records_processed=0,
            records_purged=0,
            total_size_mb=sum(c.estimated_size_mb for c in candidates),
            purge_method=candidates[0].retention_rule.purge_method if candidates else PurgeMethod.HARD_DELETE,
            approval_chain=approved_by or [],
            verification_hash=""
        )

        self.active_purge_operations[operation_id] = operation

        try:
            # Audit the start of purge operation
            await self.audit_system.log_audit_event(
                event_type=AuditEventType.ADMIN_ACTION,
                user_id=initiated_by,
                user_role="admin",
                result=AuditResult.SUCCESS,
                resource_accessed=f"purge_operation:{operation_id}",
                ip_address="internal",
                user_agent="retention_manager",
                session_id="system",
                organization_id="default",
                reason=f"Data retention purge started: {len(candidates)} candidates",
                data_volume=len(candidates),
                metadata={
                    "operation_id": operation_id,
                    "total_size_mb": operation.total_size_mb,
                    "purge_method": operation.purge_method.value
                }
            )

            # Process each candidate
            for candidate in candidates:
                try:
                    # Safety checks
                    if not force and candidate.risk_assessment == "high":
                        logger.warning(f"Skipping high-risk record {candidate.record_id}")
                        continue

                    if candidate.retention_rule.approval_required and not approved_by:
                        logger.warning(f"Skipping record {candidate.record_id} - approval required")
                        continue

                    # Execute purge based on method
                    success = await self._execute_single_purge(candidate, operation_id)

                    operation.records_processed += 1
                    if success:
                        operation.records_purged += 1

                        # Audit each purged record
                        await self.audit_system.log_audit_event(
                            event_type=AuditEventType.PHI_DELETE,
                            user_id=initiated_by,
                            user_role="admin",
                            patient_id=candidate.patient_id,
                            result=AuditResult.SUCCESS,
                            resource_accessed=f"{candidate.table_name}:{candidate.record_id}",
                            ip_address="internal",
                            user_agent="retention_manager",
                            session_id="system",
                            organization_id="default",
                            reason=f"Data retention purge: {candidate.retention_rule.policy_name.value}",
                            metadata={
                                "operation_id": operation_id,
                                "purge_method": candidate.retention_rule.purge_method.value,
                                "last_activity": candidate.last_activity_date.isoformat(),
                                "risk_assessment": candidate.risk_assessment
                            }
                        )

                except Exception as e:
                    logger.error(f"Error purging record {candidate.record_id}: {str(e)}")

            # Complete operation
            operation.completed_at = datetime.utcnow()
            operation.status = "completed"

            # Generate verification hash
            operation.verification_hash = self._generate_operation_hash(operation)

            # Store operation record
            await self._store_purge_operation(operation)

            logger.info(f"Purge operation {operation_id} completed: {operation.records_purged}/{operation.records_processed} records purged")

        except Exception as e:
            operation.status = "failed"
            operation.completed_at = datetime.utcnow()
            logger.error(f"Purge operation {operation_id} failed: {str(e)}")

        finally:
            # Remove from active operations
            self.active_purge_operations.pop(operation_id, None)

        return operation

    async def _execute_single_purge(self, candidate: PurgeCandidate, operation_id: str) -> bool:
        """Execute purge for a single record"""
        try:
            if candidate.retention_rule.purge_method == PurgeMethod.SOFT_DELETE:
                # Mark as deleted
                result = self.supabase.table(candidate.table_name) \
                    .update({
                        "deleted_at": datetime.utcnow().isoformat(),
                        "purge_operation_id": operation_id,
                        "purge_reason": candidate.retention_rule.policy_name.value
                    }) \
                    .eq("id", candidate.record_id) \
                    .execute()

            elif candidate.retention_rule.purge_method == PurgeMethod.HARD_DELETE:
                # Permanently delete
                result = self.supabase.table(candidate.table_name) \
                    .delete() \
                    .eq("id", candidate.record_id) \
                    .execute()

            elif candidate.retention_rule.purge_method == PurgeMethod.ARCHIVE:
                # Move to archive table
                record_result = self.supabase.table(candidate.table_name) \
                    .select("*") \
                    .eq("id", candidate.record_id) \
                    .single() \
                    .execute()

                if record_result.data:
                    # Add archive metadata
                    archive_record = record_result.data.copy()
                    archive_record["archived_at"] = datetime.utcnow().isoformat()
                    archive_record["purge_operation_id"] = operation_id
                    archive_record["original_table"] = candidate.table_name

                    # Insert into archive
                    self.supabase.table(f"{candidate.table_name}_archive") \
                        .insert(archive_record) \
                        .execute()

                    # Delete from original table
                    self.supabase.table(candidate.table_name) \
                        .delete() \
                        .eq("id", candidate.record_id) \
                        .execute()

            elif candidate.retention_rule.purge_method == PurgeMethod.SECURE_DESTROY:
                # Cryptographic destruction by key rotation
                if candidate.classification == DataClassification.PHI:
                    # This would rotate encryption keys making data unrecoverable
                    pass

                # Then hard delete
                result = self.supabase.table(candidate.table_name) \
                    .delete() \
                    .eq("id", candidate.record_id) \
                    .execute()

            elif candidate.retention_rule.purge_method == PurgeMethod.ANONYMIZE:
                # Remove identifying information
                anonymized_data = await self._anonymize_record(candidate)
                result = self.supabase.table(candidate.table_name) \
                    .update(anonymized_data) \
                    .eq("id", candidate.record_id) \
                    .execute()

            return True

        except Exception as e:
            logger.error(f"Failed to purge record {candidate.record_id}: {str(e)}")
            return False

    async def _anonymize_record(self, candidate: PurgeCandidate) -> Dict[str, Any]:
        """Anonymize a record by removing PHI"""
        # Get the record
        result = self.supabase.table(candidate.table_name) \
            .select("*") \
            .eq("id", candidate.record_id) \
            .single() \
            .execute()

        if not result.data:
            return {}

        record = result.data.copy()

        # Define PHI fields to anonymize by table
        phi_fields = {
            "patients": ["first_name", "last_name", "ssn", "phone", "email", "address"],
            "appointments": ["notes", "diagnosis", "treatment"],
            "billing_records": ["insurance_id", "payment_method"]
        }

        # Anonymize PHI fields
        for field in phi_fields.get(candidate.table_name, []):
            if field in record:
                record[field] = "[ANONYMIZED]"

        # Add anonymization metadata
        record["anonymized_at"] = datetime.utcnow().isoformat()
        record["anonymization_reason"] = "data_retention"

        return record

    def _generate_operation_hash(self, operation: PurgeOperation) -> str:
        """Generate verification hash for purge operation"""
        import hashlib

        hash_data = f"{operation.operation_id}:{operation.initiated_by}:{operation.records_purged}:{operation.completed_at}"
        return hashlib.sha256(hash_data.encode()).hexdigest()

    async def _store_purge_operation(self, operation: PurgeOperation):
        """Store purge operation record for audit purposes"""
        try:
            operation_record = {
                "operation_id": operation.operation_id,
                "initiated_by": operation.initiated_by,
                "initiated_at": operation.initiated_at.isoformat(),
                "completed_at": operation.completed_at.isoformat() if operation.completed_at else None,
                "status": operation.status,
                "records_processed": operation.records_processed,
                "records_purged": operation.records_purged,
                "total_size_mb": operation.total_size_mb,
                "purge_method": operation.purge_method.value,
                "approval_chain": operation.approval_chain,
                "verification_hash": operation.verification_hash,
                "created_at": datetime.utcnow().isoformat()
            }

            self.supabase.table("data_purge_operations") \
                .insert(operation_record) \
                .execute()

        except Exception as e:
            logger.error(f"Failed to store purge operation record: {str(e)}")

    async def set_legal_hold(self, patient_id: str, reason: str, initiated_by: str):
        """Place a legal hold on patient data"""
        self.legal_holds[patient_id] = datetime.utcnow()

        # Audit the legal hold
        await self.audit_system.log_audit_event(
            event_type=AuditEventType.ADMIN_ACTION,
            user_id=initiated_by,
            user_role="admin",
            patient_id=patient_id,
            result=AuditResult.SUCCESS,
            resource_accessed=f"patient_data:{patient_id}",
            ip_address="internal",
            user_agent="retention_manager",
            session_id="system",
            organization_id="default",
            reason=f"Legal hold placed: {reason}",
            metadata={"legal_hold_reason": reason}
        )

        logger.info(f"Legal hold placed on patient {patient_id}: {reason}")

    async def release_legal_hold(self, patient_id: str, reason: str, initiated_by: str):
        """Release a legal hold on patient data"""
        if patient_id in self.legal_holds:
            del self.legal_holds[patient_id]

            # Audit the release
            await self.audit_system.log_audit_event(
                event_type=AuditEventType.ADMIN_ACTION,
                user_id=initiated_by,
                user_role="admin",
                patient_id=patient_id,
                result=AuditResult.SUCCESS,
                resource_accessed=f"patient_data:{patient_id}",
                ip_address="internal",
                user_agent="retention_manager",
                session_id="system",
                organization_id="default",
                reason=f"Legal hold released: {reason}",
                metadata={"release_reason": reason}
            )

            logger.info(f"Legal hold released for patient {patient_id}: {reason}")

    async def generate_retention_report(self, start_date: datetime, end_date: datetime) -> Dict[str, Any]:
        """Generate data retention compliance report"""

        # Get purge operations in period
        purge_ops = []
        try:
            result = self.supabase.table("data_purge_operations") \
                .select("*") \
                .gte("initiated_at", start_date.isoformat()) \
                .lte("initiated_at", end_date.isoformat()) \
                .execute()
            purge_ops = result.data or []
        except Exception:
            pass

        # Scan current candidates
        candidates = await self.scan_for_purge_candidates(dry_run=True)

        # Calculate metrics
        total_records_purged = sum(op["records_purged"] for op in purge_ops)
        total_size_purged_mb = sum(op["total_size_mb"] for op in purge_ops)
        pending_purge_count = len(candidates)
        pending_purge_size_mb = sum(c.estimated_size_mb for c in candidates)

        # Group candidates by risk level
        risk_breakdown = {"low": 0, "medium": 0, "high": 0}
        for candidate in candidates:
            risk_breakdown[candidate.risk_assessment] += 1

        report = {
            "report_period": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat()
            },
            "purge_operations": {
                "total_operations": len(purge_ops),
                "total_records_purged": total_records_purged,
                "total_size_purged_mb": total_size_purged_mb,
                "operations": purge_ops
            },
            "pending_purges": {
                "total_candidates": pending_purge_count,
                "total_size_mb": pending_purge_size_mb,
                "risk_breakdown": risk_breakdown
            },
            "legal_holds": {
                "active_holds": len(self.legal_holds),
                "hold_details": [
                    {"patient_id": pid, "since": date.isoformat()}
                    for pid, date in self.legal_holds.items()
                ]
            },
            "retention_policies": {
                "total_policies": len(self.retention_rules),
                "policies": [
                    {
                        "name": rule.policy_name.value,
                        "retention_years": rule.retention_years,
                        "table": rule.table_name,
                        "purge_method": rule.purge_method.value
                    }
                    for rule in self.retention_rules
                ]
            },
            "generated_at": datetime.utcnow().isoformat()
        }

        return report

# Automated retention management task
async def run_automated_retention_check(
    retention_manager: DataRetentionManager,
    policies_to_check: List[RetentionPolicy] = None
):
    """Run automated retention check and purge low-risk candidates"""
    try:
        logger.info("Starting automated retention check")

        # Scan for candidates
        candidates = await retention_manager.scan_for_purge_candidates(
            policy_filter=policies_to_check,
            dry_run=False
        )

        # Filter for low-risk, non-approval-required candidates
        auto_purge_candidates = [
            c for c in candidates
            if c.risk_assessment == "low" and not c.retention_rule.approval_required
        ]

        if auto_purge_candidates:
            logger.info(f"Auto-purging {len(auto_purge_candidates)} low-risk candidates")

            # Execute purge
            operation = await retention_manager.execute_purge_operation(
                candidates=auto_purge_candidates,
                initiated_by="system_automation",
                force=False
            )

            logger.info(f"Automated purge completed: {operation.records_purged} records purged")
        else:
            logger.info("No candidates eligible for automated purging")

    except Exception as e:
        logger.error(f"Automated retention check failed: {str(e)}")

# Initialize retention manager
def init_retention_manager(
    supabase: Client,
    audit_system: HIPAAAuditSystem,
    encryption_system: PHIEncryptionSystem
) -> DataRetentionManager:
    """Initialize data retention manager"""
    return DataRetentionManager(supabase, audit_system, encryption_system)