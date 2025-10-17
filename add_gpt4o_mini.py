#!/usr/bin/env python3
"""Add GPT-4o-mini to llm_models table"""

import asyncio
from supabase import create_client
import os

async def main():
    url = os.getenv('SUPABASE_URL')
    key = os.getenv('SUPABASE_SERVICE_KEY')

    if not url or not key:
        print("❌ SUPABASE_URL or SUPABASE_SERVICE_KEY not set")
        return

    supabase = create_client(url, key)

    # Insert or update gpt-4o-mini
    model_data = {
        'provider': 'openai',
        'model_name': 'gpt-4o-mini',
        'display_name': 'GPT-4o Mini',
        'input_price_per_1m': 0.15,
        'output_price_per_1m': 0.60,
        'max_input_tokens': 128000,
        'max_output_tokens': 16384,
        'avg_output_speed': 120.0,
        'supports_streaming': True,
        'supports_tool_calling': True,
        'tool_calling_success_rate': 0.98,
        'supports_parallel_tools': True,
        'supports_json_mode': True,
        'supports_structured_output': True,
        'supports_thinking_mode': False,
        'requires_api_key_env_var': 'OPENAI_API_KEY',
        'is_active': True,
        'is_default': False,
        'is_production_ready': True
    }

    try:
        # Check if model already exists
        existing = supabase.table('llm_models').select('*').eq('model_name', 'gpt-4o-mini').execute()

        if existing.data:
            # Update existing model
            result = supabase.table('llm_models').update(model_data).eq('model_name', 'gpt-4o-mini').execute()
            print('✅ Successfully updated gpt-4o-mini in database')
        else:
            # Insert new model
            result = supabase.table('llm_models').insert(model_data).execute()
            print('✅ Successfully added gpt-4o-mini to database')

        print(f'Data: {result.data}')
    except Exception as e:
        print(f'❌ Error: {e}')
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    asyncio.run(main())
