"""
i18n Helper Functions for JSONB-based translations

This module provides utilities for working with JSONB-based i18n translations
in the healthcare.services table and other multilingual entities.
"""

from typing import Dict, Any, Optional, List


def get_translation(
    entity: Dict[str, Any],
    field: str,
    language: str,
    fallback_languages: Optional[List[str]] = None
) -> str:
    """
    Get translation from JSONB field with fallback chain

    Args:
        entity: Service/entity dict with i18n fields
        field: Field name (e.g., 'name', 'description')
        language: Target language code
        fallback_languages: Fallback language chain (default: ['en'])

    Returns:
        Translated text or fallback value

    Examples:
        >>> service = {
        ...     'name': 'Filling',
        ...     'name_i18n': {'ru': 'Пломба', 'es': 'Empaste'}
        ... }
        >>> get_translation(service, 'name', 'ru')
        'Пломба'
        >>> get_translation(service, 'name', 'de')  # Not available
        'Filling'  # Falls back to base field
        >>> get_translation(service, 'name', 'de', fallback_languages=['es', 'en'])
        'Empaste'  # Falls back to Spanish first
    """
    if fallback_languages is None:
        fallback_languages = ['en']

    i18n_field = f'{field}_i18n'

    # Try target language first
    if i18n_field in entity and entity[i18n_field]:
        translations = entity[i18n_field]
        if isinstance(translations, dict) and language in translations and translations[language]:
            return translations[language]

    # Try fallback languages in order
    if i18n_field in entity and entity[i18n_field]:
        translations = entity[i18n_field]
        if isinstance(translations, dict):
            for fallback_lang in fallback_languages:
                if fallback_lang in translations and translations[fallback_lang]:
                    return translations[fallback_lang]

    # Final fallback: base field
    return entity.get(field, '')


def set_translation(
    translations: Optional[Dict[str, str]],
    language: str,
    value: str
) -> Dict[str, str]:
    """
    Set translation in JSONB-compatible dict

    Args:
        translations: Existing translations dict (can be None)
        language: Language code
        value: Translation value

    Returns:
        Updated translations dict

    Examples:
        >>> translations = set_translation(None, 'ru', 'Пломба')
        >>> translations
        {'ru': 'Пломба'}
        >>> translations = set_translation(translations, 'es', 'Empaste')
        >>> translations
        {'ru': 'Пломба', 'es': 'Empaste'}
    """
    if translations is None:
        translations = {}

    translations[language] = value
    return translations


def get_available_languages(entity: Dict[str, Any], field: str) -> List[str]:
    """
    Get list of available languages for a specific field

    Args:
        entity: Entity dict with i18n fields
        field: Field name (e.g., 'name', 'description')

    Returns:
        List of language codes available for this field

    Examples:
        >>> service = {
        ...     'name': 'Filling',
        ...     'name_i18n': {'ru': 'Пломба', 'es': 'Empaste', 'pt': 'Obturação'}
        ... }
        >>> get_available_languages(service, 'name')
        ['ru', 'es', 'pt']
        >>> get_available_languages(service, 'description')
        []
    """
    i18n_field = f'{field}_i18n'
    if i18n_field in entity and entity[i18n_field]:
        translations = entity[i18n_field]
        if isinstance(translations, dict):
            return list(translations.keys())
    return []


def merge_translations(
    existing: Optional[Dict[str, str]],
    new_translations: Dict[str, str],
    overwrite: bool = True
) -> Dict[str, str]:
    """
    Merge new translations into existing translations

    Args:
        existing: Existing translations dict
        new_translations: New translations to merge
        overwrite: Whether to overwrite existing values (default: True)

    Returns:
        Merged translations dict

    Examples:
        >>> existing = {'ru': 'Пломба', 'es': 'Empaste'}
        >>> new = {'es': 'Nueva Empaste', 'pt': 'Obturação'}
        >>> merge_translations(existing, new, overwrite=True)
        {'ru': 'Пломба', 'es': 'Nueva Empaste', 'pt': 'Obturação'}
        >>> merge_translations(existing, new, overwrite=False)
        {'ru': 'Пломба', 'es': 'Empaste', 'pt': 'Obturação'}
    """
    if existing is None:
        existing = {}

    result = existing.copy()

    for lang, value in new_translations.items():
        if overwrite or lang not in result:
            result[lang] = value

    return result


def has_translation(
    entity: Dict[str, Any],
    field: str,
    language: str
) -> bool:
    """
    Check if a translation exists for a specific field and language

    Args:
        entity: Entity dict with i18n fields
        field: Field name (e.g., 'name', 'description')
        language: Language code to check

    Returns:
        True if translation exists and is non-empty

    Examples:
        >>> service = {
        ...     'name': 'Filling',
        ...     'name_i18n': {'ru': 'Пломба', 'es': ''}
        ... }
        >>> has_translation(service, 'name', 'ru')
        True
        >>> has_translation(service, 'name', 'es')  # Empty string
        False
        >>> has_translation(service, 'name', 'pt')  # Not present
        False
    """
    i18n_field = f'{field}_i18n'
    if i18n_field in entity and entity[i18n_field]:
        translations = entity[i18n_field]
        if isinstance(translations, dict):
            return language in translations and bool(translations[language])
    return False


def build_i18n_dict(*language_pairs: tuple) -> Dict[str, str]:
    """
    Build a JSONB-compatible i18n dictionary from language pairs

    Args:
        *language_pairs: Tuples of (language_code, translation)

    Returns:
        Dictionary ready for JSONB storage

    Examples:
        >>> build_i18n_dict(('ru', 'Пломба'), ('es', 'Empaste'), ('pt', 'Obturação'))
        {'ru': 'Пломба', 'es': 'Empaste', 'pt': 'Obturação'}
        >>> build_i18n_dict(('ru', 'Тест'), ('es', ''))  # Filters empty
        {'ru': 'Тест'}
    """
    return {lang: text for lang, text in language_pairs if text}
