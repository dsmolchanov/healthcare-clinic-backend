"""
Test Multi-Agent System Implementation
Verifies that the migrations were applied and agents were created
"""
import asyncio
from app.db.supabase_client import get_supabase_client
from app.services.agent_service import get_agent_service, AgentConfig
from app.services.orchestrator_factory import get_orchestrator_factory


async def test_database_data():
    """Test that database migrations were successful"""
    print("=" * 80)
    print("TEST 1: Database Data Verification")
    print("=" * 80)

    supabase = get_supabase_client()

    # Check templates using RPC
    print("\n📋 Agent Templates:")
    templates = supabase.rpc('get_agent_templates').execute()
    print(f"Found {len(templates.data)} templates:")
    for t in templates.data:
        official = "✅" if t.get('is_official') else "  "
        print(f"  {official} {t['slug']}: {t['name']} ({t['industry']})")

    # Check organizations using RPC
    print("\n🏢 Organizations:")
    orgs = supabase.rpc('get_all_organizations').execute()
    print(f"Found {len(orgs.data)} organizations")

    # Find Shtern
    shtern_org = None
    for org in orgs.data:
        if 'shtern' in org.get('name', '').lower() or org.get('id') == '3e411ecb-3411-4add-91e2-8fa897310cb0':
            shtern_org = org
            print(f"  ✅ Found Shtern: {org['id']} - {org['name']}")
            break

    if not shtern_org:
        print("  ❌ Shtern organization not found!")
        return False

    # Check agents for Shtern using RPC
    print("\n🤖 Agents for Shtern:")
    agents = supabase.rpc('get_agents_for_organization', {'org_id': shtern_org['id']}).execute()

    print(f"Found {len(agents.data)} agents:")
    for agent in agents.data:
        print(f"\n  Agent: {agent['name']}")
        print(f"    ID: {agent['id']}")
        print(f"    Type: {agent['type']}")
        print(f"    Parent: {agent.get('parent_agent_id', 'None')}")

        if agent.get('quick_ack_config'):
            enabled = agent['quick_ack_config'].get('enabled', False)
            messages = agent['quick_ack_config'].get('messages', {})
            print(f"    Quick Ack: {'✅ Enabled' if enabled else '❌ Disabled'}")
            if messages:
                print(f"    Languages: {', '.join(messages.keys())}")

        if agent.get('langgraph_config'):
            orch_type = agent['langgraph_config'].get('orchestrator_type', 'N/A')
            base_template = agent['langgraph_config'].get('base_template', 'N/A')
            print(f"    Orchestrator: {base_template} ({orch_type})")

        if agent.get('delegation_config'):
            print(f"    Delegation Rules: {len(agent['delegation_config'])} rules")

    return len(agents.data) >= 2  # Should have at least orchestrator + specialist


async def test_agent_service():
    """Test AgentService loading"""
    print("\n" + "=" * 80)
    print("TEST 2: AgentService")
    print("=" * 80)

    agent_service = get_agent_service()

    # Load orchestrator agent
    print("\n📥 Loading orchestrator agent...")
    # Get actual organization ID from previous test
    supabase = get_supabase_client()
    orgs = supabase.rpc('get_all_organizations').execute()
    org_id = None
    for org in orgs.data:
        if 'shtern' in org.get('name', '').lower():
            org_id = org['id']
            break

    if not org_id:
        print("❌ Shtern organization not found")
        return False

    orchestrator = await agent_service.get_agent_for_organization(
        organization_id=org_id,
        agent_type="receptionist"
    )

    if not orchestrator:
        print("❌ Failed to load orchestrator agent")
        return False

    print(f"✅ Loaded: {orchestrator.name}")
    print(f"   Type: {orchestrator.type}")
    print(f"   Orchestrator Type: {orchestrator.orchestrator_type}")
    print(f"   Base Template: {orchestrator.base_template}")

    # Test quick ack messages
    print("\n💬 Quick Ack Messages:")
    for lang in ['ru', 'en', 'he']:
        msg = orchestrator.get_quick_ack_message(lang)
        if msg:
            print(f"   {lang}: {msg}")

    # Test delegation rules
    print("\n🔀 Delegation Rules:")
    if orchestrator.should_delegate('appointment'):
        rule = orchestrator.get_delegation_rule('appointment')
        print(f"   ✅ Appointment → {rule.get('delegate_to_type')}")

    # Load child agents
    print("\n👶 Child Agents:")
    children = await agent_service.get_child_agents(orchestrator.id)
    print(f"Found {len(children)} child agents:")
    for child in children:
        print(f"   - {child.name} (type={child.type})")

    return True


