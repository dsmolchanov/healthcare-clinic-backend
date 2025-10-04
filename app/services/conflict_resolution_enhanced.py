"""
Enhanced Conflict Resolution with Human-in-the-Loop
Extends the real-time conflict detector with dashboard support and manual intervention
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any, Tuple, Callable
from dataclasses import dataclass, field, asdict
from enum import Enum
import json
import uuid

logger = logging.getLogger(__name__)


class ResolutionStatus(Enum):
    """Status of conflict resolution"""
    PENDING = "pending"
    AUTO_RESOLVED = "auto_resolved"
    MANUAL_REVIEW = "manual_review"
    HUMAN_RESOLVED = "human_resolved"
    ESCALATED = "escalated"
    FAILED = "failed"


class HumanInterventionReason(Enum):
    """Reasons for requiring human intervention"""
    HIGH_VALUE_PATIENT = "high_value_patient"
    MULTIPLE_CONFLICTS = "multiple_conflicts"
    POLICY_VIOLATION = "policy_violation"
    UNCERTAIN_RESOLUTION = "uncertain_resolution"
    PATIENT_REQUEST = "patient_request"
    DOCTOR_REQUEST = "doctor_request"
    SYSTEM_ERROR = "system_error"


@dataclass
class ConflictContext:
    """Additional context for conflict resolution"""
    patient_history: List[Dict[str, Any]] = field(default_factory=list)
    doctor_preferences: Dict[str, Any] = field(default_factory=dict)
    clinic_policies: Dict[str, Any] = field(default_factory=dict)
    previous_resolutions: List[Dict[str, Any]] = field(default_factory=list)
    business_impact: Dict[str, Any] = field(default_factory=dict)
    patient_sentiment: Optional[str] = None
    urgency_score: float = 0.0


@dataclass
class ResolutionAction:
    """An action taken to resolve a conflict"""
    action_id: str
    action_type: str
    description: str
    performed_by: str  # 'system' or user ID
    performed_at: datetime
    parameters: Dict[str, Any]
    result: Optional[str] = None
    success: bool = False


@dataclass
class ConflictResolution:
    """Complete conflict resolution record"""
    resolution_id: str
    conflict_id: str
    status: ResolutionStatus
    created_at: datetime
    updated_at: datetime
    resolved_at: Optional[datetime] = None

    # Resolution details
    strategy_used: Optional[str] = None
    actions_taken: List[ResolutionAction] = field(default_factory=list)

    # Human intervention
    requires_human: bool = False
    intervention_reason: Optional[HumanInterventionReason] = None
    assigned_to: Optional[str] = None
    human_notes: Optional[str] = None

    # Outcomes
    resolution_success: bool = False
    patient_notified: bool = False
    doctor_notified: bool = False
    calendar_updated: bool = False

    # Metrics
    resolution_time_seconds: Optional[int] = None
    automation_score: float = 0.0  # 0-1, how much was automated


class EnhancedConflictResolver:
    """
    Enhanced conflict resolution system with human-in-the-loop capabilities
    """

    def __init__(self, redis_client: Any, supabase_client: Any, websocket_manager: Any):
        self.redis = redis_client
        self.supabase = supabase_client
        self.ws_manager = websocket_manager

        # Resolution tracking
        self.active_resolutions: Dict[str, ConflictResolution] = {}
        self.resolution_queue: asyncio.Queue = asyncio.Queue()

        # Human intervention thresholds
        self.auto_resolve_threshold = 0.8  # Confidence threshold for auto-resolution
        self.escalation_timeout = 300  # 5 minutes before escalation

        # Resolution callbacks
        self.resolution_handlers: Dict[str, Callable] = {}

        # Background task for processing queue
        self.processing_task: Optional[asyncio.Task] = None

    async def start(self):
        """Start the resolution processor"""
        if not self.processing_task:
            self.processing_task = asyncio.create_task(self._process_resolution_queue())
            logger.info("Started enhanced conflict resolver")

    async def stop(self):
        """Stop the resolution processor"""
        if self.processing_task:
            self.processing_task.cancel()
            try:
                await self.processing_task
            except asyncio.CancelledError:
                pass
            self.processing_task = None
            logger.info("Stopped enhanced conflict resolver")

    async def _process_resolution_queue(self):
        """Background task to process resolution queue"""
        while True:
            try:
                conflict_data = await self.resolution_queue.get()
                await self._process_conflict_resolution(conflict_data)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error processing resolution queue: {e}")
                await asyncio.sleep(1)

    async def analyze_conflict(
        self,
        conflict: 'ConflictEvent',
        context: Optional[ConflictContext] = None
    ) -> Tuple[float, List['ResolutionSuggestion'], Optional[HumanInterventionReason]]:
        """
        Analyze a conflict and determine resolution approach

        Args:
            conflict: The conflict event to analyze
            context: Additional context for resolution

        Returns:
            Tuple of (confidence_score, suggestions, intervention_reason)
        """
        confidence_score = 0.0
        suggestions = []
        intervention_reason = None

        if not context:
            context = await self._gather_conflict_context(conflict)

        # Analyze conflict complexity
        complexity_factors = {
            'multiple_sources': len(conflict.sources) > 2,
            'high_severity': conflict.severity.value in ['high', 'critical'],
            'vip_patient': context.business_impact.get('vip_status', False),
            'multiple_conflicts': len(context.previous_resolutions) > 2,
            'policy_violation': self._check_policy_violations(conflict, context),
            'negative_sentiment': context.patient_sentiment == 'negative'
        }

        # Calculate confidence score
        complexity_count = sum(complexity_factors.values())
        confidence_score = max(0, 1 - (complexity_count * 0.2))

        # Generate resolution suggestions based on conflict type
        if conflict.conflict_type.value == 'double_booking':
            suggestions = await self._suggest_double_booking_resolution(conflict, context)
        elif conflict.conflict_type.value == 'hold_conflict':
            suggestions = await self._suggest_hold_conflict_resolution(conflict, context)
        elif conflict.conflict_type.value == 'external_override':
            suggestions = await self._suggest_external_override_resolution(conflict, context)
        else:
            suggestions = await self._suggest_generic_resolution(conflict, context)

        # Determine if human intervention is needed
        if confidence_score < self.auto_resolve_threshold:
            if complexity_factors['vip_patient']:
                intervention_reason = HumanInterventionReason.HIGH_VALUE_PATIENT
            elif complexity_factors['multiple_conflicts']:
                intervention_reason = HumanInterventionReason.MULTIPLE_CONFLICTS
            elif complexity_factors['policy_violation']:
                intervention_reason = HumanInterventionReason.POLICY_VIOLATION
            else:
                intervention_reason = HumanInterventionReason.UNCERTAIN_RESOLUTION

        return confidence_score, suggestions, intervention_reason

    async def create_resolution(
        self,
        conflict: 'ConflictEvent',
        context: Optional[ConflictContext] = None
    ) -> ConflictResolution:
        """
        Create a resolution record for a conflict

        Args:
            conflict: The conflict to resolve
            context: Additional context

        Returns:
            ConflictResolution record
        """
        # Analyze the conflict
        confidence, suggestions, intervention_reason = await self.analyze_conflict(conflict, context)

        # Create resolution record
        resolution = ConflictResolution(
            resolution_id=f"res_{uuid.uuid4().hex[:12]}",
            conflict_id=conflict.conflict_id,
            status=ResolutionStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            requires_human=intervention_reason is not None,
            intervention_reason=intervention_reason,
            automation_score=confidence
        )

        # Store in active resolutions
        self.active_resolutions[resolution.resolution_id] = resolution

        # Store in database
        await self._store_resolution(resolution)

        # Queue for processing
        await self.resolution_queue.put({
            'conflict': conflict,
            'resolution': resolution,
            'suggestions': suggestions,
            'context': context
        })

        # Notify dashboard if human intervention needed
        if resolution.requires_human:
            await self._notify_human_intervention(resolution, conflict, suggestions)

        return resolution

    async def _process_conflict_resolution(self, data: Dict[str, Any]):
        """Process a conflict resolution"""
        conflict = data['conflict']
        resolution = data['resolution']
        suggestions = data['suggestions']
        context = data.get('context')

        try:
            if resolution.requires_human:
                # Wait for human intervention or timeout
                await self._wait_for_human_resolution(resolution, self.escalation_timeout)
            else:
                # Attempt automatic resolution
                await self._execute_automatic_resolution(resolution, conflict, suggestions[0] if suggestions else None)

        except asyncio.TimeoutError:
            # Escalate if timeout
            await self._escalate_resolution(resolution, conflict)
        except Exception as e:
            logger.error(f"Error processing resolution {resolution.resolution_id}: {e}")
            resolution.status = ResolutionStatus.FAILED
            await self._update_resolution(resolution)

    async def _execute_automatic_resolution(
        self,
        resolution: ConflictResolution,
        conflict: 'ConflictEvent',
        suggestion: Optional['ResolutionSuggestion']
    ):
        """Execute automatic resolution"""
        if not suggestion:
            resolution.status = ResolutionStatus.FAILED
            await self._update_resolution(resolution)
            return

        # Create action record
        action = ResolutionAction(
            action_id=f"act_{uuid.uuid4().hex[:8]}",
            action_type=suggestion.strategy,
            description=suggestion.description,
            performed_by='system',
            performed_at=datetime.now(timezone.utc),
            parameters={'conflict_id': conflict.conflict_id}
        )

        try:
            # Execute resolution strategy
            if handler := self.resolution_handlers.get(suggestion.strategy):
                result = await handler(conflict, suggestion)
                action.success = result.get('success', False)
                action.result = result.get('message', '')
            else:
                # Default resolution logic
                action.success = True
                action.result = "Resolution applied successfully"

            resolution.actions_taken.append(action)

            if action.success:
                resolution.status = ResolutionStatus.AUTO_RESOLVED
                resolution.resolved_at = datetime.now(timezone.utc)
                resolution.resolution_success = True
                resolution.strategy_used = suggestion.strategy

                # Calculate resolution time
                resolution.resolution_time_seconds = int(
                    (resolution.resolved_at - resolution.created_at).total_seconds()
                )

                # Notify parties
                await self._notify_resolution_complete(resolution, conflict)
            else:
                # If auto-resolution failed, require human intervention
                resolution.status = ResolutionStatus.MANUAL_REVIEW
                resolution.requires_human = True
                resolution.intervention_reason = HumanInterventionReason.SYSTEM_ERROR
                await self._notify_human_intervention(resolution, conflict, [])

        except Exception as e:
            logger.error(f"Error executing automatic resolution: {e}")
            action.success = False
            action.result = str(e)
            resolution.actions_taken.append(action)
            resolution.status = ResolutionStatus.FAILED

        await self._update_resolution(resolution)

    async def handle_human_resolution(
        self,
        resolution_id: str,
        user_id: str,
        action: str,
        parameters: Dict[str, Any],
        notes: Optional[str] = None
    ) -> bool:
        """
        Handle human intervention for conflict resolution

        Args:
            resolution_id: ID of the resolution
            user_id: ID of the user taking action
            action: Action to take
            parameters: Action parameters
            notes: Optional notes from human

        Returns:
            Success status
        """
        if resolution_id not in self.active_resolutions:
            logger.error(f"Resolution {resolution_id} not found")
            return False

        resolution = self.active_resolutions[resolution_id]

        # Create action record
        human_action = ResolutionAction(
            action_id=f"act_{uuid.uuid4().hex[:8]}",
            action_type=action,
            description=f"Manual resolution by {user_id}",
            performed_by=user_id,
            performed_at=datetime.now(timezone.utc),
            parameters=parameters
        )

        try:
            # Execute the action
            if handler := self.resolution_handlers.get(action):
                result = await handler(parameters)
                human_action.success = result.get('success', False)
                human_action.result = result.get('message', '')
            else:
                human_action.success = True
                human_action.result = "Manual action completed"

            resolution.actions_taken.append(human_action)
            resolution.assigned_to = user_id
            resolution.human_notes = notes

            if human_action.success:
                resolution.status = ResolutionStatus.HUMAN_RESOLVED
                resolution.resolved_at = datetime.now(timezone.utc)
                resolution.resolution_success = True
                resolution.strategy_used = action
                resolution.resolution_time_seconds = int(
                    (resolution.resolved_at - resolution.created_at).total_seconds()
                )

                # Notify completion
                await self._notify_resolution_complete(resolution, None)
            else:
                resolution.status = ResolutionStatus.FAILED

        except Exception as e:
            logger.error(f"Error in human resolution: {e}")
            human_action.success = False
            human_action.result = str(e)
            resolution.actions_taken.append(human_action)
            resolution.status = ResolutionStatus.FAILED

        await self._update_resolution(resolution)
        return human_action.success

    async def _wait_for_human_resolution(self, resolution: ConflictResolution, timeout: int):
        """Wait for human intervention with timeout"""
        start_time = datetime.now(timezone.utc)

        while (datetime.now(timezone.utc) - start_time).total_seconds() < timeout:
            # Check if resolution status changed
            current = await self._get_resolution(resolution.resolution_id)
            if current and current.status not in [ResolutionStatus.PENDING, ResolutionStatus.MANUAL_REVIEW]:
                resolution.status = current.status
                resolution.actions_taken = current.actions_taken
                return

            await asyncio.sleep(5)  # Check every 5 seconds

        # Timeout reached
        raise asyncio.TimeoutError(f"Human intervention timeout for {resolution.resolution_id}")

    async def _escalate_resolution(self, resolution: ConflictResolution, conflict: 'ConflictEvent'):
        """Escalate resolution to higher authority"""
        resolution.status = ResolutionStatus.ESCALATED
        resolution.updated_at = datetime.now(timezone.utc)

        # Create escalation action
        action = ResolutionAction(
            action_id=f"act_{uuid.uuid4().hex[:8]}",
            action_type="escalation",
            description="Escalated due to timeout",
            performed_by="system",
            performed_at=datetime.now(timezone.utc),
            parameters={"reason": "timeout", "original_conflict": conflict.conflict_id},
            success=True
        )
        resolution.actions_taken.append(action)

        await self._update_resolution(resolution)

        # Notify escalation
        await self._notify_escalation(resolution, conflict)

    async def _gather_conflict_context(self, conflict: 'ConflictEvent') -> ConflictContext:
        """Gather context information for conflict resolution"""
        context = ConflictContext()

        try:
            # Get patient history
            if patient_id := conflict.details.get('patient_id'):
                history_query = self.supabase.table('appointments').select('*').eq('patient_id', patient_id).limit(10).execute()
                context.patient_history = history_query.data if history_query.data else []

            # Get doctor preferences
            if doctor_id := conflict.doctor_id:
                pref_query = self.supabase.table('doctor_preferences').select('*').eq('doctor_id', doctor_id).execute()
                if pref_query.data:
                    context.doctor_preferences = pref_query.data[0]

            # Get clinic policies
            clinic_query = self.supabase.table('clinic_policies').select('*').execute()
            if clinic_query.data:
                context.clinic_policies = {p['policy_name']: p['policy_value'] for p in clinic_query.data}

            # Check for VIP status
            if patient_id:
                vip_query = self.supabase.table('patients').select('vip_status').eq('id', patient_id).execute()
                if vip_query.data and vip_query.data[0].get('vip_status'):
                    context.business_impact['vip_status'] = True

            # Calculate urgency score
            if conflict.severity.value == 'critical':
                context.urgency_score = 1.0
            elif conflict.severity.value == 'high':
                context.urgency_score = 0.75
            elif conflict.severity.value == 'medium':
                context.urgency_score = 0.5
            else:
                context.urgency_score = 0.25

        except Exception as e:
            logger.error(f"Error gathering conflict context: {e}")

        return context

    def _check_policy_violations(self, conflict: 'ConflictEvent', context: ConflictContext) -> bool:
        """Check if conflict violates any policies"""
        policies = context.clinic_policies

        # Check scheduling policies
        if 'max_daily_appointments' in policies:
            # Check if this would exceed daily limit
            pass

        if 'min_appointment_gap' in policies:
            # Check if minimum gap is maintained
            pass

        if 'no_double_booking' in policies and conflict.conflict_type.value == 'double_booking':
            return True

        return False

    async def _suggest_double_booking_resolution(
        self,
        conflict: 'ConflictEvent',
        context: ConflictContext
    ) -> List['ResolutionSuggestion']:
        """Generate suggestions for double booking conflicts"""
        from app.services.realtime_conflict_detector import ResolutionSuggestion

        suggestions = []

        # Check which appointment was made first
        internal_time = conflict.details.get('internal_booking_time')
        external_time = conflict.details.get('external_booking_time')

        if internal_time and external_time:
            if internal_time < external_time:
                # Internal booking came first
                suggestions.append(ResolutionSuggestion(
                    strategy="keep_internal",
                    description="Keep internal appointment (booked first), update external calendar",
                    priority=1,
                    automatic=True,
                    impact="External calendar will be updated"
                ))
            else:
                # External booking came first
                suggestions.append(ResolutionSuggestion(
                    strategy="keep_external",
                    description="Keep external appointment (booked first), reschedule internal",
                    priority=1,
                    automatic=False,
                    impact="Patient notification required for rescheduling"
                ))

        # Always offer rescheduling option
        suggestions.append(ResolutionSuggestion(
            strategy="reschedule_next",
            description="Reschedule to next available slot",
            priority=2,
            automatic=True,
            impact="Both parties notified of new time"
        ))

        # Offer manual review for complex cases
        if context.business_impact.get('vip_status'):
            suggestions.append(ResolutionSuggestion(
                strategy="manual_review",
                description="Requires manual review due to VIP patient",
                priority=0,
                automatic=False,
                impact="Staff intervention required"
            ))

        return suggestions

    async def _suggest_hold_conflict_resolution(
        self,
        conflict: 'ConflictEvent',
        context: ConflictContext
    ) -> List['ResolutionSuggestion']:
        """Generate suggestions for hold conflicts"""
        from app.services.realtime_conflict_detector import ResolutionSuggestion

        suggestions = [
            ResolutionSuggestion(
                strategy="convert_hold",
                description="Convert hold to confirmed appointment",
                priority=1,
                automatic=True,
                impact="Hold will be confirmed"
            ),
            ResolutionSuggestion(
                strategy="release_hold",
                description="Release expired hold",
                priority=2,
                automatic=True,
                impact="Hold will be cancelled"
            ),
            ResolutionSuggestion(
                strategy="extend_hold",
                description="Extend hold duration by 15 minutes",
                priority=3,
                automatic=False,
                impact="Requires approval"
            )
        ]
        return suggestions

    async def _suggest_external_override_resolution(
        self,
        conflict: 'ConflictEvent',
        context: ConflictContext
    ) -> List['ResolutionSuggestion']:
        """Generate suggestions for external override conflicts"""
        from app.services.realtime_conflict_detector import ResolutionSuggestion

        suggestions = [
            ResolutionSuggestion(
                strategy="accept_external",
                description="Accept external calendar changes",
                priority=1,
                automatic=True,
                impact="Internal appointment updated"
            ),
            ResolutionSuggestion(
                strategy="reject_external",
                description="Reject external changes, restore internal state",
                priority=2,
                automatic=False,
                impact="External calendar will be reverted"
            )
        ]
        return suggestions

    async def _suggest_generic_resolution(
        self,
        conflict: 'ConflictEvent',
        context: ConflictContext
    ) -> List['ResolutionSuggestion']:
        """Generate generic resolution suggestions"""
        from app.services.realtime_conflict_detector import ResolutionSuggestion

        return [
            ResolutionSuggestion(
                strategy="manual_review",
                description="Requires manual review",
                priority=1,
                automatic=False,
                impact="Staff intervention required"
            )
        ]

    async def _notify_human_intervention(
        self,
        resolution: ConflictResolution,
        conflict: 'ConflictEvent',
        suggestions: List['ResolutionSuggestion']
    ):
        """Notify dashboard about required human intervention"""
        notification = {
            'type': 'conflict_human_intervention',
            'resolution_id': resolution.resolution_id,
            'conflict_id': conflict.conflict_id,
            'conflict_type': conflict.conflict_type.value,
            'severity': conflict.severity.value,
            'intervention_reason': resolution.intervention_reason.value if resolution.intervention_reason else None,
            'suggestions': [
                {
                    'strategy': s.strategy,
                    'description': s.description,
                    'automatic': s.automatic
                }
                for s in suggestions
            ],
            'created_at': resolution.created_at.isoformat(),
            'urgency_score': conflict.details.get('urgency_score', 0.5)
        }

        # Send via WebSocket
        await self.ws_manager.broadcast(json.dumps(notification), channel='dashboard')

        # Store in Redis for dashboard polling
        await self.redis.setex(
            f"intervention:{resolution.resolution_id}",
            3600,  # 1 hour TTL
            json.dumps(notification)
        )

        logger.info(f"Notified human intervention for resolution {resolution.resolution_id}")

    async def _notify_resolution_complete(self, resolution: ConflictResolution, conflict: Optional['ConflictEvent']):
        """Notify about completed resolution"""
        notification = {
            'type': 'conflict_resolved',
            'resolution_id': resolution.resolution_id,
            'status': resolution.status.value,
            'resolved_by': resolution.actions_taken[-1].performed_by if resolution.actions_taken else 'system',
            'resolution_time': resolution.resolution_time_seconds,
            'success': resolution.resolution_success,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }

        await self.ws_manager.broadcast(json.dumps(notification), channel='dashboard')
        logger.info(f"Resolution {resolution.resolution_id} completed")

    async def _notify_escalation(self, resolution: ConflictResolution, conflict: 'ConflictEvent'):
        """Notify about escalation"""
        notification = {
            'type': 'conflict_escalated',
            'resolution_id': resolution.resolution_id,
            'conflict_id': conflict.conflict_id,
            'reason': 'timeout',
            'severity': 'high',
            'requires_immediate_action': True,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }

        await self.ws_manager.broadcast(json.dumps(notification), channel='managers')
        logger.warning(f"Escalated resolution {resolution.resolution_id}")

    async def _store_resolution(self, resolution: ConflictResolution):
        """Store resolution in database"""
        try:
            data = {
                'id': resolution.resolution_id,
                'conflict_id': resolution.conflict_id,
                'status': resolution.status.value,
                'requires_human': resolution.requires_human,
                'intervention_reason': resolution.intervention_reason.value if resolution.intervention_reason else None,
                'assigned_to': resolution.assigned_to,
                'automation_score': resolution.automation_score,
                'created_at': resolution.created_at.isoformat(),
                'updated_at': resolution.updated_at.isoformat(),
                'resolved_at': resolution.resolved_at.isoformat() if resolution.resolved_at else None,
                'metadata': json.dumps({
                    'actions': [asdict(a) for a in resolution.actions_taken],
                    'strategy_used': resolution.strategy_used,
                    'human_notes': resolution.human_notes
                })
            }

            self.supabase.table('conflict_resolutions').upsert(data).execute()

        except Exception as e:
            logger.error(f"Error storing resolution: {e}")

    async def _update_resolution(self, resolution: ConflictResolution):
        """Update resolution in database"""
        resolution.updated_at = datetime.now(timezone.utc)
        await self._store_resolution(resolution)

    async def _get_resolution(self, resolution_id: str) -> Optional[ConflictResolution]:
        """Get resolution from database"""
        try:
            result = self.supabase.table('conflict_resolutions').select('*').eq('id', resolution_id).execute()
            if result.data:
                # Reconstruct resolution object from database
                # This is simplified - would need proper deserialization
                return self.active_resolutions.get(resolution_id)
        except Exception as e:
            logger.error(f"Error getting resolution: {e}")
        return None

    def register_resolution_handler(self, strategy: str, handler: Callable):
        """Register a custom resolution handler"""
        self.resolution_handlers[strategy] = handler
        logger.info(f"Registered resolution handler for strategy: {strategy}")

    async def get_pending_interventions(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get list of pending human interventions"""
        interventions = []

        for resolution in self.active_resolutions.values():
            if resolution.requires_human and resolution.status in [ResolutionStatus.PENDING, ResolutionStatus.MANUAL_REVIEW]:
                if user_id and resolution.assigned_to and resolution.assigned_to != user_id:
                    continue

                interventions.append({
                    'resolution_id': resolution.resolution_id,
                    'conflict_id': resolution.conflict_id,
                    'status': resolution.status.value,
                    'intervention_reason': resolution.intervention_reason.value if resolution.intervention_reason else None,
                    'created_at': resolution.created_at.isoformat(),
                    'assigned_to': resolution.assigned_to,
                    'automation_score': resolution.automation_score
                })

        return sorted(interventions, key=lambda x: x['created_at'], reverse=True)

    async def get_resolution_metrics(self, time_range: timedelta = timedelta(days=7)) -> Dict[str, Any]:
        """Get resolution metrics for dashboard"""
        cutoff_time = datetime.now(timezone.utc) - time_range

        metrics = {
            'total_conflicts': 0,
            'auto_resolved': 0,
            'human_resolved': 0,
            'escalated': 0,
            'failed': 0,
            'avg_resolution_time': 0,
            'automation_rate': 0,
            'by_type': {},
            'by_severity': {},
            'pending_interventions': 0
        }

        resolution_times = []

        for resolution in self.active_resolutions.values():
            if resolution.created_at < cutoff_time:
                continue

            metrics['total_conflicts'] += 1

            if resolution.status == ResolutionStatus.AUTO_RESOLVED:
                metrics['auto_resolved'] += 1
            elif resolution.status == ResolutionStatus.HUMAN_RESOLVED:
                metrics['human_resolved'] += 1
            elif resolution.status == ResolutionStatus.ESCALATED:
                metrics['escalated'] += 1
            elif resolution.status == ResolutionStatus.FAILED:
                metrics['failed'] += 1
            elif resolution.requires_human:
                metrics['pending_interventions'] += 1

            if resolution.resolution_time_seconds:
                resolution_times.append(resolution.resolution_time_seconds)

        if resolution_times:
            metrics['avg_resolution_time'] = sum(resolution_times) / len(resolution_times)

        if metrics['total_conflicts'] > 0:
            metrics['automation_rate'] = metrics['auto_resolved'] / metrics['total_conflicts']

        return metrics