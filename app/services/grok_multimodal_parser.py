"""
Multimodal bulk parser using Grok-4-fast for healthcare data extraction
Supports images, PDFs, CSVs, and text with intelligent entity discovery
"""

import os
import base64
import json
import re
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
import asyncio
import aiohttp
from pathlib import Path
import logging
import fitz  # PyMuPDF for PDF handling
from PIL import Image
import io

logger = logging.getLogger(__name__)

class DataType(Enum):
    STRING = "string"
    NUMBER = "number"
    DATE = "date"
    EMAIL = "email"
    PHONE = "phone"
    CURRENCY = "currency"
    BOOLEAN = "boolean"
    TIME = "time"

class TableType(Enum):
    DOCTORS = "doctors"
    SERVICES = "services"
    PATIENTS = "patients"
    APPOINTMENTS = "appointments"
    ROOMS = "rooms"
    UNKNOWN = "unknown"

@dataclass
class DetectedEntity:
    """Represents a discovered entity/field in the input data"""
    field_name: str
    sample_values: List[Any]
    data_type: str
    occurrence_count: int
    suggested_table: str
    suggested_field: str
    confidence: float
    metadata: Optional[Dict] = None

@dataclass
class DiscoveryResult:
    """Result of entity discovery phase"""
    detected_entities: List[DetectedEntity]
    summary: Dict[str, Any]
    raw_preview: Optional[List[Dict]] = None
    warnings: List[str] = None

@dataclass
class FieldMapping:
    """User-validated field mapping"""
    original_field: str
    target_table: str
    target_field: str
    data_type: str
    transformation: Optional[str] = None  # e.g., "split_name", "parse_date"

@dataclass
class ImportResult:
    """Result of parse and import operation"""
    success: bool
    imported: Dict[str, int]  # table -> count
    failed: Dict[str, List[Dict]]  # table -> error records
    warnings: List[str]
    details: Dict[str, Any]

