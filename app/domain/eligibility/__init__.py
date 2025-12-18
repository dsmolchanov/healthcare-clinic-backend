"""
Eligibility domain module.

Provides centralized doctor-service eligibility mapping logic.
"""
from .mapping import (
    derive_mappings,
    normalize_specialty,
    normalize_category,
    get_categories_for_specialty,
    get_specialties_for_doctor,
    is_restricted_category,
    CANONICAL_CATEGORIES,
    RESTRICTED_CATEGORIES,
    SPECIALTY_ALIASES,
    SPECIALTY_CATEGORY_MAP,
)

__all__ = [
    "derive_mappings",
    "normalize_specialty",
    "normalize_category",
    "get_categories_for_specialty",
    "get_specialties_for_doctor",
    "is_restricted_category",
    "CANONICAL_CATEGORIES",
    "RESTRICTED_CATEGORIES",
    "SPECIALTY_ALIASES",
    "SPECIALTY_CATEGORY_MAP",
]
