"""
Get Webhook Tokens and URLs for Evolution API Configuration

This script retrieves all WhatsApp integration webhook tokens and URLs
from healthcare.integrations table. Use this to update Evolution API
webhook configurations.

Usage:
    python get_webhook_tokens.py              # Show all tokens
    python get_webhook_tokens.py --clinic-id <id>  # Show specific clinic
    python get_webhook_tokens.py --export      # Export to JSON
"""

import os
import sys
import json
import argparse
from datetime import datetime

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

from app.db.supabase_client import get_supabase_client


def get_all_webhook_tokens():
    """Retrieve all WhatsApp integration webhook tokens"""
    supabase = get_supabase_client()

    result = supabase.schema('healthcare').table('integrations').select(
        'id, organization_id, clinic_id, webhook_token, webhook_url, phone_number, display_name, config, status, enabled'
    ).eq('type', 'whatsapp').execute()

    return result.data if result.data else []


def get_clinic_name(supabase, clinic_id):
    """Get clinic name by ID"""
    result = supabase.table('clinics').select('name').eq('id', clinic_id).limit(1).execute()
    return result.data[0]['name'] if result.data else 'Unknown'


def display_tokens(integrations, verbose=False):
    """Display webhook tokens in a user-friendly format"""
    if not integrations:
        print("❌ No WhatsApp integrations found")
        return

    print(f"\n{'='*80}")
    print(f"WhatsApp Webhook Tokens ({len(integrations)} found)")
    print(f"{'='*80}\n")

    supabase = get_supabase_client()

    for i, integration in enumerate(integrations, 1):
        clinic_id = integration.get('clinic_id')
        clinic_name = get_clinic_name(supabase, clinic_id)
        config = integration.get('config', {})
        instance_name = config.get('instance')

        print(f"[{i}] {clinic_name}")
        print(f"    Clinic ID: {clinic_id}")
        print(f"    Instance: {instance_name or 'N/A'}")
        print(f"    Status: {integration.get('status', 'N/A')} | Enabled: {integration.get('enabled', False)}")
        print(f"    Phone: {integration.get('phone_number', 'N/A')}")
        print(f"    ")
        print(f"    🔑 Webhook Token:")
        print(f"       {integration.get('webhook_token', 'NOT GENERATED')}")
        print(f"    ")
        print(f"    🔗 Webhook URL:")
        print(f"       {integration.get('webhook_url') or 'NOT GENERATED'}")

        if verbose:
            print(f"    ")
            print(f"    Evolution API Configuration:")
            print(f"       1. Go to Evolution API dashboard")
            print(f"       2. Select instance: {instance_name}")
            print(f"       3. Update webhook URL to:")
            print(f"          {integration.get('webhook_url')}")
            print(f"       4. Ensure X-Webhook-Signature header is enabled")

        print()


def export_to_json(integrations, filename='webhook_tokens.json'):
    """Export webhook tokens to JSON file"""
    supabase = get_supabase_client()

    export_data = {
        'exported_at': datetime.now().isoformat(),
        'count': len(integrations),
        'integrations': []
    }

    for integration in integrations:
        clinic_id = integration.get('clinic_id')
        clinic_name = get_clinic_name(supabase, clinic_id)
        config = integration.get('config', {})

        export_data['integrations'].append({
            'clinic_id': clinic_id,
            'clinic_name': clinic_name,
            'instance_name': config.get('instance'),
            'phone_number': integration.get('phone_number'),
            'webhook_token': integration.get('webhook_token'),
            'webhook_url': integration.get('webhook_url'),
            'status': integration.get('status'),
            'enabled': integration.get('enabled')
        })

    with open(filename, 'w') as f:
        json.dump(export_data, f, indent=2)

    print(f"✅ Exported {len(integrations)} integrations to {filename}")


def main():
    parser = argparse.ArgumentParser(description='Get WhatsApp webhook tokens')
    parser.add_argument('--clinic-id', help='Filter by clinic ID')
    parser.add_argument('--export', action='store_true', help='Export to JSON file')
    parser.add_argument('--verbose', '-v', action='store_true', help='Show detailed instructions')
    args = parser.parse_args()

    print("📥 Fetching WhatsApp integrations...")
    integrations = get_all_webhook_tokens()

    # Filter by clinic if specified
    if args.clinic_id:
        integrations = [i for i in integrations if i.get('clinic_id') == args.clinic_id]

    if args.export:
        export_to_json(integrations)
    else:
        display_tokens(integrations, verbose=args.verbose)

    print("\n" + "="*80)
    print("Next Steps:")
    print("1. Copy the webhook URLs above")
    print("2. Update Evolution API webhook configuration for each instance")
    print("3. Test new webhook endpoint by sending a WhatsApp message")
    print("4. Monitor logs to verify token-based routing is working")
    print("="*80 + "\n")


if __name__ == '__main__':
    main()
