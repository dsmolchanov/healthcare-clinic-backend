import asyncio
import os
import yaml
import json
import sys
import argparse
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock, patch
from contextlib import ExitStack
from dotenv import load_dotenv

# Ensure paths are correct
sys.path.append(os.getcwd())

from app.api.pipeline_message_processor import PipelineMessageProcessor, MessageRequest
from app.services.router_service import RouterService
from tests.evals.judge import LLMJudge

# Load environment variables
load_dotenv()

# Mock environment variables if not present
if "OPENAI_API_KEY" not in os.environ:
    print("⚠️ OPENAI_API_KEY not found in environment. Please set it to run evals.")
    sys.exit(1)

async def run_evals():
    # Parse arguments
    parser = argparse.ArgumentParser(description="Run evaluations for Healthcare Agent")
    parser.add_argument("scenario_file", nargs="?", default="tests/evals/scenarios.yaml", help="Path to scenarios YAML file")
    parser.add_argument("--real-data", action="store_true", help="Run against real Supabase data (disable DB/Tool mocks)")
    parser.add_argument("--clinic-id", default="test-clinic", help="Clinic ID to test against (default: test-clinic)")
    parser.add_argument("--trace", action="store_true", help="Enable Langfuse tracing (sends traces to Langfuse dashboard)")
    args = parser.parse_args()

    # Load scenarios
    try:
        print(f"📂 Loading scenarios from: {args.scenario_file}")
        with open(args.scenario_file, "r") as f:
            data = yaml.safe_load(f)
            scenarios = data["scenarios"]
    except FileNotFoundError:
        print(f"❌ Scenarios file not found at {args.scenario_file}")
        sys.exit(1)

    # Initialize Judge
    judge = LLMJudge()

    print(f"🔧 Initializing Agent (Real Data: {args.real_data}, Tracing: {args.trace})...")
    
    with ExitStack() as stack:
        # --- ALWAYS MOCK THESE ---
        # Mock Logger to avoid polluting metrics
        stack.enter_context(patch('app.api.async_message_logger.AsyncMessageLogger'))
        # Mock Analyzer to avoid extra latency/complexity if not needed for core logic
        mock_analyzer = MagicMock()
        mock_analyzer.analyze_agent_response = AsyncMock(return_value={'turn_status': 'user_turn'})
        stack.enter_context(patch('app.services.response_analyzer.ResponseAnalyzer', return_value=mock_analyzer))
        # Mock Escalation Handler
        mock_escalation = MagicMock()
        mock_escalation.check_if_should_escalate = AsyncMock(return_value={'should_escalate': False, 'reason': None})
        stack.enter_context(patch('app.services.escalation_handler.EscalationHandler', return_value=mock_escalation))
        # Mock Followup Scheduler
        stack.enter_context(patch('app.services.followup_scheduler.FollowupScheduler'))
        # Mock Constraints Manager (usually internal logic, but can be mocked)
        # Constraints Manager
        from app.services.conversation_constraints import ConversationConstraints

        class FakeConstraintsManager:
            def __init__(self, *args, **kwargs):
                self.constraints = ConversationConstraints()
                self.constraints.excluded_doctors = set()
                self.constraints.excluded_services = set()

            async def update_constraints(self, session_id, desired_service=None, desired_doctor=None, exclude_doctor=None, exclude_service=None, time_window=None):
                if desired_service:
                    self.constraints.desired_service = desired_service
                if desired_doctor:
                    self.constraints.desired_doctor = desired_doctor
                if exclude_doctor:
                    self.constraints.excluded_doctors.add(exclude_doctor)
                if exclude_service:
                    self.constraints.excluded_services.add(exclude_service)
                if time_window:
                    self.constraints.time_window_start = time_window[0]
                    self.constraints.time_window_display = f"{time_window[0]} to {time_window[0]}" # Simple display for test
                return self.constraints

            async def get_constraints(self, session_id):
                return self.constraints

            async def clear_constraints(self, session_id):
                self.constraints.desired_service = None
                self.constraints.desired_doctor = None
                self.constraints.excluded_doctors = set()
                self.constraints.excluded_services = set()
                self.constraints.time_window_start = None

        mock_cm_instance = FakeConstraintsManager()
        # When ConstraintsManager() is called, it should return this instance
        stack.enter_context(patch('app.services.conversation_constraints.ConstraintsManager', return_value=mock_cm_instance))
        # Mock Constraint Extractor
        # Mock ConstraintExtractor unless using real data
        if not args.real_data:
            mock_ce = MagicMock()
            mock_ce.detect_meta_reset.return_value = False
            stack.enter_context(patch('app.services.constraint_extractor.ConstraintExtractor', return_value=mock_ce))
        else:
            # For real data, we might want to mock it if it's not fully configured, 
            # but to avoid MagicMock serialization errors, we should let it run or use a better mock.
            # For now, let's NOT mock it and assume it works or fails gracefully.
            pass
        # Mock State Echo Formatter
        # State Echo Formatter
        mock_formatter = MagicMock()
        mock_formatter.format_response.side_effect = lambda response, *args, **kwargs: response
        stack.enter_context(patch('app.services.state_echo_formatter.StateEchoFormatter', return_value=mock_formatter))

        # Mock Langfuse unless --trace flag is set
        if args.trace:
            print("  - 📊 Langfuse tracing ENABLED (traces will be sent to dashboard)")
        else:
            stack.enter_context(patch('langfuse.Langfuse', MagicMock()))

        # Mock LLM Factory - We ALWAYS mock this to inject our capturing wrapper
        # This allows us to capture tool calls even when using real tools
        # Note: Pipeline uses get_llm_factory from app.services.llm.llm_factory
        mock_get_factory = stack.enter_context(patch('app.services.llm.llm_factory.get_llm_factory'))

        # --- CONDITIONAL MOCKS ---
        if not args.real_data:
            print("  - Mocking Database, Tools, and Session State")
            
            # Memory Manager
            mock_mm = MagicMock()
            mock_mm.get_or_create_session = AsyncMock(return_value={'id': 'test-session', 'metadata': {}})
            mock_mm.get_session_by_id = AsyncMock(return_value={'id': 'test-session', 'metadata': {}})
            mock_mm.store_message = AsyncMock()
            mock_mm.get_user_preferences = AsyncMock(return_value={})
            mock_mm.get_memory_context = AsyncMock(return_value=[])
            mock_mm.get_conversation_history = AsyncMock(return_value=[])
            stack.enter_context(patch('app.memory.conversation_memory.get_memory_manager', return_value=mock_mm))

            # Patch datetime in LLM step to fix "Today" context
            mock_dt = MagicMock()
            # Set "now" to Wednesday, Nov 26, 2025 (a weekday when clinic is open)
            mock_dt.now.return_value = datetime(2025, 11, 26, 10, 0, 0)
            mock_dt.fromisoformat = datetime.fromisoformat
            stack.enter_context(patch('app.api.pipeline.steps.llm_step.datetime', mock_dt))

            # Session Manager
            mock_sm = MagicMock()
            mock_sm.check_and_manage_boundary = AsyncMock(return_value=('test-session', False, 'none'))
            mock_lock = MagicMock()
            mock_lock.acquire = MagicMock()
            mock_lock.acquire.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_lock.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_sm.boundary_lock = mock_lock
            stack.enter_context(patch('app.services.session_manager.SessionManager', return_value=mock_sm))

            # Tool State Gate (Patched in Executor)
            mock_gate = MagicMock()
            mock_gate.validate_tool_call.return_value = (True, None, None)
            stack.enter_context(patch('app.services.tool_state_gate.ToolStateGate', return_value=mock_gate))

            # MessageContextHydrator (Replaces CacheService mock)
            mock_hydrator = MagicMock()
            mock_hydrator.hydrate = AsyncMock(return_value={
                'clinic': {
                    'id': args.clinic_id,
                    'name': 'Test Dental Clinic',
                    'phone': '+15550000000',
                    'location': '123 Test St',
                    'services': ['Dental Cleaning', 'Root Canal', 'Teeth Whitening'],
                    'hours': {
                        'weekdays': '9 AM - 5 PM',
                        'saturday': '10 AM - 2 PM',
                        'sunday': 'Closed'
                    },
                    'service_aliases': {'cleaning': 'Dental Cleaning'},
                    'doctors': [] # Empty to force tool usage
                },
                'patient': {
                    'id': 'test-patient',
                    'first_name': 'John',
                    'last_name': 'Doe',
                    'phone': '+15551112222',
                    'preferred_language': 'en'
                },
                'session_state': {},
                'services': [],
                'doctors': [],
                'faqs': [],
                'history': [],
                'preferences': {},
                'profile': None,
                'conversation_state': None
            })
            stack.enter_context(patch('app.services.message_context_hydrator.MessageContextHydrator', return_value=mock_hydrator))

            # Supabase Client (used in multiple places)
            mock_supabase = MagicMock()
            stack.enter_context(patch('app.database.get_healthcare_client', return_value=mock_supabase))
            stack.enter_context(patch('app.database.get_main_client', return_value=mock_supabase))
            stack.enter_context(patch('app.database.create_supabase_client', return_value=mock_supabase))
            
            # Patch SummarySearchService instance in conversation_history_tools
            # This is needed because it's instantiated at module level
            mock_summary_search = MagicMock()
            mock_summary_search.search_summaries = AsyncMock(return_value=[])
            stack.enter_context(patch('app.tools.conversation_history_tools.summary_search', mock_summary_search))

            # Tools
            # ClinicInfoTool (Patched in Handler)
            mock_clinic_tool = MagicMock()
            mock_clinic_tool.get_clinic_info = AsyncMock(return_value={
                'name': 'Test Dental Clinic',
                'address': '123 Test St',
                'hours': {'weekdays': '9 AM - 5 PM'},
                'phone': '+15550000000',
                'email': 'contact@testclinic.com'
            })
            mock_clinic_tool.get_doctor_count = AsyncMock(return_value={
                'total_doctors': 3,
                'specializations': {
                    'General': [{'name': 'Dr. Smith', 'id': 'doc-1'}],
                    'Orthodontics': [{'name': 'Dr. Shtern', 'id': 'doc-2'}]
                },
                'doctor_list': ['Dr. Smith', 'Dr. Shtern'],
                'doctor_details': [
                    {'name': 'Dr. Smith', 'id': 'doc-1', 'specialization': 'General'},
                    {'name': 'Dr. Shtern', 'id': 'doc-2', 'specialization': 'Orthodontics'}
                ]
            })
            stack.enter_context(patch('app.services.tools.clinic_info_handler.ClinicInfoTool', return_value=mock_clinic_tool))
            # Also mock ClinicDataCache in handler
            mock_cdc = MagicMock()
            mock_cdc.get_services = AsyncMock(return_value=[{'name': 'Dental Cleaning'}, {'name': 'Root Canal'}])
            stack.enter_context(patch('app.services.tools.clinic_info_handler.ClinicDataCache', return_value=mock_cdc))

            # PriceQueryTool (Patched in Handler)
            mock_price_tool = MagicMock()
            mock_price_tool.get_services_by_query = AsyncMock(return_value=[
                {'name': 'Dental Cleaning', 'price': 100.0, 'base_price': 100.0},
                {'name': 'Root Canal', 'price': 500.0, 'base_price': 500.0},
                {'name': 'Teeth Whitening', 'price': 300.0, 'base_price': 300.0}
            ])
            stack.enter_context(patch('app.services.tools.price_handler.PriceQueryTool', return_value=mock_price_tool))

            # ReservationTools (Patched in Handlers)
            mock_reservation_tools = MagicMock()
            # Slots must use 'datetime' field in ISO format (not separate date/start_time)
            # This matches the real IntelligentScheduler output format
            # Slots are for Nov 27 (Thursday = tomorrow) and Nov 28 (Friday)
            mock_reservation_tools.check_availability_tool = AsyncMock(return_value={
                'success': True,
                'available_slots': [
                    {'datetime': '2025-11-27T09:00:00', 'doctor_name': 'Dr. Smith', 'doctor_id': 'doc-1'},
                    {'datetime': '2025-11-27T10:00:00', 'doctor_name': 'Dr. Shtern', 'doctor_id': 'doc-2'},
                    {'datetime': '2025-11-27T14:00:00', 'doctor_name': 'Dr. Smith', 'doctor_id': 'doc-1'},
                    {'datetime': '2025-11-28T09:00:00', 'doctor_name': 'Dr. Shtern', 'doctor_id': 'doc-2'},
                    {'datetime': '2025-11-28T11:00:00', 'doctor_name': 'Dr. Smith', 'doctor_id': 'doc-1'}
                ],
                'recommendation': 'We have morning and afternoon slots available.'
            })
            # Dynamic book_appointment mock - signature must match real reservation_tools.book_appointment_tool
            async def mock_book_appointment(
                patient_info,
                service_id,
                datetime_str,
                doctor_id=None,
                notes=None,
                hold_id=None,
                idempotency_key=None,
                **kwargs
            ):
                # Parse datetime_str to make it look nice
                display_date = datetime_str.replace('T', ' ') if datetime_str else 'Unknown'

                # Determine doctor name based on doctor_id
                doctor_name = "Dr. Smith"  # Default
                if doctor_id == "doc-2" or (doctor_id and "shtern" in str(doctor_id).lower()):
                    doctor_name = "Dr. Shtern"
                elif doctor_id == "doc-1":
                    doctor_name = "Dr. Smith"

                return {
                    'success': True,
                    'appointment_id': 'appt-123',
                    'appointment': {
                        'date': display_date,
                        'doctor_name': doctor_name,
                        'start_time': datetime_str.split('T')[1] if datetime_str and 'T' in datetime_str else None
                    },
                    'confirmation_message': f'Appointment booked successfully for {display_date} with {doctor_name}'
                }

            mock_reservation_tools.book_appointment_tool = AsyncMock(side_effect=mock_book_appointment)
            
            # Cancel Appointment Mock
            mock_reservation_tools.cancel_appointment_tool = AsyncMock(return_value={
                'success': True,
                'message': 'Appointment cancelled successfully'
            })
            
            # Reschedule Appointment Mock (New for v3)
            mock_reservation_tools.reschedule_appointment_tool = AsyncMock(return_value={
                'success': True,
                'appointment_id': 'appt-456',
                'old_appointment_id': 'appt-123',
                'message': 'Appointment rescheduled successfully'
            })
            # Patch in both handlers
            stack.enter_context(patch('app.services.tools.availability_handler.ReservationTools', return_value=mock_reservation_tools))
            stack.enter_context(patch('app.services.tools.booking_handler.ReservationTools', return_value=mock_reservation_tools))

            # CRITICAL: Also mock AppointmentTools used by the LangGraph orchestrator
            # The orchestrator imports: from ..tools.appointment_tools import AppointmentTools
            mock_appointment_tools = MagicMock()
            mock_appointment_tools.check_availability = AsyncMock(return_value={
                'available': True,
                'slots': [
                    {'datetime': '2025-11-27T09:00:00', 'doctor_name': 'Dr. Smith', 'doctor_id': 'doc-1'},
                    {'datetime': '2025-11-27T10:00:00', 'doctor_name': 'Dr. Shtern', 'doctor_id': 'doc-2'},
                    {'datetime': '2025-11-27T14:00:00', 'doctor_name': 'Dr. Smith', 'doctor_id': 'doc-1'},
                ],
                'message': 'Found 3 available slots for tomorrow.'
            })
            mock_appointment_tools.book_appointment = AsyncMock(return_value={
                'success': True,
                'appointment_id': 'appt-123',
                'confirmation': 'Appointment booked successfully for 9:00 AM with Dr. Smith'
            })
            mock_appointment_tools.cancel_appointment = AsyncMock(return_value={
                'success': True,
                'message': 'Appointment cancelled successfully'
            })
            # Patch AppointmentTools class in the orchestrator template
            mock_appointment_tools_class = MagicMock(return_value=mock_appointment_tools)
            stack.enter_context(patch('app.services.orchestrator.templates.healthcare_template.AppointmentTools', mock_appointment_tools_class))
            stack.enter_context(patch('app.services.orchestrator.tools.appointment_tools.AppointmentTools', mock_appointment_tools_class))

        else:
            print("  - ⚠️ Using REAL Supabase Connection and Tools")
            # When using real data, we DON'T mock the tools or DB services.
            # However, we still need to ensure the environment is set up correctly.

        # --- LLM FACTORY SETUP (Shared) ---
        from app.services.llm.adapters.openai_adapter import OpenAIAdapter
        from app.services.llm.base_adapter import ModelCapability, LLMProvider
        
        real_adapter = OpenAIAdapter(ModelCapability(
            provider=LLMProvider.OPENAI,
            model_name="gpt-4o",
            api_key=os.environ["OPENAI_API_KEY"],
            display_name="GPT-4o",
            input_price_per_1m=5.0,
            output_price_per_1m=15.0,
            max_input_tokens=128000,
            max_output_tokens=4096,
            supports_streaming=True,
            supports_tool_calling=True,
            tool_calling_success_rate=0.95,
            supports_parallel_tools=True,
            supports_json_mode=True,
            supports_structured_output=True,
            supports_thinking_mode=False,
            api_endpoint="https://api.openai.com/v1",
            requires_api_key_env_var="OPENAI_API_KEY",
            base_url_override=None
        ))

        # Wrap generate_with_tools to capture tool calls
        captured_tool_calls = []
        captured_tool_outputs = []

        # --- CAPTURE TOOL CALLS ---
        original_generate_with_tools = real_adapter.generate_with_tools
        
        # We also need to capture tool OUTPUTS from the executor
        # Since ToolExecutor is initialized inside Processor, we need to patch the class method
        # or the instance on the processor.
        # The processor is initialized below. We can patch processor.tool_executor.execute
        
        async def capturing_generate(*args, **kwargs):
            # OpenAIAdapter returns LLMResponse
            llm_response = await original_generate_with_tools(*args, **kwargs)
            
            # Capture tools
            if llm_response.tool_calls:
                captured_tool_calls.extend([t.model_dump() for t in llm_response.tool_calls])
            
            # Processor expects LLMResponse object
            return llm_response

        mock_factory = MagicMock()
        mock_factory.generate_with_tools = capturing_generate

        async def capturing_generate_simple(*args, **kwargs):
            return await real_adapter.generate(*args, **kwargs)
        mock_factory.generate = capturing_generate_simple

        # Patch get_llm_factory for code that uses the function
        stack.enter_context(patch('app.services.llm.llm_factory.get_llm_factory', new=AsyncMock(return_value=mock_factory)))

        # CRITICAL: Also patch LLMFactory class for code that instantiates it directly
        # The LangGraph orchestrator uses: from app.services.llm.llm_factory import LLMFactory
        # Since it's a lazy import, patching the source module is sufficient
        mock_factory_class = MagicMock(return_value=mock_factory)
        stack.enter_context(patch('app.services.llm.llm_factory.LLMFactory', mock_factory_class))

        # Initialize Processor (using Pipeline architecture)
        processor = PipelineMessageProcessor()

        # Note: PipelineMessageProcessor uses discrete steps, no need to mock internal methods like
        # _upsert_patient_from_whatsapp which was part of the legacy processor

        # --- REAL ROUTER SETUP ---
        # Replace router_service with mocked language service for consistent behavior
        mock_lang_service = MagicMock()
        mock_lang_service.match_service_alias.return_value = None
        mock_lang_service.is_affirmative.return_value = False
        mock_lang_service.is_negative.return_value = False

        # PipelineMessageProcessor uses session_service (not session_manager) for RouterService
        real_router = RouterService(language_service=mock_lang_service, session_service=processor.session_service)
        processor.router_service = real_router

        # Ensure message_logger methods are async mocks
        processor.message_logger.log_message_with_metrics = AsyncMock()

        # Patch ToolExecutor.execute to capture outputs
        original_tool_execute = processor.tool_executor.execute

        async def capturing_tool_execute(
            tool_call_id,
            tool_name,
            tool_args,
            context,
            constraints=None,
            current_state="idle",
            tool_schemas=None,
            prior_tool_results=None
        ):
            try:
                # Call original with ALL parameters - it returns a tuple (result, updated_prior_results)
                result, updated_prior_results = await original_tool_execute(
                    tool_call_id,
                    tool_name,
                    tool_args,
                    context,
                    constraints,
                    current_state,
                    tool_schemas,
                    prior_tool_results
                )

                # Ensure output is a string for OpenAI
                output_content = result.get("content")
                if output_content is None:
                    # If result is a dict (raw tool output), dump it
                    import json
                    output_content = json.dumps(result, default=str)
                elif not isinstance(output_content, str):
                    output_content = str(output_content)
            except Exception as e:
                # Capture the error as output so history remains valid
                output_content = f"Error executing tool {tool_name}: {str(e)}"
                captured_tool_outputs.append({
                    "id": tool_call_id,
                    "name": tool_name,
                    "args": tool_args,
                    "output": output_content
                })
                raise e

            print(f"DEBUG: Tool {tool_name} output: {output_content} (type: {type(output_content)})")
            captured_tool_outputs.append({
                "id": tool_call_id,
                "name": tool_name,
                "args": tool_args,
                "output": output_content
            })
            # Return the tuple as expected by the caller
            return result, updated_prior_results

        processor.tool_executor.execute = capturing_tool_execute

        print("🚀 Starting Evaluations...\n")
        
        results = []
        errors = []

        for scenario in scenarios:
            print(f"Running Scenario: {scenario['name']}")
            
            # Reset constraints for new scenario to prevent state leakage
            if not args.real_data:
                mock_cm_instance.constraints = ConversationConstraints()
                mock_cm_instance.constraints.excluded_doctors = set()
                mock_cm_instance.constraints.excluded_services = set()
            
            # Clear captured tool calls for the new scenario
            captured_tool_calls.clear()
            captured_tool_outputs.clear()

            # Clear processor cache to prevent context leakage (e.g. doctor IDs)
            if hasattr(processor, '_clinic_profile_cache'):
                processor._clinic_profile_cache.clear()
            if hasattr(processor, '_known_clinic_ids'):
                processor._known_clinic_ids.clear()
            
            conversation_history = []
            last_agent_response = None
            
            try:
                # Handle multiturn scenario format (turns) vs standard format (messages)
                if 'turns' in scenario:
                    # MULTITURN SCENARIO PROCESSING
                    # Turns describe the expected tool chain across agent's internal processing.
                    # Only turns with user messages trigger new agent invocations.
                    # Continuation turns (no messages) describe expected internal tool calls.

                    # Collect all expected tools from all turns for validation
                    all_expected_tools = []
                    all_criteria = []
                    last_expected_behavior = ''

                    # Extract only actual user messages (not continuation turns)
                    user_turns = []
                    for turn in scenario['turns']:
                        turn_messages = turn.get('messages', [])
                        expected_tools = turn.get('expected_tools', [])

                        # Collect expected tools from all turns
                        for tool in expected_tools:
                            tool_name = tool.get('name', tool) if isinstance(tool, dict) else tool
                            all_expected_tools.append(tool_name)

                        # Collect criteria and behavior from turns
                        if turn.get('criteria'):
                            all_criteria.extend(turn.get('criteria', []))
                        if turn.get('expected_behavior'):
                            last_expected_behavior = turn.get('expected_behavior', '')

                        # Only process turns with actual user messages
                        user_messages = [m for m in turn_messages if m.get('role') == 'user']
                        if user_messages:
                            user_turns.append({
                                'turn_id': turn.get('turn_id', len(user_turns) + 1),
                                'messages': turn_messages,
                                'expected_tools': expected_tools,
                                'expected_behavior': turn.get('expected_behavior', ''),
                                'criteria': turn.get('criteria', [])
                            })

                    print(f"  Expected tool chain: {all_expected_tools}")

                    # Process each user turn
                    for turn_idx, turn in enumerate(user_turns):
                        for msg in turn['messages']:
                            if msg['role'] == 'user':
                                print(f"  Turn {turn['turn_id']}: User says '{msg['content']}'")

                                # Update mock memory with current history (only if mocked)
                                if not args.real_data:
                                    mock_mm.get_conversation_history = AsyncMock(return_value=conversation_history)
                                    current_context = mock_hydrator.hydrate.return_value
                                    current_context['history'] = list(conversation_history)
                                    mock_hydrator.hydrate = AsyncMock(return_value=current_context)

                                req = MessageRequest(
                                    from_phone='+15551112222',
                                    to_phone='+15550000000',
                                    body=msg['content'],
                                    message_sid=f"msg-{datetime.now().timestamp()}",
                                    clinic_id=args.clinic_id,
                                    clinic_name='Test Dental Clinic'
                                )

                                # Process Message - agent will execute its full tool chain internally
                                response = await processor.process_message(req)
                                agent_response_text = response.message
                                last_agent_response = agent_response_text

                                # Phase 6: Capture internal tool tracking from response metadata
                                response_meta = getattr(response, 'metadata', {}) or {}
                                internal_tools = response_meta.get('internal_tools_called', [])
                                internal_tools_failed = response_meta.get('internal_tools_failed', [])
                                validation_errors = response_meta.get('executor_validation_errors', [])
                                hallucination_blocked = response_meta.get('hallucination_blocked', False)

                                print(f"  Agent says: {agent_response_text}")
                                if captured_tool_calls:
                                    actual_tools = [t['name'] for t in captured_tool_calls]
                                    print(f"  LLM Tools called: {actual_tools}")
                                if internal_tools:
                                    print(f"  Internal tools executed: {internal_tools}")
                                if internal_tools_failed:
                                    print(f"  ⚠️ Internal tools failed: {internal_tools_failed}")
                                if hallucination_blocked:
                                    print(f"  ✓ Hallucination blocked by validator")

                                # Update history
                                conversation_history.append({"role": "user", "content": msg['content']})

                                # Append tool outputs as SYSTEM messages
                                if captured_tool_outputs:
                                    for tool_out in captured_tool_outputs:
                                        output_text = f"Tool '{tool_out['name']}' output: {tool_out['output']}"
                                        conversation_history.append({
                                            "role": "system",
                                            "content": output_text
                                        })

                                conversation_history.append({"role": "assistant", "content": agent_response_text})

                            elif msg['role'] == 'assistant':
                                if conversation_history and conversation_history[-1]['role'] == 'assistant':
                                    print(f"  (Overriding Agent response with: '{msg['content']}')")
                                    conversation_history[-1] = msg
                                else:
                                    conversation_history.append(msg)

                    # After all user turns processed, evaluate the complete scenario
                    # Use scenario-level or last turn's expected_behavior/criteria
                    expected_behavior = scenario.get('expected_behavior', last_expected_behavior)
                    criteria = scenario.get('criteria', all_criteria if all_criteria else [])

                    # Get the last user message for context
                    last_user_msg = ''
                    for msg in reversed(conversation_history):
                        if msg.get('role') == 'user':
                            last_user_msg = msg.get('content', '')
                            break

                    # Validate tool chain - check if expected tools were called in order
                    actual_tool_names = [t['name'] for t in captured_tool_calls]
                    tool_chain_valid = True
                    expected_idx = 0
                    for actual_tool in actual_tool_names:
                        if expected_idx < len(all_expected_tools) and actual_tool == all_expected_tools[expected_idx]:
                            expected_idx += 1
                    tool_chain_valid = expected_idx == len(all_expected_tools)

                    if not tool_chain_valid:
                        print(f"  ⚠️ Tool chain mismatch: expected {all_expected_tools}, got {actual_tool_names}")

                    # Judge Response - Phase 6: Include internal tool tracking
                    eval_result = judge.evaluate_response(
                        user_input=last_user_msg,
                        agent_response=last_agent_response or '',
                        expected_behavior=expected_behavior,
                        criteria=criteria,
                        tool_calls=captured_tool_calls,
                        tool_outputs=captured_tool_outputs,
                        # Phase 6: Internal tool tracking
                        internal_tools_called=internal_tools,
                        internal_tools_failed=internal_tools_failed,
                        validation_errors=validation_errors,
                        hallucination_blocked=hallucination_blocked,
                        requires_availability_check=scenario.get('requires_availability_check', False),
                        requires_pricing_tool=scenario.get('requires_pricing_tool', False),
                    )

                    print(f"  Score: {eval_result['score']}/10")
                    print(f"  Pass: {'✅' if eval_result['pass'] else '❌'}")
                    print(f"  Reasoning: {eval_result['reasoning']}\n")

                    # Phase 6: Include internal tool tracking in results
                    results.append({
                        "scenario": scenario['name'],
                        "result": eval_result,
                        "transcript": conversation_history,
                        "expected_tool_chain": all_expected_tools,
                        "actual_tool_chain": actual_tool_names,
                        "tool_chain_valid": tool_chain_valid,
                        # Phase 6: Internal tool tracking
                        "internal_tools_called": internal_tools,
                        "internal_tools_failed": internal_tools_failed,
                        "validation_errors": validation_errors,
                        "hallucination_blocked": hallucination_blocked,
                    })

                else:
                    # STANDARD SINGLE-TURN SCENARIO PROCESSING
                    scenario_messages = scenario.get('messages', [])

                    # Iterate through messages
                    for i, msg in enumerate(scenario_messages):
                        if msg['role'] == 'user':
                            print(f"  Turn {i+1}: User says '{msg['content']}'")

                            # Update mock memory with current history (only if mocked)
                            if not args.real_data:
                                mock_mm.get_conversation_history = AsyncMock(return_value=conversation_history)
                                current_context = mock_hydrator.hydrate.return_value
                                current_context['history'] = list(conversation_history)
                                mock_hydrator.hydrate = AsyncMock(return_value=current_context)

                            req = MessageRequest(
                                from_phone='+15551112222',
                                to_phone='+15550000000',
                                body=msg['content'],
                                message_sid=f"msg-{datetime.now().timestamp()}",
                                clinic_id=args.clinic_id,
                                clinic_name='Test Dental Clinic'
                            )

                            # Process Message
                            response = await processor.process_message(req)
                            agent_response_text = response.message
                            last_agent_response = agent_response_text

                            # Phase 6: Capture internal tool tracking
                            response_meta = getattr(response, 'metadata', {}) or {}
                            internal_tools = response_meta.get('internal_tools_called', [])
                            internal_tools_failed = response_meta.get('internal_tools_failed', [])
                            validation_errors = response_meta.get('executor_validation_errors', [])
                            hallucination_blocked = response_meta.get('hallucination_blocked', False)

                            print(f"  Agent says: {agent_response_text}")
                            if captured_tool_calls:
                                print(f"  LLM Tools called: {[t['name'] for t in captured_tool_calls]}")
                            if internal_tools:
                                print(f"  Internal tools executed: {internal_tools}")
                            if internal_tools_failed:
                                print(f"  ⚠️ Internal tools failed: {internal_tools_failed}")
                            if hallucination_blocked:
                                print(f"  ✓ Hallucination blocked")

                            # Update history
                            conversation_history.append({"role": "user", "content": msg['content']})

                            # Append tool outputs as SYSTEM messages
                            if captured_tool_outputs:
                                for tool_out in captured_tool_outputs:
                                    output_text = f"Tool '{tool_out['name']}' output: {tool_out['output']}"
                                    conversation_history.append({
                                        "role": "system",
                                        "content": output_text
                                    })

                            conversation_history.append({"role": "assistant", "content": agent_response_text})

                            # Check if this is the last message in the scenario
                            if i == len(scenario_messages) - 1:
                                expected_behavior = scenario.get('expected_behavior', '')
                                criteria = scenario.get('criteria', [])

                                # Judge Response - Phase 6: Include internal tool tracking
                                eval_result = judge.evaluate_response(
                                    user_input=msg['content'],
                                    agent_response=agent_response_text,
                                    expected_behavior=expected_behavior,
                                    criteria=criteria,
                                    tool_calls=captured_tool_calls,
                                    tool_outputs=captured_tool_outputs,
                                    # Phase 6: Internal tool tracking
                                    internal_tools_called=internal_tools,
                                    internal_tools_failed=internal_tools_failed,
                                    validation_errors=validation_errors,
                                    hallucination_blocked=hallucination_blocked,
                                    requires_availability_check=scenario.get('requires_availability_check', False),
                                    requires_pricing_tool=scenario.get('requires_pricing_tool', False),
                                )

                                print(f"  Score: {eval_result['score']}/10")
                                print(f"  Pass: {'✅' if eval_result['pass'] else '❌'}")
                                print(f"  Reasoning: {eval_result['reasoning']}\n")

                                # Phase 6: Include internal tool tracking
                                results.append({
                                    "scenario": scenario['name'],
                                    "result": eval_result,
                                    "transcript": conversation_history,
                                    "internal_tools_called": internal_tools,
                                    "internal_tools_failed": internal_tools_failed,
                                    "validation_errors": validation_errors,
                                    "hallucination_blocked": hallucination_blocked,
                                })

                        elif msg['role'] == 'assistant':
                            # If the scenario defines an assistant message, it overrides the actual agent response
                            if conversation_history and conversation_history[-1]['role'] == 'assistant':
                                print(f"  (Overriding Agent response with: '{msg['content']}')")
                                conversation_history[-1] = msg
                            else:
                                conversation_history.append(msg)

            except Exception as e:
                print(f"❌ Error running scenario '{scenario['name']}': {e}\n")
                import traceback
                traceback.print_exc()
                errors.append({
                    "scenario": scenario['name'],
                    "error": str(e)
                })

        # Summary
        print("--- Evaluation Summary ---")
        total_defined = len(scenarios)
        passed = sum(1 for r in results if r['result']['pass'])
        failed_logic = len(results) - passed
        failed_errors = len(errors)
        total_failed = failed_logic + failed_errors
        
        print(f"Total Scenarios: {total_defined}")
        print(f"Passed: {passed}")
        print(f"Failed (Logic): {failed_logic}")
        print(f"Failed (Errors): {failed_errors}")
        
        success_rate = (passed / total_defined * 100) if total_defined > 0 else 0
        print(f"Success Rate: {success_rate:.1f}%")

        # Save Results
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        # Use absolute path relative to script location
        script_dir = os.path.dirname(os.path.abspath(__file__))
        output_file = os.path.join(script_dir, f"results-{timestamp}.json")
        with open(output_file, "w") as f:
            json.dump({
                "timestamp": timestamp,
                "summary": {
                    "total": total_defined,
                    "passed": passed,
                    "failed": total_failed,
                    "success_rate": success_rate
                },
                "results": results,
                "errors": errors
            }, f, indent=2)
        print(f"Detailed results saved to {output_file}")

        # Exit Code
        if total_failed > 0:
            sys.exit(1)
        sys.exit(0)

if __name__ == "__main__":
    asyncio.run(run_evals())
