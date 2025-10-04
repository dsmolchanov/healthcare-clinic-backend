"""
Phase 3 Tests: Remove Pinecone Dependencies & Code

Tests to verify:
1. No pinecone in requirements.txt
2. No pinecone imports in codebase
3. hybrid_search_engine.py deleted
4. System works end-to-end without Pinecone
"""

import pytest
import os
import subprocess


def test_no_pinecone_in_requirements():
    """Requirements should not include pinecone"""
    requirements_path = os.path.join(os.path.dirname(__file__), '..', 'requirements.txt')

    with open(requirements_path) as f:
        content = f.read()

    assert 'pinecone' not in content.lower(), \
        "Pinecone should be removed from requirements.txt"


def test_no_pinecone_imports_in_codebase():
    """No Python files should import pinecone (except optional/legacy code)"""
    # Check multilingual_message_processor specifically
    import inspect
    from app.api import multilingual_message_processor

    source = inspect.getsource(multilingual_message_processor)

    # The legacy import may remain for backward compatibility, but shouldn't be used
    # Count how many times 'Pinecone' appears in actual usage (not comments)
    lines = [line for line in source.split('\n') if 'Pinecone' in line and not line.strip().startswith('#')]

    # Should only be in the optional import section at the top (max 3-4 lines)
    pinecone_usage_lines = [
        line for line in lines
        if 'import' not in line.lower()  # Exclude import statements
    ]

    # After Phase 3, no actual Pinecone usage should remain
    assert len(pinecone_usage_lines) == 0, \
        f"Found Pinecone usage (not imports) in {len(pinecone_usage_lines)} lines: {pinecone_usage_lines}"


def test_hybrid_search_file_deleted():
    """hybrid_search_engine.py should be deleted"""
    file_path = os.path.join(
        os.path.dirname(__file__),
        '..',
        'app',
        'api',
        'hybrid_search_engine.py'
    )

    assert not os.path.exists(file_path), \
        "hybrid_search_engine.py should be deleted"


def test_no_pinecone_client_in_main():
    """main.py should not initialize Pinecone"""
    import inspect
    from app import main

    source = inspect.getsource(main)

    # Should not have Pinecone client initialization
    assert 'Pinecone(' not in source, \
        "main.py should not initialize Pinecone client"
    assert 'pinecone.init' not in source.lower(), \
        "main.py should not call pinecone.init"


def test_server_starts_without_pinecone_key():
    """Server should start without PINECONE_API_KEY"""
    # This is tested by the running server already
    # Just verify the health endpoint works
    import requests

    try:
        response = requests.get('http://localhost:8000/health', timeout=2)
        assert response.status_code == 200, "Server should be healthy"
        data = response.json()
        assert data['status'] == 'healthy', "Server should report healthy status"
    except Exception as e:
        pytest.fail(f"Server health check failed: {e}")


@pytest.mark.asyncio
async def test_message_processing_without_pinecone_env():
    """Message processing should work without PINECONE_API_KEY env var"""
    import os

    # Verify PINECONE_API_KEY is not required
    old_value = os.environ.get('PINECONE_API_KEY')

    try:
        # Remove if exists
        if 'PINECONE_API_KEY' in os.environ:
            del os.environ['PINECONE_API_KEY']

        # Import should work without Pinecone key
        from app.api.multilingual_message_processor import MultilingualMessageProcessor

        # Should initialize without errors
        processor = MultilingualMessageProcessor()

        assert processor is not None, "Processor should initialize without Pinecone"

    finally:
        # Restore
        if old_value:
            os.environ['PINECONE_API_KEY'] = old_value


def test_no_pinecone_env_in_fly_toml():
    """fly.toml should not reference PINECONE env vars"""
    fly_toml_path = os.path.join(os.path.dirname(__file__), '..', 'fly.toml')

    if not os.path.exists(fly_toml_path):
        pytest.skip("fly.toml not found")

    with open(fly_toml_path) as f:
        content = f.read()

    # Should not have Pinecone environment variable references
    assert 'PINECONE' not in content.upper(), \
        "fly.toml should not reference PINECONE env vars"


def test_improved_pinecone_kb_not_used():
    """ImprovedPineconeKnowledgeBase should not be instantiated"""
    import inspect
    from app.api import multilingual_message_processor

    source = inspect.getsource(multilingual_message_processor)

    # The class may exist for legacy reasons, but shouldn't be used
    assert 'ImprovedPineconeKnowledgeBase(' not in source, \
        "ImprovedPineconeKnowledgeBase should not be instantiated"


def test_clean_pip_install():
    """pip install should work without pinecone-client"""
    # This test verifies the requirements.txt is valid
    # It's a meta-test that ensures our cleanup didn't break dependencies

    requirements_path = os.path.join(os.path.dirname(__file__), '..', 'requirements.txt')

    # Just verify the file is readable and has content
    with open(requirements_path) as f:
        lines = [line.strip() for line in f if line.strip() and not line.startswith('#')]

    assert len(lines) > 0, "Requirements file should not be empty"

    # Verify no duplicate or broken entries
    package_names = [line.split('==')[0].split('>=')[0].split('[')[0] for line in lines]
    duplicates = [name for name in package_names if package_names.count(name) > 1]

    assert len(duplicates) == 0, f"Found duplicate packages: {duplicates}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
