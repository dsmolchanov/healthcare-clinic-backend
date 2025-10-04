"""
Entity Extractor for Healthcare RAG System

Extracts structured entities from queries including doctors, services, dates,
and medical terms for enhanced search targeting.
"""

import re
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import spacy
from spacy.matcher import Matcher

logger = logging.getLogger(__name__)


class EntityExtractor:
    """Extracts structured entities from natural language queries"""
    
    def __init__(self):
        """Initialize entity extractor with SpaCy NER model"""
        try:
            # Try to load the model, fall back to basic if not available
            try:
                self.nlp = spacy.load("en_core_web_sm")
            except:
                # If model not installed, use blank English model
                self.nlp = spacy.blank("en")
                logger.warning("SpaCy model not found, using basic entity extraction")
            
            # Initialize matcher for pattern-based extraction
            self.matcher = Matcher(self.nlp.vocab)
            self._setup_patterns()
            
            # Common medical specializations
            self.specializations = [
                'cardiology', 'cardiologist',
                'dermatology', 'dermatologist',
                'pediatrics', 'pediatrician',
                'orthopedics', 'orthopedist',
                'neurology', 'neurologist',
                'psychiatry', 'psychiatrist',
                'gynecology', 'gynecologist', 'obgyn',
                'ophthalmology', 'ophthalmologist',
                'radiology', 'radiologist',
                'oncology', 'oncologist',
                'endocrinology', 'endocrinologist',
                'gastroenterology', 'gastroenterologist',
                'pulmonology', 'pulmonologist',
                'general practice', 'gp', 'family medicine',
                'internal medicine', 'internist'
            ]
            
            # Common service categories
            self.service_categories = [
                'consultation', 'checkup', 'check-up',
                'surgery', 'operation',
                'x-ray', 'xray', 'imaging', 'mri', 'ct scan', 'ultrasound',
                'blood test', 'lab test', 'laboratory',
                'vaccination', 'vaccine', 'immunization',
                'physical therapy', 'physiotherapy',
                'dental', 'teeth cleaning',
                'eye exam', 'vision test',
                'screening', 'scan',
                'emergency', 'urgent care'
            ]
            
            # Time-related keywords
            self.time_keywords = {
                'morning': ['morning', 'am', 'early'],
                'afternoon': ['afternoon', 'pm', 'lunch'],
                'evening': ['evening', 'night', 'late'],
                'weekend': ['weekend', 'saturday', 'sunday'],
                'weekday': ['weekday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday'],
                'urgent': ['urgent', 'emergency', 'asap', 'immediately', 'today'],
                'soon': ['soon', 'tomorrow', 'this week']
            }
            
            # Language preferences
            self.languages = [
                'english', 'spanish', 'mandarin', 'chinese', 'french',
                'german', 'arabic', 'hindi', 'portuguese', 'russian',
                'japanese', 'korean', 'italian', 'vietnamese', 'polish'
            ]
            
        except Exception as e:
            logger.error(f"Failed to initialize EntityExtractor: {e}")
            raise
    
    def _setup_patterns(self):
        """Setup SpaCy matcher patterns"""
        
        # Pattern for doctor names (Dr. LastName or Dr. FirstName LastName)
        doctor_pattern = [
            {"LOWER": {"IN": ["dr", "dr.", "doctor"]}},
            {"POS": "PROPN"},
            {"POS": "PROPN", "OP": "?"}
        ]
        self.matcher.add("DOCTOR_NAME", [doctor_pattern])
        
        # Pattern for appointment requests
        appointment_pattern = [
            {"LOWER": {"IN": ["book", "schedule", "make", "need", "want"]}},
            {"LOWER": {"IN": ["an", "a"]}, "OP": "?"},
            {"LOWER": {"IN": ["appointment", "booking", "consultation", "visit"]}}
        ]
        self.matcher.add("APPOINTMENT_REQUEST", [appointment_pattern])
        
        # Pattern for time expressions
        time_pattern = [
            {"LOWER": {"IN": ["next", "this", "tomorrow"]}},
            {"LOWER": {"IN": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "week", "month"]}}
        ]
        self.matcher.add("TIME_EXPRESSION", [time_pattern])
    
    async def extract(self, query: str) -> Dict[str, Any]:
        """Extract entities from query"""
        
        entities = {}
        query_lower = query.lower()
        
        try:
            # Process with SpaCy if available
            doc = self.nlp(query)
            
            # Extract named entities
            for ent in doc.ents:
                if ent.label_ == "PERSON":
                    # Check if it's a doctor name
                    if any(title in query_lower for title in ['dr', 'doctor']):
                        entities['doctor_name'] = ent.text
                elif ent.label_ == "DATE":
                    entities['date'] = ent.text
                elif ent.label_ == "TIME":
                    entities['time'] = ent.text
                elif ent.label_ == "ORG":
                    # Could be a clinic or service name
                    entities['organization'] = ent.text
            
            # Use matcher patterns
            matches = self.matcher(doc)
            for match_id, start, end in matches:
                span = doc[start:end]
                match_label = self.nlp.vocab.strings[match_id]
                
                if match_label == "DOCTOR_NAME":
                    # Extract the doctor name (skip the "Dr." part)
                    name_tokens = [token.text for token in span[1:]]
                    if name_tokens:
                        entities['doctor_name'] = ' '.join(name_tokens)
                elif match_label == "APPOINTMENT_REQUEST":
                    entities['intent'] = 'appointment'
                elif match_label == "TIME_EXPRESSION":
                    entities['time_expression'] = span.text
            
            # Extract specialization
            for spec in self.specializations:
                if spec in query_lower:
                    entities['specialization'] = spec
                    break
            
            # Extract service category
            for service in self.service_categories:
                if service in query_lower:
                    entities['service_category'] = service
                    break
            
            # Extract time preferences
            for time_type, keywords in self.time_keywords.items():
                if any(keyword in query_lower for keyword in keywords):
                    entities['time_preference'] = time_type
                    break
            
            # Extract language preference
            for language in self.languages:
                if language in query_lower:
                    entities['language'] = language
                    break
            
            # Extract dates using regex patterns
            date_patterns = [
                r'\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b',  # MM/DD/YYYY or MM-DD-YYYY
                r'\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})(,?\s+\d{4})?\b',
                r'\b(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december)(\s+\d{4})?\b',
            ]
            
            for pattern in date_patterns:
                match = re.search(pattern, query_lower)
                if match:
                    entities['date'] = match.group()
                    break
            
            # Extract relative dates
            relative_dates = {
                'today': 0,
                'tomorrow': 1,
                'day after tomorrow': 2,
                'next week': 7,
                'next month': 30
            }
            
            for relative_date, days_offset in relative_dates.items():
                if relative_date in query_lower:
                    target_date = datetime.now() + timedelta(days=days_offset)
                    entities['date'] = target_date.strftime('%Y-%m-%d')
                    entities['date_type'] = 'relative'
                    break
            
            # Extract insurance related queries
            insurance_keywords = ['insurance', 'coverage', 'copay', 'deductible', 'in-network', 'out-of-network']
            if any(keyword in query_lower for keyword in insurance_keywords):
                entities['insurance_query'] = True
            
            # Extract cost/price queries
            cost_keywords = ['cost', 'price', 'fee', 'charge', 'expensive', 'cheap', 'afford']
            if any(keyword in query_lower for keyword in cost_keywords):
                entities['cost_query'] = True
            
            # Extract availability queries
            availability_keywords = ['available', 'availability', 'opening', 'slot', 'free']
            if any(keyword in query_lower for keyword in availability_keywords):
                entities['availability_query'] = True
            
            # Extract patient type
            if 'new patient' in query_lower:
                entities['patient_type'] = 'new'
            elif 'existing patient' in query_lower or 'returning patient' in query_lower:
                entities['patient_type'] = 'existing'
            
            # Extract urgency level
            if any(word in query_lower for word in ['emergency', 'urgent', 'immediately', 'asap']):
                entities['urgency'] = 'high'
            elif any(word in query_lower for word in ['soon', 'quickly', 'today']):
                entities['urgency'] = 'medium'
            else:
                entities['urgency'] = 'normal'
            
            # Extract gender preferences (for doctor)
            if 'female doctor' in query_lower or 'woman doctor' in query_lower:
                entities['doctor_gender_preference'] = 'female'
            elif 'male doctor' in query_lower or 'man doctor' in query_lower:
                entities['doctor_gender_preference'] = 'male'
            
            # Extract location references
            location_keywords = ['near', 'nearby', 'close', 'location', 'address', 'where']
            if any(keyword in query_lower for keyword in location_keywords):
                entities['location_query'] = True
            
            # Extract specific medical procedures
            procedure_keywords = [
                'blood pressure', 'bp check',
                'cholesterol', 'diabetes test', 'glucose test',
                'ecg', 'ekg', 'electrocardiogram',
                'pregnancy test', 'prenatal',
                'pap smear', 'mammogram',
                'colonoscopy', 'endoscopy',
                'biopsy', 'allergy test'
            ]
            
            for procedure in procedure_keywords:
                if procedure in query_lower:
                    entities['procedure'] = procedure
                    break
            
            # Extract symptoms if mentioned
            symptom_keywords = [
                'pain', 'ache', 'fever', 'cough', 'cold',
                'headache', 'dizziness', 'nausea', 'vomiting',
                'rash', 'itching', 'swelling', 'bleeding',
                'fatigue', 'tired', 'weak', 'shortness of breath'
            ]
            
            symptoms = [symptom for symptom in symptom_keywords if symptom in query_lower]
            if symptoms:
                entities['symptoms'] = symptoms
            
            # Extract medication references
            if any(word in query_lower for word in ['prescription', 'medication', 'medicine', 'drug', 'refill']):
                entities['medication_query'] = True
            
        except Exception as e:
            logger.error(f"Error extracting entities: {e}")
            # Return basic extraction on error
            entities = self._basic_extraction(query)
        
        return entities
    
    def _basic_extraction(self, query: str) -> Dict[str, Any]:
        """Fallback basic extraction without SpaCy"""
        
        entities = {}
        query_lower = query.lower()
        
        # Basic doctor name extraction
        dr_match = re.search(r'\b(?:dr\.?|doctor)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)', query, re.IGNORECASE)
        if dr_match:
            entities['doctor_name'] = dr_match.group(1)
        
        # Basic specialization extraction
        for spec in self.specializations:
            if spec in query_lower:
                entities['specialization'] = spec
                break
        
        # Basic service extraction
        for service in self.service_categories:
            if service in query_lower:
                entities['service_category'] = service
                break
        
        # Basic time preference
        for time_type, keywords in self.time_keywords.items():
            if any(keyword in query_lower for keyword in keywords):
                entities['time_preference'] = time_type
                break
        
        # Basic urgency detection
        if any(word in query_lower for word in ['emergency', 'urgent', 'asap']):
            entities['urgency'] = 'high'
        
        return entities


