"""
Facts Extractor - Extracts structured facts from documents
"""

import re
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class ExtractedFact:
    """Represents an extracted fact"""
    type: str
    value: Any
    confidence: float
    context: Optional[str] = None
    source_position: Optional[int] = None

class FactsExtractor:
    """Extracts structured facts from documents"""
    
    # Comprehensive patterns for healthcare/clinic context
    PATTERNS = {
        # Contact Information
        'phone': {
            'pattern': r'(?:(?:\+?1[-.\s]?)?\(?(?:[0-9]{3})\)?[-.\s]?)?(?:[0-9]{3})[-.\s]?(?:[0-9]{4})',
            'validator': lambda x: len(re.sub(r'\D', '', x)) >= 10,
            'normalizer': lambda x: re.sub(r'\D', '', x)
        },
        'email': {
            'pattern': r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
            'validator': lambda x: '@' in x and '.' in x.split('@')[1],
            'normalizer': lambda x: x.lower()
        },
        'website': {
            'pattern': r'(?:https?://)?(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&/=]*)',
            'validator': lambda x: True,
            'normalizer': lambda x: x if x.startswith('http') else f'https://{x}'
        },
        
        # Business Hours
        'hours': {
            'pattern': r'(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\s*[-–]\s*(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\s*:?\s*\d{1,2}(?::\d{2})?\s*(?:AM|PM|am|pm)?\s*[-–]\s*\d{1,2}(?::\d{2})?\s*(?:AM|PM|am|pm)?',
            'validator': lambda x: True,
            'normalizer': lambda x: x.strip()
        },
        'time': {
            'pattern': r'\b\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)\b',
            'validator': lambda x: True,
            'normalizer': lambda x: x.upper()
        },
        
        # Address
        'address': {
            'pattern': r'\d+\s+(?:[A-Z][a-z]+\s*)+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Plaza|Parkway|Pkwy)\.?\s*,?\s*(?:[A-Z][a-z]+\s*)*,?\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?',
            'validator': lambda x: True,
            'normalizer': lambda x: x.strip()
        },
        'zipcode': {
            'pattern': r'\b\d{5}(?:-\d{4})?\b',
            'validator': lambda x: len(x.replace('-', '')) >= 5,
            'normalizer': lambda x: x
        },
        
        # Medical/Healthcare Specific
        'doctor': {
            'pattern': r'(?:Dr\.?|Doctor)\s+(?:[A-Z][a-z]+\s*){1,3}(?:MD|DDS|DMD|PhD|DO)?',
            'validator': lambda x: True,
            'normalizer': lambda x: x.strip()
        },
        'npi': {
            'pattern': r'\b\d{10}\b',
            'validator': lambda x: len(x) == 10 and x.isdigit(),
            'normalizer': lambda x: x
        },
        'procedure_code': {
            'pattern': r'\b(?:CPT|ICD-10|ICD10|HCPCS)\s*:?\s*[A-Z0-9]{3,7}\b',
            'validator': lambda x: True,
            'normalizer': lambda x: x.upper()
        },
        
        # Financial
        'price': {
            'pattern': r'\$\s*\d+(?:,\d{3})*(?:\.\d{2})?',
            'validator': lambda x: True,
            'normalizer': lambda x: float(re.sub(r'[^\d.]', '', x))
        },
        'insurance': {
            'pattern': r'(?:Aetna|Anthem|Blue Cross|Blue Shield|BCBS|Cigna|Humana|Kaiser|Medicare|Medicaid|United Healthcare|UHC|Delta Dental|MetLife|Guardian)',
            'validator': lambda x: True,
            'normalizer': lambda x: x.strip()
        },
        
        # Dates
        'date': {
            'pattern': r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b',
            'validator': lambda x: True,
            'normalizer': lambda x: x.strip()
        },
        
        # Appointment Types
        'appointment_type': {
            'pattern': r'(?:consultation|check-up|checkup|cleaning|examination|surgery|procedure|follow-up|followup|emergency|routine|annual|screening)',
            'validator': lambda x: True,
            'normalizer': lambda x: x.lower()
        }
    }
    
    def __init__(self):
        self.compiled_patterns = {}
        self._compile_patterns()
    
    def _compile_patterns(self):
        """Pre-compile regex patterns for efficiency"""
        for fact_type, config in self.PATTERNS.items():
            self.compiled_patterns[fact_type] = re.compile(config['pattern'], re.IGNORECASE)
    
    async def extract(self, text: str, metadata: Dict[str, Any] = None) -> Dict[str, Any]:
        """Extract all facts from text"""
        facts = {}
        
        # Extract using patterns
        for fact_type, pattern in self.compiled_patterns.items():
            matches = pattern.findall(text)
            if matches:
                config = self.PATTERNS[fact_type]
                
                # Validate and normalize matches
                valid_matches = []
                for match in matches:
                    if config['validator'](match):
                        normalized = config['normalizer'](match)
                        if normalized not in valid_matches:
                            valid_matches.append(normalized)
                
                if valid_matches:
                    # Limit number of facts per type
                    facts[fact_type] = valid_matches[:5] if len(valid_matches) > 1 else valid_matches[0]
        
        # Extract business-specific facts
        business_facts = self._extract_business_facts(text)
        facts.update(business_facts)
        
        # Extract medical facts if healthcare context
        if self._is_healthcare_context(text, metadata):
            medical_facts = self._extract_medical_facts(text)
            facts.update(medical_facts)
        
        # Add metadata facts
        if metadata:
            if metadata.get('title'):
                facts['document_title'] = metadata['title']
            if metadata.get('author'):
                facts['document_author'] = metadata['author']
        
        return facts
    
    def _extract_business_facts(self, text: str) -> Dict[str, Any]:
        """Extract business-specific facts"""
        facts = {}
        
        # Business name (look for common patterns)
        business_pattern = r'(?:Welcome to|Visit us at|Contact)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*(?:\s+(?:Clinic|Dental|Medical|Healthcare|Center|Practice|Office))?)'
        business_matches = re.findall(business_pattern, text)
        if business_matches:
            facts['business_name'] = business_matches[0]
        
        # Services offered
        services_keywords = [
            'cleaning', 'examination', 'x-ray', 'filling', 'crown', 'root canal',
            'extraction', 'whitening', 'implant', 'orthodontics', 'braces',
            'consultation', 'surgery', 'check-up', 'screening', 'vaccination'
        ]
        found_services = []
        text_lower = text.lower()
        for service in services_keywords:
            if service in text_lower:
                found_services.append(service)
        if found_services:
            facts['services_offered'] = found_services[:10]
        
        # Languages spoken
        language_pattern = r'(?:We speak|Languages spoken|Hablamos|Se habla)\s*:?\s*([A-Za-z,\s]+)'
        language_matches = re.findall(language_pattern, text, re.IGNORECASE)
        if language_matches:
            languages = [lang.strip() for lang in language_matches[0].split(',')]
            facts['languages'] = languages
        
        return facts
    
    def _extract_medical_facts(self, text: str) -> Dict[str, Any]:
        """Extract medical/healthcare specific facts"""
        facts = {}
        
        # Specialties
        specialties = [
            'general dentistry', 'orthodontics', 'oral surgery', 'endodontics',
            'periodontics', 'pediatric dentistry', 'cosmetic dentistry',
            'family medicine', 'internal medicine', 'pediatrics', 'cardiology',
            'dermatology', 'orthopedics', 'psychiatry', 'neurology'
        ]
        found_specialties = []
        text_lower = text.lower()
        for specialty in specialties:
            if specialty in text_lower:
                found_specialties.append(specialty)
        if found_specialties:
            facts['medical_specialties'] = found_specialties
        
        # Insurance acceptance
        if 'insurance' in text_lower or 'accept' in text_lower:
            facts['accepts_insurance'] = True
        
        # Emergency availability
        if 'emergency' in text_lower or '24/7' in text or 'after hours' in text_lower:
            facts['emergency_available'] = True
        
        # New patient acceptance
        if 'new patient' in text_lower or 'accepting new patients' in text_lower:
            facts['accepting_new_patients'] = True
        
        return facts
    
    def _is_healthcare_context(self, text: str, metadata: Dict[str, Any] = None) -> bool:
        """Determine if the text is in a healthcare context"""
        healthcare_keywords = [
            'clinic', 'hospital', 'medical', 'dental', 'doctor', 'patient',
            'appointment', 'treatment', 'diagnosis', 'health', 'care',
            'physician', 'nurse', 'surgery', 'medication', 'prescription'
        ]
        
        text_lower = text.lower()
        keyword_count = sum(1 for keyword in healthcare_keywords if keyword in text_lower)
        
        # Check metadata for healthcare indicators
        if metadata:
            category = metadata.get('category', '').lower()
            if any(kw in category for kw in ['medical', 'health', 'dental', 'clinic']):
                return True
        
        return keyword_count >= 3
    
    def extract_with_confidence(self, text: str) -> List[ExtractedFact]:
        """Extract facts with confidence scores"""
        extracted_facts = []
        
        for fact_type, pattern in self.compiled_patterns.items():
            matches = pattern.finditer(text)
            for match in matches:
                # Calculate confidence based on context
                confidence = self._calculate_confidence(match, text, fact_type)
                
                fact = ExtractedFact(
                    type=fact_type,
                    value=self.PATTERNS[fact_type]['normalizer'](match.group()),
                    confidence=confidence,
                    context=text[max(0, match.start()-50):min(len(text), match.end()+50)],
                    source_position=match.start()
                )
                extracted_facts.append(fact)
        
        return extracted_facts
    
    def _calculate_confidence(self, match, text: str, fact_type: str) -> float:
        """Calculate confidence score for an extracted fact"""
        confidence = 0.5  # Base confidence
        
        # Increase confidence if surrounded by relevant keywords
        context = text[max(0, match.start()-100):min(len(text), match.end()+100)].lower()
        
        if fact_type == 'phone':
            if any(kw in context for kw in ['call', 'phone', 'contact', 'tel']):
                confidence += 0.3
        elif fact_type == 'email':
            if any(kw in context for kw in ['email', 'contact', 'reach', 'send']):
                confidence += 0.3
        elif fact_type == 'address':
            if any(kw in context for kw in ['location', 'address', 'visit', 'office']):
                confidence += 0.3
        elif fact_type == 'hours':
            if any(kw in context for kw in ['hours', 'open', 'closed', 'schedule']):
                confidence += 0.3
        
        # Increase confidence for well-formatted matches
        if fact_type in ['phone', 'email', 'website']:
            if re.match(self.PATTERNS[fact_type]['pattern'], match.group(), re.IGNORECASE):
                confidence += 0.2
        
        return min(confidence, 1.0)