class GrokMultimodalParser:
    """Main parser class using Grok-4-fast for multimodal data extraction"""
    
    # Table schema definitions for mapping
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
        self.api_key = api_key or os.getenv("XAI_API_KEY")
        if not self.api_key:
            raise ValueError("XAI_API_KEY is required for Grok parser")
        
        self.api_url = "https://api.x.ai/v1/chat/completions"
        self.model = "grok-4-fast"
        
    async def discover_entities(self, 
                               file_content: bytes,
                               mime_type: str,
                               filename: str = None) -> DiscoveryResult:
        """
        Phase 1: Analyze input and discover what entities it contains
        """
        try:
            # Prepare content for Grok based on file type
            content_data = await self._prepare_content(file_content, mime_type, filename)
            
            # Build discovery prompt
            discovery_prompt = self._build_discovery_prompt()
            
            # Call Grok for entity discovery
            if content_data["type"] == "visual":
                response = await self._call_grok_vision(
                    content_data["content"],
                    discovery_prompt
                )
            else:
                response = await self._call_grok_text(
                    content_data["content"],
                    discovery_prompt
                )
            
            # Parse discovery response
            result = self._parse_discovery_response(response)
            
            # Add intelligent suggestions based on patterns
            result = self._enhance_with_suggestions(result)
            
            return result
            
        except Exception as e:
            logger.error(f"Entity discovery failed: {e}")
            raise
    
    async def parse_and_import(self,
                              file_content: bytes,
                              mime_type: str,
                              mappings: List[FieldMapping],
                              clinic_id: str) -> ImportResult:
        """
        Phase 2: Parse data with validated mappings and prepare for import
        """
        try:
            # Prepare content
            content_data = await self._prepare_content(file_content, mime_type)
            
            # Build parsing prompt with specific mappings
            parsing_prompt = self._build_parsing_prompt(mappings)
            
            # Call Grok for structured extraction
            if content_data["type"] == "visual":
                response = await self._call_grok_vision(
                    content_data["content"],
                    parsing_prompt
                )
            else:
                response = await self._call_grok_text(
                    content_data["content"],
                    parsing_prompt
                )
            
            # Parse structured data
            parsed_data = self._parse_import_response(response, mappings)

            # Count records by table
            counts = {}
            for table in ['doctors', 'services', 'patients', 'appointments', 'rooms']:
                if table in parsed_data and isinstance(parsed_data[table], list):
                    counts[table] = len(parsed_data[table])
                else:
                    counts[table] = 0

            return ImportResult(
                success=True,
                imported=counts,
                failed={},
                warnings=[],
                details={"data": parsed_data}
            )
            
        except Exception as e:
            logger.error(f"Parse and import failed: {e}")
            raise
    
    async def _prepare_content(self, 
                              file_content: bytes, 
                              mime_type: str,
                              filename: str = None) -> Dict:
        """
        Prepare content for Grok based on file type
        """
        if mime_type.startswith("image/"):
            # Direct image encoding
            base64_content = base64.b64encode(file_content).decode('utf-8')
            return {
                "type": "visual",
                "content": f"data:{mime_type};base64,{base64_content}"
            }
            
        elif mime_type == "application/pdf":
            # Extract first few pages as images for better analysis
            images = self._extract_pdf_pages_as_images(file_content, max_pages=5)
            if images:
                return {
                    "type": "visual",
                    "content": images[0]  # Use first page for discovery
                }
            else:
                # Fallback to text extraction
                text = self._extract_pdf_text(file_content)
                return {
                    "type": "text",
                    "content": text
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
    
    def _extract_pdf_pages_as_images(self, pdf_bytes: bytes, max_pages: int = 5) -> List[str]:
        """Extract PDF pages as base64 images"""
        images = []
        try:
            pdf_stream = io.BytesIO(pdf_bytes)
            doc = fitz.open(stream=pdf_stream, filetype="pdf")
            
            pages_to_process = min(len(doc), max_pages)
            
            for page_num in range(pages_to_process):
                page = doc[page_num]
                mat = fitz.Matrix(2.0, 2.0)  # 2x zoom
                pix = page.get_pixmap(matrix=mat)
                img_data = pix.tobytes("png")
                img_base64 = base64.b64encode(img_data).decode('utf-8')
                images.append(f"data:image/png;base64,{img_base64}")
                
            doc.close()
        except Exception as e:
            logger.error(f"PDF image extraction failed: {e}")
            
        return images
    
    def _extract_pdf_text(self, pdf_bytes: bytes) -> str:
        """Extract text from PDF"""
        text = ""
        try:
            pdf_stream = io.BytesIO(pdf_bytes)
            doc = fitz.open(stream=pdf_stream, filetype="pdf")
            
            for page in doc:
                text += page.get_text()
                
            doc.close()
        except Exception as e:
            logger.error(f"PDF text extraction failed: {e}")
            
        return text
    
    def _build_discovery_prompt(self) -> str:
        """Build prompt for entity discovery phase"""
        return """
        Analyze this healthcare data and identify ALL information present.
        
        Look for these types of data:
        
        1. PEOPLE (doctors, patients, staff):
           - Names, titles, roles
           - Contact information (emails, phones)
           - Qualifications, specializations, licenses
           - Experience, languages, education
           - Availability, schedules
        
        2. MEDICAL SERVICES/PROCEDURES:
           - Service/procedure names
           - Prices, costs, fees
           - Durations, session counts
           - Categories, specializations required
           - Insurance codes, descriptions
        
        3. APPOINTMENTS/SCHEDULES:
           - Dates, times, durations
           - Patient names, doctor names
           - Services requested
           - Statuses, confirmations
           - Room assignments
        
        4. LOCATIONS/FACILITIES:
           - Room names/numbers
           - Buildings, floors
           - Equipment available
           - Capacity information
        
        5. PATIENT INFORMATION:
           - Names, demographics
           - Contact details
           - Insurance information
           - Medical history references
        
        For EACH distinct piece of information found:
        - Extract field name/column header
        - Provide 3-5 sample values
        - Identify data type (string, number, date, email, phone, currency)
        - Count total occurrences
        - Suggest which table it belongs to (doctors, services, patients, appointments, rooms)
        - Suggest database field name
        - Rate confidence (0.0 to 1.0)

        IMPORTANT for NAME fields:
        - If you find a combined "name" or "full_name" field, create TWO separate entities:
          1. One for "first_name" (first word of the name)
          2. One for "last_name" (remaining words of the name)
        - Example: "John Smith" → first_name: "John", last_name: "Smith"
        - Example: "Dr. Jane Doe" → first_name: "Jane", last_name: "Doe" (skip titles)
        
        Return as JSON:
        {
          "detected_entities": [
            {
              "field_name": "original field/column name",
              "sample_values": ["sample1", "sample2", "sample3"],
              "data_type": "string|number|date|email|phone|currency",
              "occurrence_count": 10,
              "suggested_table": "doctors|services|patients|appointments|rooms",
              "suggested_field": "database_field_name",
              "confidence": 0.85,
              "metadata": {
                "format_pattern": "optional format info",
                "notes": "any special observations"
              }
            }
          ],
          "summary": {
            "total_rows": 100,
            "primary_data_type": "doctors|services|patients|appointments|rooms|mixed",
            "detected_tables": ["doctors", "services"],
            "data_quality": "high|medium|low",
            "confidence": 0.9,
            "observations": "Key observations about the data"
          },
          "warnings": [
            "Any data quality issues or concerns"
          ]
        }
        
        Be thorough - extract EVERYTHING that could be useful for a healthcare clinic.
        """
    
    def _build_parsing_prompt(self, mappings: List[FieldMapping]) -> str:
        """Build prompt for structured parsing with mappings"""
        
        # Group mappings by table
        table_mappings = {}
        for mapping in mappings:
            if mapping.target_table not in table_mappings:
                table_mappings[mapping.target_table] = []
            table_mappings[mapping.target_table].append(mapping)
        
        # Build specific extraction instructions
        extraction_instructions = []
        for table, fields in table_mappings.items():
            field_map = "\n".join([
                f"  - '{m.original_field}' -> {m.target_field} ({m.data_type})"
                for m in fields
            ])
            extraction_instructions.append(f"""
            {table.upper()} table:
            Extract these fields:
            {field_map}
            """)
        
        return f"""
        Parse this healthcare data and extract information for the specified tables and fields.
        
        Extraction Instructions:
        {"".join(extraction_instructions)}
        
        Important Rules:
        1. Extract ALL rows/records found, not just samples
        2. Clean and standardize data:
           - Dates: YYYY-MM-DD format
           - Times: HH:MM format
           - Phones: digits only (remove formatting)
           - Emails: lowercase, validated format
           - Names: proper capitalization
           - Prices: numeric only (remove $ symbols)
        
        3. Handle missing data:
           - Use null for missing required fields
           - Generate codes if needed (e.g., service codes from names)
           - Set reasonable defaults where appropriate
        
        4. Maintain relationships:
           - Link related records correctly
           - Preserve foreign key relationships
           - Keep associated data together
        
        Return as JSON:
        {{
          "doctors": [
            {{"first_name": "John", "last_name": "Smith", ...}}
          ],
          "services": [
            {{"name": "Root Canal", "price": 1200, ...}}
          ],
          "patients": [...],
          "appointments": [...],
          "rooms": [...],
          "metadata": {{
            "total_extracted": 100,
            "extraction_quality": 0.95,
            "skipped_rows": [],
            "transformations_applied": []
          }}
        }}
        
        Extract and return ALL data, maintaining data integrity and relationships.
        """
    
    async def _call_grok_vision(self, image_data: str, prompt: str) -> str:
        """Call Grok API for vision processing"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        messages = [{
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": image_data,
                        "detail": "high"
                    }
                },
                {
                    "type": "text",
                    "text": prompt
                }
            ]
        }]
        
        payload = {
            "model": self.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
            "max_tokens": 8000
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(self.api_url, headers=headers, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"Grok API error: {response.status} - {error_text}")
                
                result = await response.json()
                return result['choices'][0]['message']['content']
    
    async def _call_grok_text(self, text_content: str, prompt: str) -> str:
        """Call Grok API for text processing"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        # Truncate if too long
        if len(text_content) > 30000:
            text_content = text_content[:30000] + "\n...[truncated]"
        
        full_prompt = f"{prompt}\n\nData to analyze:\n{text_content}"
        
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You are a healthcare data extraction expert."},
                {"role": "user", "content": full_prompt}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
            "max_tokens": 8000
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(self.api_url, headers=headers, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"Grok API error: {response.status} - {error_text}")
                
                result = await response.json()
                return result['choices'][0]['message']['content']
    
    def _parse_discovery_response(self, response: str) -> DiscoveryResult:
        """Parse Grok's discovery response"""
        try:
            data = json.loads(response)
            
            entities = []
            for entity_data in data.get("detected_entities", []):
                entities.append(DetectedEntity(
                    field_name=entity_data["field_name"],
                    sample_values=entity_data["sample_values"],
                    data_type=entity_data["data_type"],
                    occurrence_count=entity_data.get("occurrence_count", 0),
                    suggested_table=entity_data["suggested_table"],
                    suggested_field=entity_data["suggested_field"],
                    confidence=entity_data.get("confidence", 0.5),
                    metadata=entity_data.get("metadata")
                ))
            
            return DiscoveryResult(
                detected_entities=entities,
                summary=data.get("summary", {}),
                warnings=data.get("warnings", [])
            )
            
        except Exception as e:
            logger.error(f"Error parsing discovery response: {e}")
            raise
    
    def _enhance_with_suggestions(self, result: DiscoveryResult) -> DiscoveryResult:
        """Enhance discovery with intelligent suggestions"""

        # Check for combined name fields and split them
        entities_to_add = []

        for entity in result.detected_entities:
            field_lower = entity.field_name.lower()

            # If it's a combined name field, ADD split fields (but keep original)
            if ('name' in field_lower and
                'first' not in field_lower and
                'last' not in field_lower and
                entity.suggested_table in ['doctors', 'patients']):

                # Process name values for splitting
                first_values = []
                last_values = []
                for value in entity.sample_values:
                    if value and isinstance(value, str):
                        parts = value.strip().split(None, 1)
                        # Skip titles like Dr., Mr., Ms.
                        if parts and parts[0].lower() in ['dr', 'dr.', 'mr', 'mr.', 'ms', 'ms.', 'mrs', 'mrs.']:
                            parts = parts[1:] if len(parts) > 1 else parts
                        if parts:
                            first_values.append(parts[0])
                            last_values.append(parts[1] if len(parts) > 1 else parts[0])

                # Add split field entities (derived from original)
                # Use field names that clearly indicate they're derived
                entities_to_add.append(DetectedEntity(
                    field_name=f"{entity.field_name} → First Name",
                    sample_values=first_values[:3],
                    data_type="string",
                    occurrence_count=entity.occurrence_count,
                    suggested_table=entity.suggested_table,
                    suggested_field="first_name",
                    confidence=0.95,
                    metadata={"derived_from": entity.field_name, "is_split_field": True}
                ))

                entities_to_add.append(DetectedEntity(
                    field_name=f"{entity.field_name} → Last Name",
                    sample_values=last_values[:3],
                    data_type="string",
                    occurrence_count=entity.occurrence_count,
                    suggested_table=entity.suggested_table,
                    suggested_field="last_name",
                    confidence=0.95,
                    metadata={"derived_from": entity.field_name, "is_split_field": True}
                ))

                # KEEP the original field but suggest it could be split
                entity.metadata = entity.metadata or {}
                entity.metadata["can_be_split"] = True
                entity.metadata["split_suggestion"] = "This field appears to contain full names that can be split into first_name and last_name"
                # Don't suggest a single field mapping for combined names
                entity.suggested_field = None
                entity.confidence = 0.75  # Lower confidence for unsplit name fields
            else:
                # Improve field mapping suggestions for non-name fields
                improved = self._suggest_field_mapping(
                    entity.field_name,
                    entity.sample_values,
                    entity.suggested_table
                )
                if improved:
                    entity.suggested_field = improved["field"]
                    entity.confidence = max(entity.confidence, improved["confidence"])

                # Detect data type more accurately
                detected_type = self._detect_data_type(entity.sample_values)
                if detected_type:
                    entity.data_type = detected_type

        # Add the new split entities
        result.detected_entities.extend(entities_to_add)

        return result
    
    def _suggest_field_mapping(self, 
                              field_name: str, 
                              samples: List[Any],
                              table: str) -> Optional[Dict]:
        """Suggest field mapping based on patterns"""
        
        field_lower = field_name.lower().replace(" ", "_").replace("-", "_")
        
        # Common patterns with higher confidence for exact matches
        patterns = {
            "doctors": {
                # Exact matches get highest confidence
                r"^first[_\s]?name$": ("first_name", 0.99),
                r"^last[_\s]?name$": ("last_name", 0.99),
                r"^email$": ("email", 0.99),
                r"^phone$": ("phone", 0.99),
                r"^specialization$": ("specialization", 0.99),
                r"^languages?$": ("languages_spoken", 0.95),
                r"^bio$": ("bio", 0.95),
                r"^title$": ("title", 0.95),
                r"^suffix$": ("suffix", 0.95),
                r"^middle[_\s]?name$": ("middle_name", 0.95),

                # Very close matches
                r"^specialty$": ("specialization", 0.92),
                r"^languages[_\s]?spoken$": ("languages_spoken", 0.92),
                r"^years?[_\s]?of[_\s]?experience$": ("years_of_experience", 0.92),
                r"^license[_\s]?number$": ("license_number", 0.92),
                r"^npi[_\s]?number$": ("npi_number", 0.92),
                r"^dea[_\s]?number$": ("dea_number", 0.92),
                r"^direct[_\s]?line$": ("direct_line", 0.92),
                r"^education$": ("education", 0.92),
                r"^certifications?$": ("certifications", 0.92),
                r"^photo[_\s]?url$": ("photo_url", 0.92),
                r"^active$": ("active", 0.92),
                r"^accepting[_\s]?new[_\s]?patients$": ("accepting_new_patients", 0.92),

                # Partial matches with good confidence
                r"^name$|^full.*name$|^doctor.*name$": ("full_name", 0.85),  # Will be split later
                r"(first|given).*name": ("first_name", 0.88),
                r"(last|family|sur).*name": ("last_name", 0.88),
                r"(specialization|specialty|department)": ("specialization", 0.85),
                r"(license|registration).*num": ("license_number", 0.85),
                r"(email|e[-_\s]?mail)": ("email", 0.90),
                r"(phone|tel|contact|mobile|cell)": ("phone", 0.85),
                r"(bio|about|description|profile)": ("bio", 0.82),
                r"(year|exp|experience)": ("years_of_experience", 0.82),
                r"(language|speak|spoken)": ("languages_spoken", 0.82),
                r"(cert|certification|qualified)": ("certifications", 0.82),
                r"(edu|education|degree|school)": ("education", 0.82),
                r"(available|availability)": ("available_days", 0.82),
                r"(hours|schedule|timing)": ("working_hours", 0.82),
            },
            "services": {
                # Exact matches
                r"^name$": ("name", 0.99),
                r"^code$": ("code", 0.99),
                r"^price$": ("price", 0.99),
                r"^category$": ("category", 0.99),
                r"^description$": ("description", 0.99),
                r"^duration[_\s]?minutes$": ("duration_minutes", 0.95),
                r"^currency$": ("currency", 0.95),
                r"^is[_\s]?active$": ("is_active", 0.95),

                # Very close matches
                r"^service[_\s]?name$": ("name", 0.92),
                r"^procedure[_\s]?name$": ("name", 0.92),
                r"^treatment[_\s]?name$": ("name", 0.92),
                r"^service[_\s]?code$": ("code", 0.92),
                r"^duration$": ("duration_minutes", 0.90),
                r"^minutes$": ("duration_minutes", 0.88),
                r"^cost$": ("price", 0.92),
                r"^fee$": ("price", 0.90),
                r"^charge$": ("price", 0.88),

                # Partial matches
                r"(service|procedure|treatment).*name": ("name", 0.85),
                r"(code|id|sku)": ("code", 0.82),
                r"(price|cost|fee|charge|amount)": ("price", 0.85),
                r"(duration|time|length|minutes)": ("duration_minutes", 0.82),
                r"(category|type|class|group)": ("category", 0.82),
                r"(description|detail|info|notes)": ("description", 0.82),
                r"(insurance|coverage).*code": ("insurance_codes", 0.82),
                r"(require|need|specialty)": ("requires_specialty", 0.82),
            },
            "patients": {
                # Exact matches
                r"^first[_\s]?name$": ("first_name", 0.99),
                r"^last[_\s]?name$": ("last_name", 0.99),
                r"^email$": ("email", 0.99),
                r"^phone$": ("phone", 0.99),
                r"^gender$": ("gender", 0.99),
                r"^address$": ("address", 0.99),
                r"^city$": ("city", 0.99),
                r"^state$": ("state", 0.99),
                r"^zip$|^zip[_\s]?code$": ("zip", 0.99),
                r"^middle[_\s]?name$": ("middle_name", 0.95),

                # Very close matches
                r"^date[_\s]?of[_\s]?birth$": ("date_of_birth", 0.95),
                r"^dob$": ("date_of_birth", 0.95),
                r"^insurance[_\s]?provider$": ("insurance_provider", 0.92),
                r"^insurance[_\s]?id$": ("insurance_id", 0.92),
                r"^emergency[_\s]?contact$": ("emergency_contact", 0.92),
                r"^blood[_\s]?type$": ("blood_type", 0.92),

                # Partial matches with good confidence
                r"(first|given).*name": ("first_name", 0.88),
                r"(last|family|sur).*name": ("last_name", 0.88),
                r"(dob|birth|birthday)": ("date_of_birth", 0.85),
                r"(email|e[-_\s]?mail)": ("email", 0.90),
                r"(phone|tel|mobile|cell)": ("phone", 0.85),
                r"(insurance|carrier|plan|coverage)": ("insurance_provider", 0.82),
                r"(gender|sex)": ("gender", 0.85),
                r"(address|street|location)": ("address", 0.82),
                r"(emergency|contact)": ("emergency_contact", 0.82),
            },
            "appointments": {
                # Exact matches
                r"^appointment[_\s]?date$": ("appointment_date", 0.99),
                r"^appointment[_\s]?time$": ("appointment_time", 0.99),
                r"^date$": ("appointment_date", 0.95),
                r"^time$": ("appointment_time", 0.95),
                r"^status$": ("status", 0.99),
                r"^room$": ("room", 0.99),
                r"^notes$": ("notes", 0.99),
                r"^duration[_\s]?minutes$": ("duration_minutes", 0.95),
                r"^confirmed$": ("confirmed", 0.95),
                r"^reminder[_\s]?sent$": ("reminder_sent", 0.95),

                # Very close matches
                r"^patient[_\s]?name$": ("patient_name", 0.92),
                r"^doctor[_\s]?name$": ("doctor_name", 0.92),
                r"^service$": ("service", 0.92),
                r"^procedure$": ("service", 0.88),

                # Partial matches
                r"(date|day|when|schedule)": ("appointment_date", 0.85),
                r"(time|hour|slot|timing)": ("appointment_time", 0.85),
                r"(patient|client|customer)": ("patient_name", 0.82),
                r"(doctor|provider|practitioner|physician)": ("doctor_name", 0.82),
                r"(service|procedure|treatment|reason)": ("service", 0.82),
                r"(status|confirmed|state|confirmation)": ("status", 0.82),
                r"(room|location|suite|office)": ("room", 0.82),
                r"(note|notes|comment|remark)": ("notes", 0.82),
            },
            "rooms": {
                # Exact matches
                r"^name$": ("name", 0.99),
                r"^number$": ("number", 0.99),
                r"^floor$": ("floor", 0.99),
                r"^building$": ("building", 0.99),
                r"^capacity$": ("capacity", 0.99),
                r"^equipment$": ("equipment", 0.99),
                r"^is[_\s]?available$": ("is_available", 0.95),

                # Very close matches
                r"^room[_\s]?name$": ("name", 0.92),
                r"^room[_\s]?number$": ("number", 0.92),
                r"^room[_\s]?capacity$": ("capacity", 0.92),
                r"^availability[_\s]?schedule$": ("availability_schedule", 0.92),

                # Partial matches
                r"(room|suite|office).*name": ("name", 0.85),
                r"(number|num|no|#)": ("number", 0.85),
                r"(floor|level|story)": ("floor", 0.85),
                r"(capacity|size|seats|occupancy)": ("capacity", 0.82),
                r"(equipment|tools|devices|amenities)": ("equipment", 0.82),
                r"(available|availability|status)": ("is_available", 0.82),
            }
        }
        
        # Check patterns for the specific table
        if table in patterns:
            for pattern, (field, confidence) in patterns[table].items():
                if re.search(pattern, field_lower):
                    return {"field": field, "confidence": confidence}
        
        return None
    
    def _detect_data_type(self, samples: List[Any]) -> Optional[str]:
        """Detect data type from samples"""
        if not samples:
            return None
        
        # Check each sample
        types = []
        for sample in samples[:5]:  # Check first 5 samples
            if sample is None or sample == "":
                continue
                
            sample_str = str(sample)
            
            # Email pattern
            if re.match(r'^[^@]+@[^@]+\.[^@]+$', sample_str):
                types.append("email")
            # Phone pattern
            elif re.match(r'^[\d\s\-\+\(\)]+$', sample_str) and len(re.sub(r'\D', '', sample_str)) >= 10:
                types.append("phone")
            # Date patterns
            elif re.match(r'^\d{4}-\d{2}-\d{2}$', sample_str):
                types.append("date")
            elif re.match(r'^\d{1,2}/\d{1,2}/\d{2,4}$', sample_str):
                types.append("date")
            # Time pattern
            elif re.match(r'^\d{1,2}:\d{2}(:\d{2})?(\s*[AP]M)?$', sample_str, re.IGNORECASE):
                types.append("time")
            # Currency
            elif re.match(r'^[\$£€¥]\s*[\d,]+\.?\d*$', sample_str):
                types.append("currency")
            elif re.match(r'^[\d,]+\.?\d*\s*[\$£€¥]$', sample_str):
                types.append("currency")
            # Number
            elif re.match(r'^[\d,]+\.?\d*$', sample_str):
                types.append("number")
            # Boolean
            elif sample_str.lower() in ['true', 'false', 'yes', 'no', '1', '0']:
                types.append("boolean")
            else:
                types.append("string")
        
        # Return most common type
        if types:
            return max(set(types), key=types.count)
        
        return "string"
    
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

    def _parse_import_response(self, response: str, mappings: List[FieldMapping]) -> Dict:
        """Parse Grok's structured extraction response"""
        try:
            return json.loads(response)
        except Exception as e:
            logger.error(f"Error parsing import response: {e}")
            raise
    
    def _apply_transformations_UNUSED(self, data: Dict, mappings: List[FieldMapping]) -> Dict:
        """Apply data transformations based on mappings"""
        
        for table_name, records in data.items():
            if table_name == "metadata":
                continue
                
            for record in records:
                # Apply field-specific transformations
                for mapping in mappings:
                    if mapping.target_table == table_name and mapping.transformation:
                        self._apply_field_transformation(
                            record,
                            mapping.target_field,
                            mapping.transformation
                        )
        
        return data
    
    def _apply_field_transformation(self, record: Dict, field: str, transformation: str):
        """Apply specific transformation to a field"""
        if field not in record:
            return
            
        value = record[field]
        
        if transformation == "split_name":
            # Split full name into first/last
            parts = str(value).split()
            if len(parts) >= 2:
                record["first_name"] = parts[0]
                record["last_name"] = parts[-1]
                if len(parts) > 2:
                    record["middle_name"] = " ".join(parts[1:-1])
                    
        elif transformation == "parse_date":
            # Parse various date formats
            # Implementation depends on specific needs
            pass
            
        elif transformation == "clean_phone":
            # Remove non-digits from phone
            record[field] = re.sub(r'\D', '', str(value))
            
        elif transformation == "lowercase_email":
            record[field] = str(value).lower()
            
        elif transformation == "parse_currency":
            # Extract numeric value from currency string
            numeric = re.sub(r'[^\d.]', '', str(value))
            record[field] = float(numeric) if numeric else 0.0
    
    def _validate_data_UNUSED(self, data: Dict) -> Dict:
        """Validate extracted data"""
        validated = {
            "data": {},
            "counts": {},
            "errors": {},
            "warnings": []
        }
        
        for table_name, records in data.items():
            if table_name == "metadata":
                continue
                
            validated["data"][table_name] = []
            validated["errors"][table_name] = []
            validated["counts"][table_name] = 0
            
            schema = self.TABLE_SCHEMAS.get(table_name, {})
            required_fields = schema.get("required", [])
            
            for record in records:
                # Check required fields
                missing = [f for f in required_fields if not record.get(f)]
                if missing:
                    validated["errors"][table_name].append({
                        "record": record,
                        "error": f"Missing required fields: {missing}"
                    })
                    continue
                
                # Validate specific fields
                validation_errors = []
                
                # Email validation
                if "email" in record and record["email"]:
                    if not re.match(r'^[^@]+@[^@]+\.[^@]+$', record["email"]):
                        validation_errors.append(f"Invalid email: {record['email']}")
                
                # Phone validation (at least 10 digits)
                if "phone" in record and record["phone"]:
                    digits = re.sub(r'\D', '', str(record["phone"]))
                    if len(digits) < 10:
                        validation_errors.append(f"Invalid phone: {record['phone']}")
                
                if validation_errors:
                    validated["errors"][table_name].append({
                        "record": record,
                        "errors": validation_errors
                    })
                else:
                    validated["data"][table_name].append(record)
                    validated["counts"][table_name] += 1
        
        return validated


# Export main class
__all__ = ['GrokMultimodalParser', 'DiscoveryResult', 'FieldMapping', 'ImportResult']