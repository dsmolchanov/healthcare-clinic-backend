"""
Structured Data Embedder for Healthcare RAG System

This module embeds structured data from doctors and services tables into Pinecone
for hybrid retrieval in the enhanced RAG system.
"""

import os
import hashlib
from typing import List, Dict, Any, Optional
from datetime import datetime
from pinecone import Pinecone, ServerlessSpec
from openai import OpenAI
from supabase import Client
import logging

logger = logging.getLogger(__name__)


class StructuredDataEmbedder:
    """Embeds structured data from doctors and services tables into Pinecone"""

    def __init__(self, clinic_id: str, supabase: Client):
        self.clinic_id = clinic_id
        self.supabase = supabase
        self.openai = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))

        # Create safe index name
        safe_clinic_id = clinic_id[:8].replace('_', '-').replace(' ', '-').lower()
        self.index_name = f"clinic-{safe_clinic_id}-kb"

        self._init_pinecone()

    def _init_pinecone(self):
        """Initialize Pinecone index"""
        try:
            # Initialize Pinecone with API key
            pc = Pinecone(api_key=os.environ.get('PINECONE_API_KEY'))

            # Check if index exists, create if not
            existing_indexes = [index.name for index in pc.list_indexes()]

            if self.index_name not in existing_indexes:
                logger.info(f"Creating Pinecone index: {self.index_name}")
                pc.create_index(
                    name=self.index_name,
                    dimension=1536,  # OpenAI text-embedding-3-small dimension
                    metric='cosine',
                    spec=ServerlessSpec(
                        cloud='aws',
                        region='us-east-1'
                    )
                )

            # Get index reference
            self.index = pc.Index(self.index_name)
            logger.info(f"Connected to Pinecone index: {self.index_name}")

        except Exception as e:
            logger.error(f"Failed to initialize Pinecone: {e}")
            raise

    async def embed_doctors(self) -> Dict[str, Any]:
        """Embed all doctors for the clinic"""
        try:
            # Fetch doctors from database
            result = self.supabase.table('doctors').select('*').eq(
                'clinic_id', self.clinic_id
            ).eq('active', True).execute()

            if not result.data:
                logger.warning(f"No active doctors found for clinic {self.clinic_id}")
                return {
                    'indexed_count': 0,
                    'type': 'doctors'
                }

            vectors = []
            for doctor in result.data:
                # Create searchable text representation
                search_text = self._create_doctor_search_text(doctor)

                # Generate embedding
                embedding_response = self.openai.embeddings.create(
                    model="text-embedding-3-small",
                    input=search_text
                )
                embedding = embedding_response.data[0].embedding

                # Create vector with structured metadata
                vector_id = f"doctor_{doctor['id']}"

                # Build metadata (Pinecone has restrictions on metadata)
                metadata = {
                    'type': 'doctor',
                    'doctor_id': str(doctor['id']),
                    'clinic_id': str(self.clinic_id),
                    'name': f"{doctor.get('title', 'Dr.')} {doctor['first_name']} {doctor['last_name']}",
                    'specialization': doctor.get('specialization', 'General Practice'),
                    'text': search_text[:1000],  # Preview for display
                    'category': 'providers',
                    'indexed_at': datetime.utcnow().isoformat()
                }

                # Add optional fields if they exist
                if doctor.get('sub_specialties'):
                    metadata['sub_specialties'] = ', '.join(doctor['sub_specialties'][:5])  # Limit for metadata size

                if doctor.get('available_days'):
                    metadata['available_days'] = ', '.join(doctor['available_days'])

                if doctor.get('languages'):
                    metadata['languages'] = ', '.join(doctor.get('languages', ['English'])[:5])
                else:
                    metadata['languages'] = 'English'

                metadata['accepts_new_patients'] = doctor.get('accepts_new_patients', True)

                vectors.append({
                    'id': vector_id,
                    'values': embedding,
                    'metadata': metadata
                })

            # Batch upsert to Pinecone
            if vectors:
                # Upsert in batches of 100
                batch_size = 100
                for i in range(0, len(vectors), batch_size):
                    batch = vectors[i:i + batch_size]
                    self.index.upsert(vectors=batch)

                logger.info(f"Indexed {len(vectors)} doctors for clinic {self.clinic_id}")

            return {
                'indexed_count': len(vectors),
                'type': 'doctors'
            }

        except Exception as e:
            logger.error(f"Error embedding doctors: {e}")
            return {
                'indexed_count': 0,
                'type': 'doctors',
                'error': str(e)
            }

    async def embed_services(self) -> Dict[str, Any]:
        """Embed all services for the clinic"""
        try:
            # Fetch services from database
            result = self.supabase.table('services').select('*').eq(
                'clinic_id', self.clinic_id
            ).eq('active', True).execute()

            if not result.data:
                logger.warning(f"No active services found for clinic {self.clinic_id}")
                return {
                    'indexed_count': 0,
                    'type': 'services'
                }

            vectors = []
            for service in result.data:
                # Create searchable text representation
                search_text = self._create_service_search_text(service)

                # Generate embedding
                embedding_response = self.openai.embeddings.create(
                    model="text-embedding-3-small",
                    input=search_text
                )
                embedding = embedding_response.data[0].embedding

                # Create vector with structured metadata
                vector_id = f"service_{service['id']}"

                # Build metadata
                metadata = {
                    'type': 'service',
                    'service_id': str(service['id']),
                    'clinic_id': str(self.clinic_id),
                    'name': service['name'],
                    'category': service.get('category', 'general'),
                    'duration_minutes': service.get('duration_minutes', 30),
                    'text': search_text[:1000],
                    'indexed_at': datetime.utcnow().isoformat()
                }

                # Add price if available
                if service.get('base_price') is not None:
                    metadata['base_price'] = float(service['base_price'])

                metadata['insurance_covered'] = service.get('insurance_covered', True)
                metadata['requires_referral'] = service.get('requires_referral', False)

                vectors.append({
                    'id': vector_id,
                    'values': embedding,
                    'metadata': metadata
                })

            # Batch upsert to Pinecone
            if vectors:
                # Upsert in batches of 100
                batch_size = 100
                for i in range(0, len(vectors), batch_size):
                    batch = vectors[i:i + batch_size]
                    self.index.upsert(vectors=batch)

                logger.info(f"Indexed {len(vectors)} services for clinic {self.clinic_id}")

            return {
                'indexed_count': len(vectors),
                'type': 'services'
            }

        except Exception as e:
            logger.error(f"Error embedding services: {e}")
            return {
                'indexed_count': 0,
                'type': 'services',
                'error': str(e)
            }

    def _create_doctor_search_text(self, doctor: Dict) -> str:
        """Create searchable text representation of doctor"""
        parts = []

        # Name and title
        title = doctor.get('title', 'Dr.')
        parts.append(f"{title} {doctor['first_name']} {doctor['last_name']}")

        # Specialization
        if doctor.get('specialization'):
            parts.append(f"Specialization: {doctor['specialization']}")

        # Sub-specialties
        if doctor.get('sub_specialties') and doctor['sub_specialties']:
            parts.append(f"Sub-specialties: {', '.join(doctor['sub_specialties'])}")

        # Biography or description
        if doctor.get('bio'):
            parts.append(f"Biography: {doctor['bio']}")
        elif doctor.get('description'):
            parts.append(f"Description: {doctor['description']}")

        # Availability
        if doctor.get('available_days') and doctor['available_days']:
            parts.append(f"Available: {', '.join(doctor['available_days'])}")

        # Languages
        if doctor.get('languages') and doctor['languages']:
            parts.append(f"Languages: {', '.join(doctor['languages'])}")

        # Qualifications
        if doctor.get('qualifications'):
            parts.append(f"Qualifications: {doctor['qualifications']}")

        # Experience
        if doctor.get('years_of_experience'):
            parts.append(f"Experience: {doctor['years_of_experience']} years")

        # Education
        if doctor.get('education'):
            parts.append(f"Education: {doctor['education']}")

        # Accepting new patients
        if doctor.get('accepts_new_patients') is not None:
            status = "accepting new patients" if doctor['accepts_new_patients'] else "not accepting new patients"
            parts.append(f"Currently {status}")

        return " ".join(parts)

    def _create_service_search_text(self, service: Dict) -> str:
        """Create searchable text representation of service"""
        parts = []

        # Service name
        parts.append(f"Service: {service['name']}")

        # Category
        if service.get('category'):
            parts.append(f"Category: {service['category']}")

        # Description
        if service.get('description'):
            parts.append(f"Description: {service['description']}")

        # Duration
        if service.get('duration_minutes'):
            parts.append(f"Duration: {service['duration_minutes']} minutes")

        # Price
        if service.get('base_price') is not None:
            parts.append(f"Price: ${service['base_price']}")

        # Insurance
        if service.get('insurance_covered') is not None:
            coverage = "covered by insurance" if service['insurance_covered'] else "not covered by insurance"
            parts.append(f"Insurance: {coverage}")

        # Referral requirement
        if service.get('requires_referral') is not None:
            referral = "requires referral" if service['requires_referral'] else "no referral needed"
            parts.append(f"Referral: {referral}")

        # Preparation instructions
        if service.get('preparation_instructions'):
            parts.append(f"Preparation: {service['preparation_instructions']}")

        # Code (CPT, ICD, etc.)
        if service.get('code'):
            parts.append(f"Code: {service['code']}")

        # Age restrictions
        if service.get('minimum_age'):
            parts.append(f"Minimum age: {service['minimum_age']}")
        if service.get('maximum_age'):
            parts.append(f"Maximum age: {service['maximum_age']}")

        # Gender specific
        if service.get('gender_specific'):
            parts.append(f"Gender specific: {service['gender_specific']}")

        # Requires fasting
        if service.get('requires_fasting'):
            parts.append("Requires fasting")

        return " ".join(parts)

    async def delete_structured_data(self, data_type: str = 'all') -> Dict[str, Any]:
        """Delete structured data vectors from Pinecone"""
        try:
            if data_type == 'all':
                prefixes = ['doctor_', 'service_']
            elif data_type == 'doctors':
                prefixes = ['doctor_']
            elif data_type == 'services':
                prefixes = ['service_']
            else:
                return {'deleted_count': 0, 'error': 'Invalid data type'}

            deleted_count = 0
            for prefix in prefixes:
                # Note: Pinecone doesn't support prefix deletion directly
                # You would need to query for IDs first and then delete
                # This is a simplified version
                logger.info(f"Would delete vectors with prefix: {prefix}")
                # In production, you'd need to implement proper deletion logic

            return {
                'deleted_count': deleted_count,
                'type': data_type
            }

        except Exception as e:
            logger.error(f"Error deleting structured data: {e}")
            return {
                'deleted_count': 0,
                'error': str(e)
            }