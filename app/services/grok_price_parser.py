"""
Grok-4-fast based price list parser for medical services
Supports PDF, images, and CSV files
"""

import os
import base64
import json
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum
import asyncio
import aiohttp
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

class FileType(Enum):
    CSV = "csv"
    PDF = "pdf"
    IMAGE = "image"
    EXCEL = "excel"

@dataclass
class ParsedService:
    """Parsed medical service from price list"""
    code: str
    name: str
    category: str
    price: float
    currency: str = "USD"
    duration_minutes: Optional[int] = None
    specialization: Optional[str] = None
    description: Optional[str] = None
    insurance_codes: Optional[List[str]] = None
    is_multi_stage: bool = False
    stage_config: Optional[Dict] = None

class GrokPriceListParser:
    """Parse medical price lists using Grok-4-fast vision model"""
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("XAI_API_KEY")
        if not self.api_key:
            raise ValueError("XAI_API_KEY is required for Grok parser")
        
        self.api_url = "https://api.x.ai/v1/chat/completions"
        self.model = "grok-4-fast"
        
    async def parse_file(self, file_path: str, file_type: FileType = None) -> List[ParsedService]:
        """Parse a price list file and extract services"""
        
        if not file_type:
            file_type = self._detect_file_type(file_path)
        
        if file_type == FileType.CSV:
            return await self._parse_csv(file_path)
        elif file_type in [FileType.PDF, FileType.IMAGE]:
            return await self._parse_visual(file_path, file_type)
        else:
            raise ValueError(f"Unsupported file type: {file_type}")
    
    def _detect_file_type(self, file_path: str) -> FileType:
        """Detect file type from extension"""
        ext = Path(file_path).suffix.lower()
        if ext == '.csv':
            return FileType.CSV
        elif ext == '.pdf':
            return FileType.PDF
        elif ext in ['.jpg', '.jpeg', '.png']:
            return FileType.IMAGE
        elif ext in ['.xlsx', '.xls']:
            return FileType.EXCEL
        else:
            raise ValueError(f"Unknown file extension: {ext}")
    
    async def _parse_csv(self, file_path: str) -> List[ParsedService]:
        """Parse CSV file using Grok for intelligent extraction"""
        with open(file_path, 'r', encoding='utf-8') as f:
            csv_content = f.read()
        
        prompt = """
        Extract medical services from this CSV price list. For each service, identify:
        - Code (service/procedure code)
        - Name (service name)
        - Category (type of service: Surgery, Endodontics, Consultation, etc.)
        - Price (numerical value)
        - Duration (if mentioned, in minutes)
        - Specialization required (if applicable)
        - Multi-stage procedures (if mentioned, e.g., "2 steps/8 days each")
        
        Return as JSON array with structure:
        [{
            "code": "string",
            "name": "string", 
            "category": "string",
            "price": number,
            "currency": "USD",
            "duration_minutes": number or null,
            "specialization": "string or null",
            "is_multi_stage": boolean,
            "stage_config": null or {"total_stages": number, "stages": [...]}
        }]
        
        CSV Content:
        """ + csv_content
        
        response = await self._call_grok_api(prompt)
        return self._parse_response(response)
    
    async def _parse_visual(self, file_path: str, file_type: FileType) -> List[ParsedService]:
        """Parse PDF or image file using Grok vision capabilities"""
        
        # Encode file as base64
        with open(file_path, 'rb') as f:
            file_data = f.read()
            base64_data = base64.b64encode(file_data).decode('utf-8')
        
        # Determine MIME type
        mime_type = "image/jpeg" if file_type == FileType.IMAGE else "application/pdf"
        if file_path.endswith('.png'):
            mime_type = "image/png"
        
        prompt = """
        Extract all medical services from this price list. For each service, identify:
        - Code (service/procedure code if available)
        - Name (service/procedure name)
        - Category (Surgery, Endodontics, Consultation, Diagnostics, etc.)
        - Price (numerical value, note currency if specified)
        - Duration (if mentioned, convert to minutes)
        - Required specialization (if mentioned)
        - Multi-stage procedures (e.g., "2 appointments 8 days apart")
        - Insurance codes (if listed)
        
        Important patterns to recognize:
        - Services with multiple stages like "Initial + Follow-up"
        - Services requiring special certifications
        - Package deals or bundled services
        - Member vs non-member pricing
        
        Return as JSON array:
        [{
            "code": "string or generate from name",
            "name": "string",
            "category": "string", 
            "price": number,
            "currency": "USD or as specified",
            "duration_minutes": number or null,
            "specialization": "string or null",
            "insurance_codes": ["array of codes"] or null,
            "is_multi_stage": boolean,
            "stage_config": null or {"total_stages": number, "stages": [{"stage_number": 1, "duration_minutes": 60, "days_until_next": 8}]}
        }]
        """
        
        # Prepare message for Grok vision API
        messages = [{
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{base64_data}",
                        "detail": "high"
                    }
                },
                {
                    "type": "text",
                    "text": prompt
                }
            ]
        }]
        
        response = await self._call_grok_vision_api(messages)
        return self._parse_response(response)
    
    async def _call_grok_api(self, prompt: str) -> str:
        """Call Grok API for text processing"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You are a medical billing expert who extracts structured data from price lists."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1,
            "max_tokens": 4000
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(self.api_url, headers=headers, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"Grok API error: {response.status} - {error_text}")
                
                result = await response.json()
                return result['choices'][0]['message']['content']
    
    async def _call_grok_vision_api(self, messages: List[Dict]) -> str:
        """Call Grok API for vision processing"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "grok-4",  # Use grok-4 for vision capabilities
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 4000
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(self.api_url, headers=headers, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"Grok Vision API error: {response.status} - {error_text}")
                
                result = await response.json()
                return result['choices'][0]['message']['content']
    
    def _parse_response(self, response: str) -> List[ParsedService]:
        """Parse Grok response into ParsedService objects"""
        try:
            # Extract JSON from response (Grok might include explanation text)
            import re
            json_match = re.search(r'\[.*\]', response, re.DOTALL)
            if json_match:
                json_str = json_match.group()
                services_data = json.loads(json_str)
            else:
                # Try parsing entire response as JSON
                services_data = json.loads(response)
            
            services = []
            for item in services_data:
                # Generate code if not provided
                if not item.get('code'):
                    item['code'] = self._generate_code(item['name'])
                
                service = ParsedService(
                    code=item['code'],
                    name=item['name'],
                    category=item.get('category', 'General'),
                    price=float(item.get('price', 0)),
                    currency=item.get('currency', 'USD'),
                    duration_minutes=item.get('duration_minutes'),
                    specialization=item.get('specialization'),
                    description=item.get('description'),
                    insurance_codes=item.get('insurance_codes'),
                    is_multi_stage=item.get('is_multi_stage', False),
                    stage_config=item.get('stage_config')
                )
                services.append(service)
            
            logger.info(f"Parsed {len(services)} services from price list")
            return services
            
        except Exception as e:
            logger.error(f"Error parsing Grok response: {e}")
            logger.debug(f"Response was: {response}")
            raise
    
    def _generate_code(self, name: str) -> str:
        """Generate a service code from the name"""
        # Simple code generation: take first letters of words
        words = name.upper().split()[:3]
        code = ''.join(w[0] for w in words)
        # Add a number to make it unique
        import random
        code += str(random.randint(100, 999))
        return code
    
    async def parse_url(self, image_url: str) -> List[ParsedService]:
        """Parse price list from a web URL"""
        
        prompt = """
        Extract all medical services from this price list image. For each service:
        - Identify the service name and code
        - Determine the category (Surgery, Consultation, etc.)
        - Extract the price
        - Note any multi-stage procedures
        - Identify required specializations
        
        Return as JSON array with the structure shown before.
        """
        
        messages = [{
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": image_url,
                        "detail": "high"
                    }
                },
                {
                    "type": "text",
                    "text": prompt
                }
            ]
        }]
        
        response = await self._call_grok_vision_api(messages)
        return self._parse_response(response)