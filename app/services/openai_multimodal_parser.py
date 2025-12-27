"""
Multimodal bulk parser using OpenAI GPT-4 for healthcare data extraction
Supports images, PDFs, CSVs, and text with intelligent entity discovery
"""

import os
import base64
import json
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import logging
from openai import AsyncOpenAI

from .grok_multimodal_parser import (
    DataType,
    TableType,
    DetectedEntity,
    DiscoveryResult,
    FieldMapping,
    ImportResult
)

logger = logging.getLogger(__name__)

class OpenAIMultimodalParser:
    """Parser using OpenAI GPT-4 Vision for multimodal data extraction"""

    # Reuse table schemas from Grok parser
    TABLE_SCHEMAS = {
        "doctors": {
            "fields": [
                "title", "first_name", "last_name", "middle_name", "suffix",
                "specialization", "sub_specialties", "license_number",
                "email", "phone", "years_of_experience", "bio",
                "languages_spoken", "education", "certifications",
                "active", "accepting_new_patients"
            ],
            "required": ["first_name", "last_name"],
            "identifiers": ["doctor", "physician", "dr", "md", "specialist", "practitioner"]
        },
        "services": {
            "fields": [
                "code", "name", "category", "price", "currency",
                "duration_minutes", "description", "requires_specialty",
                "is_multi_stage", "stage_config", "insurance_codes"
            ],
            "required": ["name"],
            "identifiers": ["service", "procedure", "treatment", "price", "cost", "fee"]
        },
        "patients": {
            "fields": [
                "first_name", "last_name", "middle_name", "date_of_birth",
                "gender", "email", "phone", "address", "city", "state", "zip",
                "insurance_provider", "insurance_id", "emergency_contact"
            ],
            "required": ["first_name", "last_name"],
            "identifiers": ["patient", "client", "member", "insurance", "dob"]
        },
        "appointments": {
            "fields": [
                "patient_name", "doctor_name", "service", "appointment_date",
                "appointment_time", "duration_minutes", "status", "notes",
                "room", "confirmed", "reminder_sent"
            ],
            "required": ["appointment_date"],
            "identifiers": ["appointment", "booking", "schedule", "visit", "slot"]
        },
        "rooms": {
            "fields": [
                "name", "number", "floor", "building", "capacity",
                "equipment", "availability_schedule", "is_available"
            ],
            "required": ["name"],
            "identifiers": ["room", "suite", "office", "facility", "location"]
        }
    }

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is required for OpenAI parser")

        self.client = AsyncOpenAI(api_key=self.api_key)
        # Model selection: TIER_MULTIMODAL_MODEL > default
        # Aligns with tier-based model abstraction system
        self.model = os.getenv("TIER_MULTIMODAL_MODEL", "gemini-3-flash-preview")

    async def discover_entities(self,
                               file_content: bytes,
                               mime_type: str,
                               filename: str = None) -> DiscoveryResult:
        """
        Phase 1: Analyze input and discover what entities it contains
        """
        try:
            # Prepare content for OpenAI
            content_data = self._prepare_content(file_content, mime_type, filename)

            # Build discovery prompt
            discovery_prompt = self._build_discovery_prompt()

            # Call OpenAI
            response = await self._call_openai(content_data, discovery_prompt)

            # Parse response
            result = self._parse_discovery_response(response)

            return result

        except Exception as e:
            logger.error(f"Entity discovery failed: {e}")
            raise

    def _prepare_content(self, file_content: bytes, mime_type: str, filename: str = None) -> Dict:
        """Prepare content for OpenAI based on file type"""

        if mime_type.startswith("image/"):
            # Direct image encoding
            base64_content = base64.b64encode(file_content).decode('utf-8')
            return {
                "type": "image",
                "content": f"data:{mime_type};base64,{base64_content}"
            }

        elif mime_type == "text/csv" or (filename and filename.endswith('.csv')):
            # CSV as text
            text = file_content.decode('utf-8', errors='ignore')
            return {
                "type": "text",
                "content": text
            }

        else:
            # Try to decode as text
            text = file_content.decode('utf-8', errors='ignore')
            return {
                "type": "text",
                "content": text
            }

    def _build_discovery_prompt(self) -> str:
        """Build prompt for entity discovery phase"""
        return """
        Analyze this healthcare data and identify ALL information present.

        Look for these types of data:

        1. PEOPLE (doctors, patients, staff):
           - Names, titles, roles
           - Contact information (emails, phones)
           - Qualifications, specializations, licenses

        2. MEDICAL SERVICES/PROCEDURES:
           - Service/procedure names
           - Prices, costs, fees
           - Durations, categories

        3. APPOINTMENTS/SCHEDULES:
           - Dates, times, durations
           - Patient and doctor names
           - Services, rooms

        4. FACILITIES/ROOMS:
           - Room names, numbers
           - Equipment, capacity
           - Availability

        5. PATIENTS:
           - Names, demographics
           - Contact info
           - Insurance details

        For each piece of information found, provide:
        - field_name: The column/field name as it appears
        - sample_values: 2-3 example values (not all)
        - data_type: string/number/date/email/phone/currency
        - suggested_table: Which table this belongs to (doctors/services/patients/appointments/rooms)
        - suggested_field: The standardized field name for our database
        - confidence: 0-1 score of how confident you are

        Return as JSON:
        {
          "detected_entities": [
            {
              "field_name": "Doctor Name",
              "sample_values": ["Dr. Smith", "Dr. Johnson"],
              "data_type": "string",
              "occurrence_count": 5,
              "suggested_table": "doctors",
              "suggested_field": "full_name",
              "confidence": 0.95
            }
          ],
          "summary": {
            "total_rows": 10,
            "likely_type": "doctors",
            "confidence": 0.9
          }
        }
        """

    async def _call_openai(self, content_data: Dict, prompt: str) -> str:
        """Call OpenAI API"""
        try:
            messages = []

            if content_data["type"] == "image":
                messages = [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": content_data["content"]}
                            }
                        ]
                    }
                ]
            else:
                messages = [
                    {
                        "role": "user",
                        "content": f"{prompt}\n\nData to analyze:\n{content_data['content'][:3000]}"  # Limit for token efficiency
                    }
                ]

            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=2000
            )

            return response.choices[0].message.content

        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            raise Exception(f"OpenAI API error: {str(e)}")

    def _parse_discovery_response(self, response: str) -> DiscoveryResult:
        """Parse OpenAI's discovery response"""
        try:
            data = json.loads(response)

            entities = []
            for entity_data in data.get("detected_entities", []):
                entities.append(DetectedEntity(
                    field_name=entity_data.get("field_name", ""),
                    sample_values=entity_data.get("sample_values", []),
                    data_type=entity_data.get("data_type", "string"),
                    occurrence_count=entity_data.get("occurrence_count", 0),
                    suggested_table=entity_data.get("suggested_table", "unknown"),
                    suggested_field=entity_data.get("suggested_field", entity_data.get("field_name", "")),
                    confidence=entity_data.get("confidence", 0.5),
                    metadata=entity_data.get("metadata")
                ))

            # If no entities found, create basic ones from the data
            if not entities and "summary" in data:
                # Try to extract basic fields
                if data["summary"].get("likely_type") == "doctors":
                    entities = [
                        DetectedEntity("first_name", [], "string", 0, "doctors", "first_name", 0.8, None),
                        DetectedEntity("last_name", [], "string", 0, "doctors", "last_name", 0.8, None),
                        DetectedEntity("specialization", [], "string", 0, "doctors", "specialization", 0.7, None),
                        DetectedEntity("email", [], "email", 0, "doctors", "email", 0.7, None),
                        DetectedEntity("phone", [], "phone", 0, "doctors", "phone", 0.7, None),
                    ]

            return DiscoveryResult(
                detected_entities=entities,
                summary=data.get("summary", {}),
                warnings=data.get("warnings", [])
            )

        except Exception as e:
            logger.error(f"Error parsing discovery response: {e}")
            # Return a basic result
            return DiscoveryResult(
                detected_entities=[
                    DetectedEntity("data", [""], "string", 0, "unknown", "data", 0.1, None)
                ],
                summary={"error": str(e)},
                warnings=[f"Failed to parse AI response: {str(e)}"]
            )

    def _handle_merged_fields(self, data: Dict, mappings: List[FieldMapping]) -> Dict:
        """Handle merged field transformations"""
        # Start with original data, not empty dict
        result = data.copy()

        for mapping in mappings:
            if mapping.transformation and mapping.transformation.startswith("merge:"):
                # Extract fields to merge
                fields_str = mapping.transformation[6:]  # Remove "merge:" prefix
                fields_to_merge = fields_str.split(",")

                # Collect values from source fields
                values = []
                for field in fields_to_merge:
                    if field in data and data[field]:
                        values.append(str(data[field]))

                # Merge values with space separator
                if values:
                    result[mapping.target_field] = " ".join(values)
                    # Remove source fields that were merged
                    for field in fields_to_merge:
                        result.pop(field, None)

        return result

    async def parse_and_import(self,
                              file_content: bytes,
                              mime_type: str,
                              mappings: List[FieldMapping],
                              clinic_id: str) -> ImportResult:
        """Phase 2: Parse data with validated mappings"""
        try:
            # Prepare content
            content_data = self._prepare_content(file_content, mime_type)

            # Build parsing prompt with specific mappings
            parsing_prompt = self._build_parsing_prompt(mappings)

            # Call OpenAI for structured extraction
            response = await self._call_openai(content_data, parsing_prompt)

            # Parse response
            data = json.loads(response)

            # Only handle merged fields if there are any merge transformations
            has_merges = any(m.transformation and m.transformation.startswith("merge:") for m in mappings)
            if has_merges and hasattr(self, '_handle_merged_fields'):
                for table in data:
                    if isinstance(data[table], list):
                        for i, record in enumerate(data[table]):
                            data[table][i] = self._handle_merged_fields(record, mappings)

            # Organize data by tables
            imported_data = {}
            for table in ['doctors', 'services', 'patients', 'appointments', 'rooms']:
                if table in data:
                    imported_data[table] = len(data[table])

            return ImportResult(
                success=True,
                imported=imported_data,
                failed={},
                warnings=data.get('warnings', []),
                details={'data': data}
            )

        except Exception as e:
            logger.error(f"Parse and import failed: {e}")
            return ImportResult(
                success=False,
                imported={},
                failed={'error': str(e)},
                warnings=[f"Import failed: {str(e)}"],
                details={}
            )

    def _build_parsing_prompt(self, mappings: List[FieldMapping]) -> str:
        """Build prompt for data extraction based on mappings"""

        # Group mappings by table
        table_mappings = {}
        for mapping in mappings:
            if mapping.target_table not in table_mappings:
                table_mappings[mapping.target_table] = []
            table_mappings[mapping.target_table].append(mapping)

        # Build extraction instructions
        extraction_instructions = []
        for table, fields in table_mappings.items():
            field_map = ", ".join([f"'{m.original_field}' -> {m.target_field}" for m in fields])
            extraction_instructions.append(f"{table}: Extract fields {field_map}")

        return f"""
        Parse this data and extract information for the specified tables and fields.

        Extraction Instructions:
        {' | '.join(extraction_instructions)}

        Important Rules:
        1. Extract ALL rows/records found, not just samples
        2. Clean and standardize data:
           - Names: proper capitalization
           - Phones: format as (XXX) XXX-XXXX
           - Emails: lowercase, validated format
        3. Handle missing data with null

        Return as JSON with structure:
        {{
          "doctors": [
            {{"first_name": "John", "last_name": "Smith", ...}}
          ],
          "services": [...],
          "patients": [...],
          "appointments": [...],
          "rooms": [...]
        }}

        Only include tables that have data. Extract ALL records.
        """