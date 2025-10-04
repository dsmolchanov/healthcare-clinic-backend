"""
Fixed import endpoint for multimodal upload
"""

from fastapi import HTTPException, Form
import json
import logging
from typing import Dict, Any

from ..services.openai_multimodal_parser import (
    OpenAIMultimodalParser,
    FieldMapping
)
from ..services.grok_multimodal_parser import GrokMultimodalParser
from ..database import get_supabase

logger = logging.getLogger(__name__)

async def import_data_fixed(
    session_id: str,
    mappings_json: str,
    cache_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Fixed import function using Supabase client
    """
    clinic_id = cache_data["clinic_id"]
    logger.info(f"[DEBUG] Received clinic_id from cache_data: {clinic_id}")

    # Handle legacy clinic_id that was deleted
    if clinic_id == "3e411ecb-3411-4add-91e2-8fa897310cb0":
        logger.warning(f"Legacy clinic_id detected: {clinic_id}, using correct clinic_id")
        clinic_id = "e0c84f56-235d-49f2-9a44-37c1be579afc"  # Use the correct clinic ID

    logger.info(f"[DEBUG] Final clinic_id being used: {clinic_id}")

    # Parse mappings
    mapping_list = []
    mappings_data = json.loads(mappings_json)

    # Group mappings by target table and field to handle merges
    merge_groups = {}
    for mapping in mappings_data:
        if mapping.get("target_table") and mapping.get("target_field"):
            key = f"{mapping['target_table']}.{mapping['target_field']}"
            if key not in merge_groups:
                merge_groups[key] = []
            merge_groups[key].append(mapping)

    # Create final mapping list with merge instructions
    for key, group in merge_groups.items():
        if len(group) == 1:
            # Single mapping, no merge needed
            mapping = group[0]
            mapping_list.append(FieldMapping(
                original_field=mapping["original_field"],
                target_table=mapping["target_table"],
                target_field=mapping["target_field"],
                data_type=mapping.get("data_type", "string"),
                transformation=mapping.get("transformation")
            ))
        else:
            # Multiple mappings to same field - merge them
            logger.info(f"Merging {len(group)} fields into {key}")
            # Create a merged mapping with concatenation transformation
            original_fields = [m["original_field"] for m in group]
            mapping_list.append(FieldMapping(
                original_field=" + ".join(original_fields),  # Indicate merged fields
                target_table=group[0]["target_table"],
                target_field=group[0]["target_field"],
                data_type="string",  # Merged fields become strings
                transformation=f"merge:{','.join(original_fields)}"
            ))

    # Initialize parser - try Grok first, then OpenAI as fallback
    try:
        parser = GrokMultimodalParser()
        logger.info("Using Grok parser for import")
    except Exception as e:
        logger.warning(f"Grok parser failed: {e}, using OpenAI")
        parser = OpenAIMultimodalParser()

    # Parse and prepare data
    logger.info(f"Parsing data with {len(mapping_list)} mappings")
    import_result = await parser.parse_and_import(
        cache_data["file_content"],
        cache_data["mime_type"],
        mapping_list,
        clinic_id
    )

    # Get Supabase client
    supabase = await get_supabase()

    # Import summary
    import_summary = {
        "doctors": 0,
        "services": 0,
        "patients": 0,
        "appointments": 0,
        "rooms": 0,
        "errors": []
    }

    # Log the import result structure for debugging
    logger.info(f"Import result details: {json.dumps(import_result.details, default=str)[:500]}")

    # Import doctors
    if "doctors" in import_result.details.get("data", {}):
        doctors_data = import_result.details["data"]["doctors"]
        if doctors_data:
            logger.info(f"Importing {len(doctors_data)} doctors")
            logger.info(f"Sample doctor data: {json.dumps(doctors_data[0] if doctors_data else {}, default=str)}")

            # Process each doctor
            for i, doctor in enumerate(doctors_data):
                # Make sure we're working with a dict, not just adding to empty dict
                if not isinstance(doctor, dict):
                    logger.error(f"Doctor {i} is not a dict: {type(doctor)} - {doctor}")
                    import_summary["errors"].append(f"Invalid doctor data type at index {i}")
                    continue

                # Add clinic_id to the existing doctor data
                doctor["clinic_id"] = clinic_id

                logger.info(f"Processing doctor {i}: {json.dumps(doctor, default=str)[:200]}")

                # Handle full_name field if present (split into first_name and last_name)
                if "full_name" in doctor and doctor["full_name"]:
                    full_name = doctor["full_name"].strip()
                    # Remove titles like Dr., Mr., Ms., etc.
                    for title in ["Dr.", "Dr", "Mr.", "Mr", "Ms.", "Ms", "Mrs.", "Mrs"]:
                        if full_name.startswith(title + " "):
                            full_name = full_name[len(title):].strip()

                    # Split the name
                    name_parts = full_name.split(None, 1)  # Split on first space
                    if len(name_parts) >= 1:
                        doctor["first_name"] = name_parts[0]
                        doctor["last_name"] = name_parts[1] if len(name_parts) > 1 else name_parts[0]

                    # Remove the full_name field as it's not in the database schema
                    del doctor["full_name"]
                    logger.info(f"Split full_name '{full_name}' into first_name='{doctor.get('first_name')}', last_name='{doctor.get('last_name')}'")

                # Ensure required fields
                if not doctor.get("first_name") or not doctor.get("last_name"):
                    import_summary["errors"].append(f"Doctor missing name: {doctor}")
                    continue

                # Handle field name variations from Grok
                # Map experience_training to appropriate fields
                if "experience_training" in doctor:
                    # Extract years of experience if present
                    exp_text = doctor["experience_training"]
                    import re
                    years_match = re.search(r'(\d+)\+?\s*years?', exp_text, re.IGNORECASE)
                    if years_match:
                        doctor["years_of_experience"] = int(years_match.group(1))

                    # Use the rest as education/certifications
                    doctor["education"] = exp_text
                    del doctor["experience_training"]

                # Handle languages field (may come as semicolon-separated)
                if "languages" in doctor:
                    if isinstance(doctor["languages"], str):
                        # Split on semicolon and clean up
                        langs = [lang.strip() for lang in doctor["languages"].split(";")]
                        doctor["languages_spoken"] = langs
                        del doctor["languages"]

                # Set required fields with defaults if not present
                doctor.setdefault("specialization", "General Practice")

                # Set optional fields - only the essential ones
                doctor.setdefault("license_number", None)

                # Filter out any fields that don't exist in the doctors table schema
                # Core fields that definitely exist (avoiding cache issues)
                allowed_fields = {
                    'clinic_id', 'first_name', 'last_name', 'email', 'phone',
                    'specialization', 'license_number'
                }

                # Remove any fields not in allowed list
                filtered_doctor = {k: v for k, v in doctor.items() if k in allowed_fields}

                # Remove any None values or empty strings that might cause issues
                filtered_doctor = {k: v for k, v in filtered_doctor.items() if v is not None and v != ''}

                try:
                    # Log the data being inserted for debugging
                    logger.info(f"Inserting doctor: {json.dumps(filtered_doctor, default=str)}")

                    # Insert doctor into healthcare schema
                    result = supabase.schema('healthcare').table("doctors").insert(filtered_doctor).execute()
                    import_summary["doctors"] += 1
                    logger.info(f"Successfully inserted doctor: {doctor.get('first_name')} {doctor.get('last_name')}")
                except Exception as e:
                    logger.error(f"Error inserting doctor: {e}")
                    logger.error(f"Doctor data that failed: {json.dumps(doctor, default=str)}")
                    import_summary["errors"].append(f"Doctor error: {str(e)}")

    # Import services
    if "services" in import_result.details.get("data", {}):
        services_data = import_result.details["data"]["services"]
        if services_data:
            logger.info(f"Importing {len(services_data)} services")

            for service in services_data:
                service["clinic_id"] = clinic_id

                # Generate code if missing
                if not service.get("code"):
                    service["code"] = service.get("name", "SVC").upper().replace(" ", "_")[:20]

                # Set defaults
                service.setdefault("category", "General")
                service.setdefault("duration_minutes", 30)
                service.setdefault("is_active", True)

                try:
                    # Insert service into healthcare schema
                    result = supabase.schema('healthcare').table("services").insert(service).execute()
                    import_summary["services"] += 1
                except Exception as e:
                    logger.error(f"Error inserting service: {e}")
                    import_summary["errors"].append(f"Service error: {str(e)}")

    # Import patients
    if "patients" in import_result.details.get("data", {}):
        patients_data = import_result.details["data"]["patients"]
        if patients_data:
            logger.info(f"Importing {len(patients_data)} patients")

            for patient in patients_data:
                patient["clinic_id"] = clinic_id

                try:
                    result = supabase.table("patients").insert(patient).execute()
                    import_summary["patients"] += 1
                except Exception as e:
                    logger.error(f"Error inserting patient: {e}")
                    import_summary["errors"].append(f"Patient error: {str(e)}")

    # Import rooms
    if "rooms" in import_result.details.get("data", {}):
        rooms_data = import_result.details["data"]["rooms"]
        if rooms_data:
            logger.info(f"Importing {len(rooms_data)} rooms")

            for room in rooms_data:
                room["clinic_id"] = clinic_id

                try:
                    result = supabase.table("rooms").insert(room).execute()
                    import_summary["rooms"] += 1
                except Exception as e:
                    logger.error(f"Error inserting room: {e}")
                    import_summary["errors"].append(f"Room error: {str(e)}")

    # Return results
    return {
        "success": True,
        "imported": import_summary,
        "session_id": session_id,
        "errors": import_summary["errors"] if import_summary["errors"] else None
    }