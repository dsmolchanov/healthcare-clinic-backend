#!/usr/bin/env python3
"""
Cleanup script to remove duplicate Google Calendar events
This script identifies and removes duplicate calendar events that were created due to the sync bug
"""
import os
import sys
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Set
from dotenv import load_dotenv
from collections import defaultdict

load_dotenv()
sys.path.insert(0, '/Users/dmitrymolchanov/Programs/Plaintalk/apps/healthcare-backend')

from app.db.supabase_client import get_supabase_client
from app.security.compliance_vault import ComplianceVault
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

async def cleanup_duplicate_events(clinic_id: str, dry_run: bool = True):
    """
    Remove duplicate calendar events for a clinic

    Args:
        clinic_id: Clinic UUID
        dry_run: If True, only report duplicates without deleting
    """
    supabase = get_supabase_client()
    vault = ComplianceVault()

    print(f"\n{'='*60}")
    print(f"Cleanup Duplicate Calendar Events for Clinic: {clinic_id}")
    print(f"Mode: {'DRY RUN (no changes)' if dry_run else 'LIVE (will delete duplicates)'}")
    print(f"{'='*60}\n")

    # Get calendar integration
    integration_result = supabase.rpc('get_calendar_integration_by_clinic', {
        'p_clinic_id': clinic_id,
        'p_provider': 'google'
    }).execute()

    if not integration_result.data or len(integration_result.data) == 0:
        print(f"❌ No Google Calendar integration found for clinic {clinic_id}")
        return

    integration = integration_result.data[0]
    print(f"✓ Found calendar integration for organization: {integration['organization_id']}")

    # Get credentials from vault
    credentials_data = await vault.retrieve_calendar_credentials(
        organization_id=integration['organization_id'],
        provider='google'
    )

    if not credentials_data:
        print(f"❌ Failed to retrieve credentials from vault")
        return

    print(f"✓ Retrieved calendar credentials")

    # Build Google Calendar service
    credentials = Credentials(
        token=credentials_data['access_token'],
        refresh_token=credentials_data.get('refresh_token'),
        token_uri='https://oauth2.googleapis.com/token',
        client_id=os.getenv('GOOGLE_CLIENT_ID'),
        client_secret=os.getenv('GOOGLE_CLIENT_SECRET')
    )

    service = build('calendar', 'v3', credentials=credentials)
    calendar_id = integration.get('calendar_id', 'primary')

    print(f"✓ Connected to Google Calendar: {calendar_id}")

    # Get all appointments from database
    appointments = supabase.from_('appointments').select(
        'id, appointment_date, start_time, patient_name, appointment_type, google_event_id'
    ).eq('clinic_id', clinic_id).gte(
        'appointment_date', (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    ).execute()

    print(f"\n✓ Found {len(appointments.data)} appointments in database\n")

    # Build a map of appointments by their key attributes
    appointment_map = {}
    for appt in appointments.data:
        key = f"{appt['appointment_date']}_{appt['start_time']}_{appt['patient_name']}"
        appointment_map[key] = appt

    # Fetch all events from Google Calendar (last 7 days)
    now = datetime.utcnow()
    events_result = service.events().list(
        calendarId=calendar_id,
        timeMin=(now - timedelta(days=7)).isoformat() + 'Z',
        timeMax=(now + timedelta(days=30)).isoformat() + 'Z',
        maxResults=2500,
        singleEvents=True,
        orderBy='startTime'
    ).execute()

    events = events_result.get('items', [])
    print(f"✓ Found {len(events)} events in Google Calendar\n")

    # Group events by appointment_id from extended properties
    events_by_appointment: Dict[str, List[Dict]] = defaultdict(list)
    orphan_events = []

    for event in events:
        ext_props = event.get('extendedProperties', {}).get('private', {})
        appointment_id = ext_props.get('appointment_id')
        source = ext_props.get('source')

        # Only process events created by our system
        if source == 'clinic_system' and appointment_id:
            events_by_appointment[appointment_id].append(event)
        elif not appointment_id and 'Appointment' in event.get('summary', ''):
            # Potential orphan event (created without appointment_id)
            orphan_events.append(event)

    # Find duplicates
    duplicates_found = 0
    events_to_delete = []

    print(f"{'─'*60}")
    print("DUPLICATE ANALYSIS")
    print(f"{'─'*60}\n")

    for appointment_id, event_list in events_by_appointment.items():
        if len(event_list) > 1:
            duplicates_found += 1
            print(f"📌 Appointment {appointment_id}: {len(event_list)} calendar events")

            # Sort by created time to keep the oldest one
            event_list.sort(key=lambda e: e.get('created', ''))

            # Keep the first event, mark others for deletion
            keep_event = event_list[0]
            delete_events = event_list[1:]

            print(f"   ✓ KEEP: {keep_event['summary']} (ID: {keep_event['id']})")
            print(f"           Created: {keep_event.get('created', 'unknown')}")

            for i, dup_event in enumerate(delete_events, 1):
                print(f"   ✗ DELETE #{i}: {dup_event['summary']} (ID: {dup_event['id']})")
                print(f"           Created: {dup_event.get('created', 'unknown')}")
                events_to_delete.append({
                    'event_id': dup_event['id'],
                    'appointment_id': appointment_id,
                    'summary': dup_event['summary']
                })

            print()

    # Report orphan events
    if orphan_events:
        print(f"\n⚠️  Found {len(orphan_events)} orphan events (no appointment_id)")
        for event in orphan_events[:10]:  # Show first 10
            print(f"   - {event['summary']} (ID: {event['id']})")
            print(f"     Start: {event['start'].get('dateTime', event['start'].get('date'))}")

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"Total appointments with duplicates: {duplicates_found}")
    print(f"Total duplicate events to delete: {len(events_to_delete)}")
    print(f"Orphan events (manual review needed): {len(orphan_events)}")
    print(f"{'='*60}\n")

    if not events_to_delete:
        print("✅ No duplicates found!")
        return

    # Delete duplicates if not dry run
    if not dry_run:
        print("\n🗑️  DELETING DUPLICATE EVENTS...\n")

        deleted_count = 0
        failed_count = 0

        for event_info in events_to_delete:
            try:
                service.events().delete(
                    calendarId=calendar_id,
                    eventId=event_info['event_id']
                ).execute()

                deleted_count += 1
                print(f"✓ Deleted: {event_info['summary']} (ID: {event_info['event_id']})")

                # Small delay to avoid rate limiting
                await asyncio.sleep(0.1)

            except Exception as e:
                failed_count += 1
                print(f"✗ Failed to delete {event_info['event_id']}: {e}")

        print(f"\n{'='*60}")
        print(f"✅ Deleted {deleted_count} duplicate events")
        if failed_count > 0:
            print(f"❌ Failed to delete {failed_count} events")
        print(f"{'='*60}\n")
    else:
        print("\n💡 This was a DRY RUN. To actually delete duplicates, run with --live flag\n")

async def main():
    import argparse

    parser = argparse.ArgumentParser(description='Cleanup duplicate Google Calendar events')
    parser.add_argument('clinic_id', help='Clinic UUID')
    parser.add_argument('--live', action='store_true', help='Actually delete duplicates (default is dry-run)')

    args = parser.parse_args()

    await cleanup_duplicate_events(args.clinic_id, dry_run=not args.live)

if __name__ == '__main__':
    asyncio.run(main())