class MedicalEntityExtractor(EntityExtractor):
    """Specialized entity extractor for medical domain with enhanced capabilities"""
    
    def __init__(self):
        super().__init__()
        
        # Additional medical-specific patterns
        self.medical_conditions = [
            'diabetes', 'hypertension', 'high blood pressure',
            'asthma', 'allergy', 'arthritis',
            'depression', 'anxiety', 'migraine',
            'covid', 'flu', 'cold', 'infection'
        ]
        
        self.body_parts = [
            'head', 'neck', 'shoulder', 'arm', 'elbow', 'wrist', 'hand',
            'chest', 'back', 'spine', 'abdomen', 'stomach',
            'hip', 'leg', 'knee', 'ankle', 'foot',
            'eye', 'ear', 'nose', 'throat', 'teeth', 'mouth'
        ]
    
    async def extract_medical_context(self, query: str) -> Dict[str, Any]:
        """Extract medical-specific context from query"""
        
        # Get base entities
        entities = await self.extract(query)
        query_lower = query.lower()
        
        # Extract medical conditions
        conditions = [cond for cond in self.medical_conditions if cond in query_lower]
        if conditions:
            entities['medical_conditions'] = conditions
        
        # Extract body parts
        parts = [part for part in self.body_parts if part in query_lower]
        if parts:
            entities['body_parts'] = parts
        
        # Determine consultation type based on extracted info
        if entities.get('symptoms') or entities.get('body_parts'):
            entities['consultation_type'] = 'symptom-based'
        elif entities.get('procedure'):
            entities['consultation_type'] = 'procedure-based'
        elif entities.get('medical_conditions'):
            entities['consultation_type'] = 'condition-management'
        elif entities.get('specialization'):
            entities['consultation_type'] = 'specialist-consultation'
        else:
            entities['consultation_type'] = 'general-consultation'
        
        return entities