#!/usr/bin/env python3
"""Check for existing appointment triggers"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, '/Users/dmitrymolchanov/Programs/Plaintalk/apps/healthcare-backend')

from app.db.supabase_client import get_supabase_client

def check_triggers():
    supabase = get_supabase_client()
    
    # Query for triggers on appointments table
    query = """
    SELECT 
        t.tgname AS trigger_name,
        p.proname AS function_name,
        t.tgenabled AS enabled,
        pg_get_triggerdef(t.oid) AS trigger_definition
    FROM pg_trigger t
    JOIN pg_class c ON t.tgrelid = c.oid
    JOIN pg_namespace n ON c.relnamespace = n.oid
    JOIN pg_proc p ON t.tgfoid = p.oid
    WHERE c.relname = 'appointments' 
    AND n.nspname = 'healthcare'
    AND NOT t.tgisinternal
    ORDER BY t.tgname;
    """
    
    result = supabase.rpc('exec_sql', {'sql': query}).execute()
    print("Existing triggers on healthcare.appointments:")
    print(result.data if result.data else "No triggers found")

if __name__ == '__main__':
    check_triggers()
