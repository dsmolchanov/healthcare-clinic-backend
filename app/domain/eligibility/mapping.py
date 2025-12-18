"""
Centralized eligibility mapping logic.

This module provides the canonical specialty-to-category mapping used by:
- Seeding scripts
- Onboarding auto-derivation
- Medical Director re-derive endpoint

IMPORTANT: This is a v1 heuristic layer. It routes availability queries,
not clinical competence. Do not treat as clinically authoritative.
"""

import logging
from typing import List, Dict, Any, Set, Tuple

logger = logging.getLogger(__name__)

# =============================================================================
# CANONICAL CATEGORIES
# =============================================================================
# These are the ONLY valid category tokens. Services must use one of these.
CANONICAL_CATEGORIES = {
    "preventive",
    "restorative",
    "diagnostic",
    "hygiene",
    "endodontic",
    "prosthetic",
    "cosmetic",
    "whitening",
    "surgery",
    "extraction",
    "implants",
    "periodontic",
    "orthodontic",
    "pediatric",
    # Additional categories found in Shtern Dental data
    "prosthesis",  # Alias for prosthetic
    "consultation",
    "operatory",   # General chair-side procedures
}

# =============================================================================
# RESTRICTED CATEGORIES (require specialist, no generalist fallback)
# =============================================================================
RESTRICTED_CATEGORIES = {
    "surgery",
    "implants",
    "endodontic",  # Complex root canals
}

# =============================================================================
# SPECIALTY NORMALIZATION
# =============================================================================
# Maps various free-text specialty strings to canonical keys
SPECIALTY_ALIASES = {
    # General Dentistry
    "general dentistry": "general",
    "general": "general",
    "gen": "general",
    "odontologia general": "general",
    "odontología general": "general",
    "dentista general": "general",

    # Oral Surgery
    "oral surgery": "oral_surgery",
    "oral and maxillofacial surgery": "oral_surgery",
    "cirugia oral": "oral_surgery",
    "cirugía oral": "oral_surgery",
    "cirugia maxilofacial": "oral_surgery",
    "cirugía maxilofacial": "oral_surgery",

    # Endodontics
    "endodontics": "endodontics",
    "endodoncia": "endodontics",
    "endo": "endodontics",  # Common abbreviation

    # Prosthodontics
    "prosthodontics": "prosthodontics",
    "prostodoncia": "prosthodontics",
    "implant-based prosthetic rehabilitation specialist": "prosthodontics",

    # Cosmetic/Aesthetic
    "cosmetic dentistry": "cosmetic",
    "dental aesthetics": "cosmetic",
    "estetica dental": "cosmetic",
    "estética dental": "cosmetic",
    "aesthetic physician; facial rejuvenation specialist": "cosmetic",

    # Periodontics
    "periodontics": "periodontics",
    "periodoncia": "periodontics",
    "periodontist & implantologist": "periodontics",

    # Pediatric
    "pediatric dentistry": "pediatric",
    "odontopediatria": "pediatric",
    "odontopediatría": "pediatric",

    # Orthodontics
    "orthodontics": "orthodontics",
    "ortodoncia": "orthodontics",

    # Numeric codes (data quality issue - map to general as safe default)
    "22": "general",
    "26": "general",
}

# =============================================================================
# SPECIALTY -> CATEGORY MAPPING
# =============================================================================
# Each normalized specialty maps to allowed category tokens
SPECIALTY_CATEGORY_MAP = {
    "general": ["preventive", "restorative", "diagnostic", "hygiene", "consultation", "operatory"],
    "oral_surgery": ["surgery", "extraction", "implants", "diagnostic", "consultation"],
    "endodontics": ["endodontic", "diagnostic", "consultation"],
    "prosthodontics": ["prosthetic", "prosthesis", "restorative", "cosmetic", "diagnostic", "consultation", "implants"],
    "cosmetic": ["cosmetic", "whitening", "restorative", "consultation"],
    "periodontics": ["periodontic", "hygiene", "diagnostic", "consultation", "surgery", "implants"],
    "pediatric": ["pediatric", "preventive", "diagnostic", "consultation"],
    "orthodontics": ["orthodontic", "diagnostic", "consultation"],
}

# Categories that generalists can always do (if not restricted)
DEFAULT_CATEGORIES = ["preventive", "restorative", "diagnostic", "hygiene", "consultation", "operatory"]

# =============================================================================
# PRIMARY CATEGORY MAPPING (for is_primary flag)
# =============================================================================
# Each specialty's "core" categories where they should be marked as primary
SPECIALTY_PRIMARY_CATEGORIES = {
    "endodontics": ["endodontic"],
    "prosthodontics": ["prosthetic", "prosthesis"],
    "oral_surgery": ["surgery", "extraction", "implants"],
    "periodontics": ["periodontic", "surgery", "implants"],
    "orthodontics": ["orthodontic"],
    "pediatric": ["pediatric"],
    "cosmetic": ["cosmetic", "whitening"],
}


