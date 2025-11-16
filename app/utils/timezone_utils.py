"""
Timezone Utilities for Healthcare Backend

P0 Fix #3 & #4: Proper timezone handling with ZoneInfo
Replaces naive datetime operations with timezone-aware datetime using ZoneInfo
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# Default timezone for the system
DEFAULT_TIMEZONE = "America/Los_Angeles"


def get_clinic_timezone(clinic_id: str, supabase_client) -> str:
    """
    Get the timezone for a specific clinic.

    Args:
        clinic_id: The clinic ID
        supabase_client: Supabase client instance

    Returns:
        Timezone string (e.g., 'America/Los_Angeles')
    """
    try:
        result = supabase_client.schema('healthcare').table('clinics').select('timezone').eq(
            'id', clinic_id
        ).single().execute()

        if result.data and result.data.get('timezone'):
            return result.data['timezone']
    except Exception as e:
        logger.warning(f"Failed to get timezone for clinic {clinic_id}: {e}")

    return DEFAULT_TIMEZONE


def now_in_timezone(timezone_str: str = DEFAULT_TIMEZONE) -> datetime:
    """
    Get current time in specified timezone.

    Args:
        timezone_str: Timezone string (e.g., 'America/Los_Angeles')

    Returns:
        Timezone-aware datetime
    """
    tz = ZoneInfo(timezone_str)
    return datetime.now(tz)


def utc_to_clinic_time(utc_dt: datetime, timezone_str: str) -> datetime:
    """
    Convert UTC datetime to clinic timezone.

    Args:
        utc_dt: UTC datetime (can be naive or aware)
        timezone_str: Target timezone string

    Returns:
        Datetime in clinic timezone
    """
    # Ensure UTC datetime is timezone-aware
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=ZoneInfo('UTC'))

    # Convert to clinic timezone
    clinic_tz = ZoneInfo(timezone_str)
    return utc_dt.astimezone(clinic_tz)


def clinic_time_to_utc(clinic_dt: datetime, timezone_str: str) -> datetime:
    """
    Convert clinic datetime to UTC.

    Args:
        clinic_dt: Datetime in clinic timezone (can be naive or aware)
        timezone_str: Source timezone string

    Returns:
        Datetime in UTC
    """
    # If naive, assume it's in the clinic timezone
    if clinic_dt.tzinfo is None:
        clinic_tz = ZoneInfo(timezone_str)
        clinic_dt = clinic_dt.replace(tzinfo=clinic_tz)

    # Convert to UTC
    return clinic_dt.astimezone(ZoneInfo('UTC'))


def format_for_display(dt: datetime, timezone_str: str, format_str: str = "%Y-%m-%d %I:%M %p %Z") -> str:
    """
    Format datetime for display in clinic timezone.

    Args:
        dt: Datetime to format (can be UTC or any timezone)
        timezone_str: Clinic timezone for display
        format_str: strftime format string

    Returns:
        Formatted datetime string
    """
    clinic_dt = utc_to_clinic_time(dt, timezone_str)
    return clinic_dt.strftime(format_str)


def get_hold_expiry_time(
    hold_duration_minutes: int = 15,
    timezone_str: str = DEFAULT_TIMEZONE
) -> datetime:
    """
    Calculate hold expiry time in clinic timezone.

    P0 Fix #3: Ensures expiry times are calculated in clinic timezone

    Args:
        hold_duration_minutes: How long the hold lasts
        timezone_str: Clinic timezone

    Returns:
        Expiry datetime in clinic timezone (timezone-aware)
    """
    now = now_in_timezone(timezone_str)
    return now + timedelta(minutes=hold_duration_minutes)


def get_hold_expiry_display(
    expire_at: datetime,
    timezone_str: str
) -> dict:
    """
    Get hold expiry information for display.

    P0 Fix #3: Returns expiry time in clinic timezone with human-readable format

    Args:
        expire_at: Expiry datetime (can be UTC or any timezone)
        timezone_str: Clinic timezone for display

    Returns:
        Dict with formatted time and remaining minutes
    """
    clinic_dt = utc_to_clinic_time(expire_at, timezone_str)
    now = now_in_timezone(timezone_str)

    remaining = clinic_dt - now
    remaining_minutes = max(0, int(remaining.total_seconds() / 60))

    return {
        'expire_at_display': clinic_dt.strftime("%I:%M %p %Z"),
        'expire_at_full': clinic_dt.strftime("%Y-%m-%d %I:%M %p %Z"),
        'remaining_minutes': remaining_minutes,
        'is_expired': remaining_minutes == 0
    }
