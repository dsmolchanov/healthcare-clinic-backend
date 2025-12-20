#!/usr/bin/env python3
"""
Utility script for refreshing Stern Clinic scheduling assets.

Legacy rule-engine data has been deprecated, so this script now only
manages visit patterns that remain compatible with the scheduling service.
"""

import os
from dotenv import load_dotenv
from supabase import create_client, Client
from supabase.client import ClientOptions

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
STERN_CLINIC_ID = "90fd1605-7f84-46b9-9a31-a32bbc73e81b"


def get_supabase_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY/ANON_KEY must be set")

    options = ClientOptions(schema="healthcare", auto_refresh_token=True, persist_session=False)
    return create_client(SUPABASE_URL, SUPABASE_KEY, options=options)


def create_stern_assets():
    """Create supporting scheduling assets for Stern Clinics."""
    supabase = get_supabase_client()

    print("Refreshing Stern Clinics scheduling assets...")
    print("Skipping legacy booking rule creation (rule engine deprecated).")

    pattern_data = {
        "clinic_id": STERN_CLINIC_ID,
        "name": "Dental Treatment Series",
        "description": "Initial exam followed by treatment and follow-up",
        "visits": [
            {
                "visit_number": 1,
                "name": "Initial Examination",
                "service_id": "dental_exam",
                "duration_minutes": 45,
                "description": "Comprehensive dental examination and X-rays",
            },
            {
                "visit_number": 2,
                "name": "Treatment",
                "service_id": "dental_treatment",
                "duration_minutes": 90,
                "offset_from_previous": {"min_days": 3, "max_days": 10, "preferred_days": 7},
                "description": "Primary treatment procedure",
            },
            {
                "visit_number": 3,
                "name": "Follow-up",
                "service_id": "dental_followup",
                "duration_minutes": 30,
                "offset_from_previous": {"min_days": 7, "max_days": 21, "preferred_days": 14},
                "description": "Post-treatment check-up",
            },
        ],
        "constraints": {
            "same_doctor": True,
            "same_location": True,
            "require_confirmation": True,
            "allow_partial_booking": False,
            "max_total_days": 30,
        },
        "active": True,
    }

    try:
        result = supabase.table("visit_patterns").insert(pattern_data).execute()
        if result.data:
            print("  ✓ Created visit pattern: Dental Treatment Series")
        else:
            print("  ℹ Visit pattern already exists or could not be created.")
    except Exception as exc:  # pylint: disable=broad-except
        print(f"  ⚠ Unable to create visit pattern: {exc}")

    print("✅ Stern Clinics asset refresh complete.")


if __name__ == "__main__":
    create_stern_assets()