def normalize_specialty(raw: str) -> str:
    """
    Normalize a free-text specialty string to a canonical key.

    Args:
        raw: Free-text specialty string (e.g., "General Dentistry", "Oral Surgery")

    Returns:
        Canonical specialty key (e.g., "general", "oral_surgery")
        Falls back to "general" if not recognized.
    """
    if not raw:
        return "general"

    normalized = raw.strip().lower()

    if normalized in SPECIALTY_ALIASES:
        return SPECIALTY_ALIASES[normalized]

    # Log unknown specialties for data quality tracking
    logger.warning(f"Unknown specialty '{raw}' - defaulting to 'general'")
    return "general"


def normalize_category(raw: str) -> str:
    """
    Normalize a free-text category string to a canonical token.

    Args:
        raw: Free-text category string

    Returns:
        Canonical category token, or empty string if not recognized
    """
    if not raw:
        return ""

    normalized = raw.strip().lower()

    # Direct match
    if normalized in CANONICAL_CATEGORIES:
        return normalized

    # Partial match (e.g., "preventive care" -> "preventive")
    for cat in CANONICAL_CATEGORIES:
        if cat in normalized or normalized in cat:
            return cat

    logger.warning(f"Unknown category '{raw}' - service may need manual mapping")
    return ""


def get_categories_for_specialty(specialty: str) -> List[str]:
    """
    Get allowed categories for a specialty.

    Args:
        specialty: Raw specialty string (will be normalized)

    Returns:
        List of canonical category tokens this specialty can perform
    """
    normalized = normalize_specialty(specialty)
    return SPECIALTY_CATEGORY_MAP.get(normalized, DEFAULT_CATEGORIES)


def get_specialties_for_doctor(
    doctor_id: str,
    doctor_specialties_rows: List[Dict],
    fallback_specialization: str = None
) -> List[str]:
    """
    Get all specialties for a doctor from doctor_specialties table.
    Falls back to doctors.specialization field if no rows exist.

    Args:
        doctor_id: UUID of doctor
        doctor_specialties_rows: Rows from healthcare.doctor_specialties table
        fallback_specialization: Value from doctors.specialization field

    Returns:
        List of normalized specialty keys
    """
    # Filter to this doctor's active, approved specialties
    doctor_rows = [
        row for row in doctor_specialties_rows
        if row.get('doctor_id') == doctor_id
        and row.get('is_active', True)
        and row.get('approval_status', 'approved') == 'approved'
    ]

    if doctor_rows:
        # Use structured data
        return list(set(
            normalize_specialty(row.get('specialty_code', ''))
            for row in doctor_rows
        ))

    # Fallback to free-text field
    if fallback_specialization:
        return [normalize_specialty(fallback_specialization)]

    return ["general"]


def is_restricted_category(category: str) -> bool:
    """Check if a category requires specialist (no generalist fallback)."""
    return normalize_category(category) in RESTRICTED_CATEGORIES


def derive_mappings(
    doctors: List[Dict],
    services: List[Dict],
    doctor_specialties: List[Dict] = None
) -> Tuple[List[Dict], Dict[str, Any]]:
    """
    Derive doctor-service mappings based on specialties.

    Args:
        doctors: List of doctor dicts with 'id', 'specialization'
        services: List of service dicts with 'id', 'name', 'category'
        doctor_specialties: Optional list from healthcare.doctor_specialties table

    Returns:
        Tuple of (mappings_list, stats_dict)
        - mappings_list: List of dicts ready for doctor_services upsert
        - stats_dict: Statistics about the derivation for logging
    """
    doctor_specialties = doctor_specialties or []
    mappings = []
    stats = {
        "total_doctors": len(doctors),
        "total_services": len(services),
        "mappings_created": 0,
        "unmapped_services": [],
        "unknown_specialties": set(),
        "unknown_categories": set(),
        "restricted_skipped": 0,
    }

    # Track which services get at least one doctor
    services_with_doctors = set()

    for doctor in doctors:
        doc_id = doctor['id']

        # Get specialties (prefer structured, fallback to text)
        specialties = get_specialties_for_doctor(
            doc_id,
            doctor_specialties,
            doctor.get('specialization')
        )

        # Union all allowed categories across specialties
        allowed_categories = set()
        primary_categories = set()

        for spec in specialties:
            allowed_categories.update(SPECIALTY_CATEGORY_MAP.get(spec, []))
            primary_categories.update(SPECIALTY_PRIMARY_CATEGORIES.get(spec, []))

        # Check each service
        for service in services:
            svc_id = service['id']
            svc_category = normalize_category(service.get('category', ''))

            if not svc_category:
                stats["unknown_categories"].add(service.get('category', '<empty>'))
                continue

            # Skip restricted categories for generalists
            if is_restricted_category(svc_category) and specialties == ["general"]:
                stats["restricted_skipped"] += 1
                continue

            # Check eligibility
            if svc_category in allowed_categories:
                mappings.append({
                    'doctor_id': doc_id,
                    'service_id': svc_id,
                    'status': 'derived',
                    'source': 'system',
                    'is_primary': svc_category in primary_categories,
                })
                services_with_doctors.add(svc_id)

    # Track unmapped services
    all_service_ids = {s['id'] for s in services}
    stats["unmapped_services"] = list(all_service_ids - services_with_doctors)
    stats["mappings_created"] = len(mappings)
    stats["unknown_specialties"] = list(stats["unknown_specialties"])
    stats["unknown_categories"] = list(stats["unknown_categories"])

    return mappings, stats
