"""
Shared helpers for building policy evaluation context across services.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Dict, Optional, Tuple
from uuid import UUID

from app.models.scheduling import HardConstraints


def within_working_hours(start: datetime, end: datetime, settings: Dict[str, Any]) -> bool:
    open_hour = settings.get("open_hour", 8)
    close_hour = settings.get("close_hour", 20)
    start_boundary = time(hour=open_hour)
    end_boundary = time(hour=close_hour)
    return start.time() >= start_boundary and end.time() <= end_boundary


def compute_slot_adjacency(
    doctor_id: UUID,
    slot_start: datetime,
    duration_minutes: int,
    doctor_appointments: Dict[UUID, Any]
) -> Tuple[Optional[float], Optional[float]]:
    appointments = doctor_appointments.get(doctor_id, [])
    prev_diff = None
    next_diff = None
    slot_end = slot_start + timedelta(minutes=duration_minutes)

    for apt in appointments:
        apt_start = datetime.fromisoformat(apt["start_time"])
        apt_end = datetime.fromisoformat(apt["end_time"])

        if apt_end <= slot_start:
            diff = (slot_start - apt_end).total_seconds() / 60
            if prev_diff is None or diff < prev_diff:
                prev_diff = diff
        elif apt_start >= slot_end:
            diff = (apt_start - slot_end).total_seconds() / 60
            if next_diff is None or diff < next_diff:
                next_diff = diff

    return prev_diff, next_diff


def is_least_busy(
    doctor_id: UUID,
    slot_time: datetime,
    doctor_appointments: Dict[UUID, Any]
) -> bool:
    slot_date = slot_time.date()
    min_count = None
    doctor_count = 0

    for doc_id, appointments in doctor_appointments.items():
        count = sum(
            1
            for apt in appointments
            if datetime.fromisoformat(apt["start_time"]).date() == slot_date
        )
        if doc_id == doctor_id:
            doctor_count = count
        min_count = count if min_count is None else min(min_count, count)

    return doctor_count <= (min_count if min_count is not None else doctor_count)


def is_emergency_request(patient_preferences: Optional[Dict[str, Any]]) -> bool:
    if not patient_preferences:
        return False
    if patient_preferences.get("is_emergency"):
        return True
    return patient_preferences.get("urgency") == "emergency"


def context_field_truthy(context: Dict[str, Any], field_path: str) -> bool:
    value: Any = context
    for part in field_path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return False
    return bool(value)


def build_slot_context(
    slot: Dict[str, Any],
    settings: Dict[str, Any],
    doctor_appointments: Dict[UUID, Any],
    patient_preferences: Optional[Dict[str, Any]],
    hard_constraints: Optional[HardConstraints],
    *,
    clinic_id: Optional[UUID] = None,
    patient_id: Optional[UUID] = None,
    tenant_id: Optional[str] = None
) -> Dict[str, Any]:
    start_time = slot["start_time"]
    end_time = slot["end_time"]
    duration = slot.get("duration_minutes") or int((end_time - start_time).total_seconds() / 60)

    minutes_since_prev, minutes_until_next = compute_slot_adjacency(
        slot["doctor_id"],
        start_time,
        duration,
        doctor_appointments
    )

    context = {
        "clinic": {
            "id": str(clinic_id) if clinic_id else None,
            "hours": {
                "open_hour": settings.get("open_hour", 8),
                "close_hour": settings.get("close_hour", 20)
            }
        },
        "tenant": {
            "id": tenant_id
        },
        "appointment": {
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "within_working_hours": within_working_hours(start_time, end_time, settings),
            "duration_minutes": duration
        },
        "slot": {
            "minutes_since_previous": minutes_since_prev,
            "minutes_until_next": minutes_until_next
        },
        "request": {
            "is_emergency": is_emergency_request(patient_preferences),
            "human_override": bool(patient_preferences.get("human_override")) if patient_preferences else False,
            "preferred_doctor_id": str(hard_constraints.doctor_id) if hard_constraints and hard_constraints.doctor_id else None
        },
        "doctor": {
            "id": str(slot["doctor_id"]),
            "is_least_busy": is_least_busy(slot["doctor_id"], start_time, doctor_appointments),
        },
        "patient": {
            "id": str(patient_id) if patient_id else None
        }
    }

    return context
