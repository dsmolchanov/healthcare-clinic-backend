import asyncio
import os
import yaml
import json
import sys
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock, patch

# Ensure paths are correct
sys.path.append(os.getcwd())

from app.api.multilingual_message_processor import MultilingualMessageProcessor, MessageRequest
from app.services.router_service import RouterService
from evaluation.judge import LLMJudge

# Mock environment variables if not present
if "OPENAI_API_KEY" not in os.environ:
    print("⚠️ OPENAI_API_KEY not found in environment. Please set it to run evals.")
    sys.exit(1)

async def run_evals():
    # Load scenarios
    try:
        with open("apps/healthcare-backend/evaluation/scenarios.yaml", "r") as f:
            data = yaml.safe_load(f)
            scenarios = data["scenarios"]
    except FileNotFoundError:
        print("❌ Scenarios file not found at apps/healthcare-backend/evaluation/scenarios.yaml")
        sys.exit(1)

    # Initialize Judge
    judge = LLMJudge()

    # Setup Processor with Mocks
    print("🔧 Initializing Agent with Real Router & Mocks...")
    
    with patch('app.memory.conversation_memory.get_memory_manager') as mock_mm_get, \
         patch('app.api.async_message_logger.AsyncMessageLogger') as mock_logger_cls, \
         patch('app.services.response_analyzer.ResponseAnalyzer') as mock_analyzer_cls, \
         patch('app.services.escalation_handler.EscalationHandler') as mock_escalation_cls, \
         patch('app.services.followup_scheduler.FollowupScheduler'), \
         patch('app.api.multilingual_message_processor.SessionManager') as mock_sm_cls, \
         patch('app.api.multilingual_message_processor.ConstraintsManager'), \
         patch('app.api.multilingual_message_processor.ConstraintExtractor') as mock_ce_cls, \
         patch('app.api.multilingual_message_processor.ToolStateGate'), \
         patch('app.api.multilingual_message_processor.StateEchoFormatter'), \
         patch('app.services.cache_service.CacheService') as mock_cache_cls, \
         patch('app.api.multilingual_message_processor.get_llm_factory') as mock_get_factory, \
         patch('app.api.multilingual_message_processor.get_supabase_client') as mock_get_supabase:

        # --- Configure Mocks ---
        
        # Memory Manager
        mock_mm = MagicMock()
        mock_mm.get_or_create_session = AsyncMock(return_value={'id': 'test-session', 'metadata': {}})
        mock_mm.get_session_by_id = AsyncMock(return_value={'id': 'test-session', 'metadata': {}})
        mock_mm.store_message = AsyncMock()
        mock_mm.get_user_preferences = AsyncMock(return_value={})
        mock_mm.get_memory_context = AsyncMock(return_value=[])
        mock_mm_get.return_value = mock_mm

        # Session Manager
        mock_sm = MagicMock()
        mock_sm.check_and_manage_boundary = AsyncMock(return_value=('test-session', False, 'none'))
        mock_lock = MagicMock()
        mock_lock.acquire = MagicMock()
        mock_lock.acquire.return_value.__aenter__ = AsyncMock(return_value=None)
        mock_lock.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_sm.boundary_lock = mock_lock
        mock_sm_cls.return_value = mock_sm

        # Cache Service
        mock_cache = MagicMock()
        mock_cache.hydrate_context = AsyncMock(return_value={
            'clinic': {
                'id': 'test-clinic',
                'name': 'Test Dental Clinic',
                'phone': '+15550000000',
                'location': '123 Test St',
                'services': ['Dental Cleaning', 'Root Canal', 'Teeth Whitening'],
                'hours': {'weekdays': '9 AM - 5 PM'},
                'service_aliases': {'cleaning': 'Dental Cleaning'}
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
            'faqs': []
        })
        mock_cache_cls.return_value = mock_cache

        # Constraint Extractor
        mock_ce = MagicMock()
        mock_ce.detect_meta_reset.return_value = False
        mock_ce_cls.return_value = mock_ce

        # Escalation Handler
        mock_escalation = MagicMock()
        mock_escalation.check_if_should_escalate = AsyncMock(return_value={'should_escalate': False, 'reason': None})
        mock_escalation_cls.return_value = mock_escalation

        # Response Analyzer
        mock_analyzer = MagicMock()
        mock_analyzer.analyze_agent_response = AsyncMock(return_value={'turn_status': 'user_turn'})
        mock_analyzer_cls.return_value = mock_analyzer

        # LLM Factory - REAL LLM CALLS
        from app.services.llm.adapters.openai_adapter import OpenAIAdapter
        from app.services.llm.base_adapter import LLMCapability, LLMProvider
        
        real_adapter = OpenAIAdapter(LLMCapability(
            provider=LLMProvider.OPENAI,
            model_name="gpt-4o",
            api_key=os.environ["OPENAI_API_KEY"]
        ))

        mock_factory = MagicMock()
        mock_factory.generate_with_tools = real_adapter.generate 
        mock_get_factory.return_value = mock_factory

        # Initialize Processor
        processor = MultilingualMessageProcessor()
        
        # Mock internal async methods
        processor._upsert_patient_from_whatsapp = AsyncMock()
        processor._extract_and_update_constraints = AsyncMock(return_value=MagicMock(
            excluded_doctors=[], excluded_services=[], desired_service=None, time_window_start=None
        ))

        # --- REAL ROUTER SETUP ---
        mock_lang_service = MagicMock()
        mock_lang_service.match_service_alias.return_value = None
        mock_lang_service.is_affirmative.return_value = False
        mock_lang_service.is_negative.return_value = False
        
        real_router = RouterService(language_service=mock_lang_service, session_service=mock_sm)
        processor.router_service = real_router

        print("🚀 Starting Evaluations...\n")
        
        results = []
        errors = []

        for scenario in scenarios:
            print(f"Running Scenario: {scenario['name']}")
            
            conversation_history = []
            last_agent_response = None
            
            try:
                # Iterate through messages
                for i, msg in enumerate(scenario['messages']):
                    if msg['role'] == 'user':
                        print(f"  Turn {i+1}: User says '{msg['content']}'")
                        
                        # Update mock memory with current history
                        mock_mm.get_conversation_history.return_value = conversation_history
                        
                        req = MessageRequest(
                            from_phone='+15551112222',
                            to_phone='+15550000000',
                            body=msg['content'],
                            message_sid=f"msg-{datetime.now().timestamp()}",
                            clinic_id='test-clinic',
                            clinic_name='Test Dental Clinic'
                        )

                        # Process Message
                        response = await processor.process_message(req)
                        agent_response_text = response.message
                        last_agent_response = agent_response_text
                        
                        print(f"  Agent says: {agent_response_text}")
                        
                        # Update history
                        conversation_history.append({"role": "user", "content": msg['content']})
                        conversation_history.append({"role": "assistant", "content": agent_response_text})
                        
                        # Check if this is the last message in the scenario
                        if i == len(scenario['messages']) - 1:
                            # Judge Response
                            eval_result = judge.evaluate_response(
                                user_input=msg['content'],
                                agent_response=agent_response_text,
                                expected_behavior=scenario['expected_behavior'],
                                criteria=scenario['criteria'],
                                tool_calls=None # Placeholder for tool calls
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
                        # Pre-load assistant message into history
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
        output_file = f"apps/healthcare-backend/evaluation/results-{timestamp}.json"
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