async def test_orchestrator_factory():
    """Test OrchestratorFactory"""
    print("\n" + "=" * 80)
    print("TEST 3: OrchestratorFactory")
    print("=" * 80)

    agent_service = get_agent_service()
    factory = get_orchestrator_factory()

    # Load agent
    print("\n📥 Loading agent...")
    # Get actual organization ID
    supabase = get_supabase_client()
    orgs = supabase.rpc('get_all_organizations').execute()
    org_id = None
    for org in orgs.data:
        if 'shtern' in org.get('name', '').lower():
            org_id = org['id']
            break

    if not org_id:
        print("❌ Shtern organization not found")
        return False

    agent_config = await agent_service.get_agent_for_organization(
        organization_id=org_id,
        agent_type="receptionist"
    )

    if not agent_config:
        print("❌ Failed to load agent")
        return False

    # Create orchestrator
    print(f"\n🏭 Creating orchestrator for: {agent_config.name}")
    print(f"   Template: {agent_config.base_template}")

    try:
        orchestrator = await factory.create_orchestrator(
            agent_config=agent_config,
            context={
                "organization_id": org_id,
                "clinic_id": org_id
            }
        )

        print(f"✅ Created orchestrator: {type(orchestrator).__name__}")
        print(f"   Has process method: {hasattr(orchestrator, 'process')}")
        print(f"   Has graph: {hasattr(orchestrator, 'graph')}")

        return True

    except Exception as e:
        print(f"❌ Failed to create orchestrator: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_language_detection():
    """Test language detection helper"""
    print("\n" + "=" * 80)
    print("TEST 4: Language Detection")
    print("=" * 80)

    from app.api.evolution_webhook import detect_language_simple

    test_cases = [
        ("Hello, I need an appointment", "en"),
        ("Здравствуйте, мне нужна запись", "ru"),
        ("שלום, אני צריך תור", "he"),
        ("Секунду, обрабатываю ваш запрос", "ru"),
    ]

    print("\n🌍 Testing language detection:")
    all_passed = True
    for text, expected in test_cases:
        detected = detect_language_simple(text)
        status = "✅" if detected == expected else "❌"
        print(f"   {status} '{text[:30]}...' → {detected} (expected: {expected})")
        if detected != expected:
            all_passed = False

    return all_passed


async def main():
    """Run all tests"""
    print("\n🧪 Multi-Agent System Test Suite")
    print("=" * 80)

    tests = [
        ("Database Data", test_database_data),
        ("AgentService", test_agent_service),
        ("OrchestratorFactory", test_orchestrator_factory),
        ("Language Detection", test_language_detection),
    ]

    results = {}
    for name, test_func in tests:
        try:
            result = await test_func()
            results[name] = result
        except Exception as e:
            print(f"\n❌ Test '{name}' failed with exception: {e}")
            import traceback
            traceback.print_exc()
            results[name] = False

    # Summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)

    passed = sum(1 for r in results.values() if r)
    total = len(results)

    for name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {name}")

    print(f"\n📊 Results: {passed}/{total} tests passed")

    if passed == total:
        print("🎉 All tests passed! Multi-agent system is operational.")
    else:
        print("⚠️  Some tests failed. Review output above.")


if __name__ == "__main__":
    asyncio.run(main())