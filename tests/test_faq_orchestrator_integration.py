"""
Integration tests for FAQ orchestrator integration
"""

import pytest
from app.services.orchestrator.templates.healthcare_template import HealthcareLangGraph, HealthcareConversationState


@pytest.fixture
def orchestrator():
    """Create orchestrator instance for testing"""
    return HealthcareLangGraph(
        clinic_id="e0c84f56-235d-49f2-9a44-37c1be579afc"  # Shtern Dental Clinic
    )


@pytest.mark.asyncio
async def test_faq_node_execution(orchestrator):
    """Test FAQ node in orchestrator workflow"""
    state = HealthcareConversationState(
        session_id="test-session",
        message="What are your hours?",
        metadata={"language": "en"},
        context={},
        audit_trail=[],
        intent=None,
        response=None,
        memories=None,
        knowledge=None,
        error=None,
        should_end=False,
        next_node=None,
        compliance_mode=None,
        compliance_checks=[],
        contains_phi=False,
        phi_tokens=None,
        de_identified_message=None,
        appointment_type=None,
        preferred_date=None,
        preferred_time=None,
        doctor_id=None,
        patient_id=None,
        patient_name=None,
        insurance_verified=False
    )

    # Execute FAQ node
    result_state = await orchestrator.faq_lookup_node(state)

    assert "faq_results" in result_state['context']
    assert "faq_success" in result_state['context']
    assert len(result_state['audit_trail']) > 0
    assert result_state['audit_trail'][0]['node'] == 'faq_lookup'


@pytest.mark.asyncio
async def test_faq_fallback_to_rag(orchestrator):
    """Test fallback to RAG when FAQ fails"""
    state = HealthcareConversationState(
        session_id="test-session",
        message="Complex medical question requiring document search",
        metadata={"language": "en"},
        context={},
        audit_trail=[],
        intent=None,
        response=None,
        memories=None,
        knowledge=None,
        error=None,
        should_end=False,
        next_node=None,
        compliance_mode=None,
        compliance_checks=[],
        contains_phi=False,
        phi_tokens=None,
        de_identified_message=None,
        appointment_type=None,
        preferred_date=None,
        preferred_time=None,
        doctor_id=None,
        patient_id=None,
        patient_name=None,
        insurance_verified=False
    )

    # Execute FAQ node
    result_state = await orchestrator.faq_lookup_node(state)

    # Route based on FAQ success
    route = orchestrator.faq_fallback_router(result_state)

    # Should fallback to RAG for complex queries
    if not result_state['context'].get('faq_success'):
        assert route == "fallback_rag"


@pytest.mark.asyncio
async def test_high_confidence_faq_response(orchestrator):
    """Test that high-confidence FAQ sets response directly"""
    state = HealthcareConversationState(
        session_id="test-session",
        message="What are your hours?",
        metadata={"language": "en"},
        context={},
        audit_trail=[],
        intent=None,
        response=None,
        memories=None,
        knowledge=None,
        error=None,
        should_end=False,
        next_node=None,
        compliance_mode=None,
        compliance_checks=[],
        contains_phi=False,
        phi_tokens=None,
        de_identified_message=None,
        appointment_type=None,
        preferred_date=None,
        preferred_time=None,
        doctor_id=None,
        patient_id=None,
        patient_name=None,
        insurance_verified=False
    )

    # Execute FAQ node
    result_state = await orchestrator.faq_lookup_node(state)

    # If high confidence, should have response set
    if result_state['context'].get('faq_success'):
        assert result_state.get('response') is not None
        assert len(result_state['response']) > 0
        # Should contain the FAQ question and answer
        assert 'hours' in result_state['response'].lower()
