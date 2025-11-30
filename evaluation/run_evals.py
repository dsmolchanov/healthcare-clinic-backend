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

from app.api.multilingual_message_processor import MultilingualMessageProcessor, MessageRequest
from app.services.router_service import RouterService
from evaluation.judge import LLMJudge

# Load environment variables
load_dotenv()

# Mock environment variables if not present
if "OPENAI_API_KEY" not in os.environ:
    print("⚠️ OPENAI_API_KEY not found in environment. Please set it to run evals.")
    sys.exit(1)

async def run_evals():
    # Parse arguments
    parser = argparse.ArgumentParser(description="Run evaluations for Healthcare Agent")
    parser.add_argument("scenario_file", nargs="?", default="apps/healthcare-backend/evaluation/scenarios.yaml", help="Path to scenarios YAML file")
    parser.add_argument("--real-data", action="store_true", help="Run against real Supabase data (disable DB/Tool mocks)")
    parser.add_argument("--clinic-id", default="test-clinic", help="Clinic ID to test against (default: test-clinic)")
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

    print(f"🔧 Initializing Agent (Real Data: {args.real_data})...")
    
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
        stack.enter_context(patch('app.api.multilingual_message_processor.ConstraintsManager', return_value=mock_cm_instance))
        # Mock Constraint Extractor
        # Mock ConstraintExtractor unless using real data
        if not args.real_data:
            mock_ce = MagicMock()
            mock_ce.detect_meta_reset.return_value = False
            stack.enter_context(patch('app.api.multilingual_message_processor.ConstraintExtractor', return_value=mock_ce))
        else:
            # For real data, we might want to mock it if it's not fully configured, 
            # but to avoid MagicMock serialization errors, we should let it run or use a better mock.
            # For now, let's NOT mock it and assume it works or fails gracefully.
            pass
        # Mock State Echo Formatter
        # State Echo Formatter
        mock_formatter = MagicMock()
        mock_formatter.format_response.side_effect = lambda response, *args, **kwargs: response
        stack.enter_context(patch('app.api.multilingual_message_processor.StateEchoFormatter', return_value=mock_formatter))
        # Mock Langfuse
        stack.enter_context(patch('app.api.multilingual_message_processor.Langfuse'))
        
        # Mock LLM Factory - We ALWAYS mock this to inject our capturing wrapper
        # This allows us to capture tool calls even when using real tools
        mock_get_factory = stack.enter_context(patch('app.api.multilingual_message_processor.get_llm_factory'))

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

            # Patch datetime in processor module to fix "Today" context
            mock_dt = MagicMock()
            # Set "now" to Sunday, Nov 23, 2025
            mock_dt.now.return_value = datetime(2025, 11, 23, 12, 0, 0)
            mock_dt.fromisoformat = datetime.fromisoformat
            stack.enter_context(patch('app.api.multilingual_message_processor.datetime', mock_dt))

            # Session Manager
            mock_sm = MagicMock()
            mock_sm.check_and_manage_boundary = AsyncMock(return_value=('test-session', False, 'none'))
            mock_lock = MagicMock()
            mock_lock.acquire = MagicMock()
            mock_lock.acquire.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_lock.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_sm.boundary_lock = mock_lock
            stack.enter_context(patch('app.api.multilingual_message_processor.SessionManager', return_value=mock_sm))

            # Tool State Gate (Patched in Executor)
            mock_gate = MagicMock()
            mock_gate.validate_tool_call.return_value = (True, None, None)
            stack.enter_context(patch('app.services.tools.executor.ToolStateGate', return_value=mock_gate))

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
                    'doctors': [
                        {'name': 'Dr. Smith', 'id': 'doc-1', 'specialization': 'General'},
                        {'name': 'Dr. Shtern', 'id': 'doc-2', 'specialization': 'Orthodontics'}
                    ]
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

            # Supabase Client
            stack.enter_context(patch('app.api.multilingual_message_processor.get_supabase_client'))
            
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
            mock_reservation_tools.check_availability_tool = AsyncMock(return_value={
                'success': True,
                'available_slots': [
                    {'date': '2025-11-23', 'start_time': '10:00', 'doctor_name': 'Dr. Smith'},
                    {'date': '2025-11-23', 'start_time': '14:00', 'doctor_name': 'Dr. Smith'},
                    {'date': '2025-11-24', 'start_time': '09:00', 'doctor_name': 'Dr. Shtern'},
                    {'date': '2025-11-24', 'start_time': '11:00', 'doctor_name': 'Dr. Shtern'}
                ],
                'recommendation': 'We have morning and afternoon slots available.'
            })
            # Dynamic book_appointment mock
            async def mock_book_appointment(date=None, start_time=None, doctor_id=None, **kwargs):
                # Handle missing args gracefully or use defaults for robustness
                if not date:
                    date = "2025-11-23"
                if not start_time:
                    start_time = "10:00"

                doctor_name = "Dr. Smith"
                if doctor_id == 'doc-2':
                    doctor_name = "Dr. Shtern"
                
                return {
                    'success': True,
                    'appointment_id': 'appt-123',
                    'appointment': {
                        'date': date,
                        'start_time': start_time,
                        'doctor_name': doctor_name
                    },
                    'confirmation_message': 'Appointment booked successfully'
                }

            mock_reservation_tools.book_appointment_tool = AsyncMock(side_effect=mock_book_appointment)
            
            # Cancel Appointment Mock
            mock_reservation_tools.cancel_appointment_tool = AsyncMock(return_value={
                'success': True,
                'message': 'Appointment cancelled successfully'
            })
            # Patch in both handlers
            stack.enter_context(patch('app.services.tools.availability_handler.ReservationTools', return_value=mock_reservation_tools))
            stack.enter_context(patch('app.services.tools.booking_handler.ReservationTools', return_value=mock_reservation_tools))
            
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
        
        stack.enter_context(patch('app.api.multilingual_message_processor.get_llm_factory', new=AsyncMock(return_value=mock_factory)))

        # Initialize Processor
        processor = MultilingualMessageProcessor()
        
        # Mock internal async methods that might cause side effects or aren't needed for e2e
        processor._upsert_patient_from_whatsapp = AsyncMock()
        
        # If mocking, we mock constraint extraction. If real, we let it run? 
        # We mocked ConstraintExtractor above in both cases to simplify.
        # But processor._extract_and_update_constraints calls it.
        # Let's mock the internal method to be safe and consistent with previous runs, 
        # unless we want to test constraint extraction too.
        # For now, let's keep it mocked to focus on tool execution.
        # Use real ConversationConstraints object instead of MagicMock to avoid JSON serialization errors
        from app.services.conversation_constraints import ConversationConstraints
        # processor._extract_and_update_constraints = AsyncMock(return_value=ConversationConstraints())

        # --- REAL ROUTER SETUP ---
        # Even with real data, we might want to mock the router's internal language service if it's complex,
        # but here we use the real RouterService class.
        # In the original script, we mocked language_service.
        mock_lang_service = MagicMock()
        mock_lang_service.match_service_alias.return_value = None
        mock_lang_service.is_affirmative.return_value = False
        mock_lang_service.is_negative.return_value = False
        
        # If real data, we might want real SessionManager?
        # If we didn't mock SessionManager above (in real mode), we should pass the real one?
        # processor.router_service is initialized inside processor.__init__.
        # If we want to inject our mocked language service but keep real session manager:
        if args.real_data:
            # We need to access the real session manager created inside processor
            # But processor creates it internally.
            # We can just replace the router service.
            # Note: processor.session_manager is the real one in real mode.
            real_router = RouterService(language_service=mock_lang_service, session_service=processor.session_manager)
            processor.router_service = real_router
        else:
            # In mock mode, mock_sm is the session manager
            real_router = RouterService(language_service=mock_lang_service, session_service=mock_sm)
            processor.router_service = real_router

        # Ensure message_logger methods are async mocks
        processor.message_logger.log_message_with_metrics = AsyncMock()

        # Patch ToolExecutor.execute to capture outputs
        original_tool_execute = processor.tool_executor.execute

        async def capturing_tool_execute(tool_call_id, tool_name, tool_args, context, constraints=None):
            try:
                result = await original_tool_execute(tool_call_id, tool_name, tool_args, context, constraints)
                
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
            return result

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
            
            conversation_history = []
            last_agent_response = None
            
            try:
                # Iterate through messages
                for i, msg in enumerate(scenario['messages']):
                    if msg['role'] == 'user':
                        print(f"  Turn {i+1}: User says '{msg['content']}'")
                        
                        # Update mock memory with current history (only if mocked)
                        if not args.real_data:
                            mock_mm.get_conversation_history = AsyncMock(return_value=conversation_history)
                            
                            # CRITICAL FIX: Also update the hydrator's return value to include history
                            # The processor uses hydrator, not memory_manager directly for context
                            current_context = mock_hydrator.hydrate.return_value
                            # We need to copy the list to avoid reference issues if the mock reuses the object
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

                        # Clear captured tool calls/outputs for this turn
                        captured_tool_calls.clear()
                        captured_tool_outputs.clear()

                        # Process Message
                        response = await processor.process_message(req)
                        agent_response_text = response.message
                        last_agent_response = agent_response_text
                        
                        print(f"  Agent says: {agent_response_text}")
                        if captured_tool_calls:
                            print(f"  Tools called: {[t['name'] for t in captured_tool_calls]}")
                        
                        # Update history
                        conversation_history.append({"role": "user", "content": msg['content']})
                        
                        # Append tool outputs as SYSTEM messages to preserve context without breaking validation
                        if captured_tool_outputs:
                            for tool_out in captured_tool_outputs:
                                # Format the output clearly
                                output_text = f"Tool '{tool_out['name']}' output: {tool_out['output']}"
                                conversation_history.append({
                                    "role": "system",
                                    "content": output_text
                                })
                        
                        conversation_history.append({"role": "assistant", "content": agent_response_text})
                        
                        # Check if this is the last message in the scenario
                        if i == len(scenario['messages']) - 1:
                            # Judge Response
                            eval_result = judge.evaluate_response(
                                user_input=msg['content'],
                                agent_response=agent_response_text,
                                expected_behavior=scenario['expected_behavior'],
                                criteria=scenario['criteria'],
                                tool_calls=captured_tool_calls,
                                tool_outputs=captured_tool_outputs
                            )

                            print(f"  Score: {eval_result['score']}/10")
                            print(f"  Pass: {'✅' if eval_result['pass'] else '❌'}")
                            print(f"  Reasoning: {eval_result['reasoning']}\n")

                            results.append({
                                "scenario": scenario['name'],
                                "result": eval_result,
                                "transcript": conversation_history
                            })

                    elif msg['role'] == 'assistant':
                        # If the scenario defines an assistant message, it overrides the actual agent response
                        # This allows forcing the conversation down a specific path
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
