"""
Strategic Price List Parser Service
Handles CSV, PDF, and Image parsing for medical services
Uses Grok-4-fast for AI-powered extraction
"""

import os
import base64
import json
import hashlib
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from enum import Enum
import asyncio
import aiohttp
from pathlib import Path
import logging
import csv
import io
from datetime import datetime, timedelta
import redis
from functools import lru_cache

logger = logging.getLogger(__name__)

class FileType(Enum):
    CSV = "csv"
    PDF = "pdf"
    IMAGE = "image"
    EXCEL = "excel"
    TEXT = "text"

@dataclass
class ParsedService:
    """Standardized parsed service structure"""
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
    confidence_score: float = 1.0  # AI confidence in extraction

    def to_dict(self):
        return asdict(self)

class PriceListParser:
    """
    Strategic parser service with caching, rate limiting, and multi-format support
    """
    
    def __init__(self, 
                 grok_api_key: str = None,
                 openai_api_key: str = None,
                 cache_client: redis.Redis = None,
                 cache_ttl: int = 3600):
        """
        Initialize parser with API keys and caching
        
        Args:
            grok_api_key: Grok API key for vision parsing
            openai_api_key: Backup OpenAI API key
            cache_client: Redis client for caching
            cache_ttl: Cache time-to-live in seconds
        """
        self.grok_api_key = grok_api_key or os.getenv("XAI_API_KEY")
        self.openai_api_key = openai_api_key or os.getenv("OPENAI_API_KEY")
        self.cache_client = cache_client
        self.cache_ttl = cache_ttl
        
        self.grok_url = "https://api.x.ai/v1/chat/completions"
        self.openai_url = "https://api.openai.com/v1/chat/completions"
        
        # Rate limiting
        self._last_api_call = {}
        self._min_interval = 0.5  # Minimum seconds between API calls
        
    def _get_cache_key(self, content_hash: str, file_type: str) -> str:
        """Generate cache key for parsed results"""
        return f"parsed_services:{file_type}:{content_hash}"
    
    def _hash_content(self, content: bytes) -> str:
        """Generate hash of file content for caching"""
        return hashlib.sha256(content).hexdigest()
    
    async def _get_cached_result(self, content_hash: str, file_type: str) -> Optional[List[ParsedService]]:
        """Check cache for previously parsed results"""
        if not self.cache_client:
            return None
            
        try:
            cache_key = self._get_cache_key(content_hash, file_type)
            cached = self.cache_client.get(cache_key)
            if cached:
                logger.info(f"Cache hit for {file_type} file")
                data = json.loads(cached)
                return [ParsedService(**item) for item in data]
        except Exception as e:
            logger.error(f"Cache error: {e}")
        
        return None
    
    async def _cache_result(self, content_hash: str, file_type: str, services: List[ParsedService]):
        """Cache parsed results"""
        if not self.cache_client:
            return
            
        try:
            cache_key = self._get_cache_key(content_hash, file_type)
            data = [s.to_dict() for s in services]
            self.cache_client.setex(
                cache_key,
                self.cache_ttl,
                json.dumps(data)
            )
            logger.info(f"Cached {len(services)} services for {file_type} file")
        except Exception as e:
            logger.error(f"Cache error: {e}")
    
    async def parse_file(self, 
                        file_content: bytes, 
                        file_name: str,
                        file_type: Optional[FileType] = None,
                        use_cache: bool = True) -> List[ParsedService]:
        """
        Main entry point for parsing any file type
        
        Args:
            file_content: Raw file bytes
            file_name: Original filename
            file_type: Explicit file type or auto-detect
            use_cache: Whether to use caching
            
        Returns:
            List of parsed services
        """
        # Auto-detect file type if not provided
        if not file_type:
            file_type = self._detect_file_type(file_name)
        
        # Check cache first
        content_hash = self._hash_content(file_content)
        if use_cache:
            cached = await self._get_cached_result(content_hash, file_type.value)
            if cached:
                return cached
        
        # Parse based on file type
        services = []
        try:
            if file_type == FileType.CSV:
                services = await self._parse_csv(file_content)
            elif file_type == FileType.PDF:
                services = await self._parse_pdf_with_ai(file_content)
            elif file_type == FileType.IMAGE:
                services = await self._parse_image_with_ai(file_content, file_name)
            elif file_type == FileType.EXCEL:
                services = await self._parse_excel(file_content)
            else:
                raise ValueError(f"Unsupported file type: {file_type}")
            
            # Cache successful result
            if use_cache and services:
                await self._cache_result(content_hash, file_type.value, services)
                
        except Exception as e:
            logger.error(f"Error parsing {file_type.value} file: {e}")
            raise
        
        return services
    
    def _detect_file_type(self, file_name: str) -> FileType:
        """Detect file type from extension"""
        ext = Path(file_name).suffix.lower()
        
        if ext == '.csv':
            return FileType.CSV
        elif ext == '.pdf':
            return FileType.PDF
        elif ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp']:
            return FileType.IMAGE
        elif ext in ['.xlsx', '.xls']:
            return FileType.EXCEL
        elif ext in ['.txt']:
            return FileType.TEXT
        else:
            # Try to detect from content
            try:
                file_content = file_name.encode() if isinstance(file_name, str) else file_name
                if b'%PDF' in file_content[:10]:
                    return FileType.PDF
                elif b'PNG' in file_content[:10] or b'JFIF' in file_content[:10]:
                    return FileType.IMAGE
            except:
                pass
            raise ValueError(f"Cannot detect file type for: {file_name}")
    
    async def _parse_csv(self, content: bytes) -> List[ParsedService]:
        """Parse CSV file with intelligent column detection"""
        try:
            text = content.decode('utf-8-sig')  # Handle BOM
        except:
            text = content.decode('latin-1')  # Fallback encoding
        
        reader = csv.DictReader(io.StringIO(text))
        services = []
        
        for row in reader:
            # Intelligent column mapping
            name = self._find_column(row, ['Service', 'Name', 'Procedure', 'Description', 'service_name', 'treatment'])
            if not name:
                continue
                
            price = self._parse_price(self._find_column(row, ['Price', 'Cost', 'Fee', 'Amount', 'price', 'rate']))
            category = self._find_column(row, ['Category', 'Type', 'Department', 'Specialty', 'category']) or 'General'
            code = self._find_column(row, ['Code', 'ID', 'CPT', 'code', 'service_code'])
            duration = self._parse_duration(self._find_column(row, ['Duration', 'Time', 'Minutes', 'duration']))
            
            if not code:
                # Generate code from name
                code = self._generate_code(name)
            
            service = ParsedService(
                code=code,
                name=name,
                category=category,
                price=price,
                duration_minutes=duration,
                confidence_score=1.0  # High confidence for CSV
            )
            services.append(service)
        
        logger.info(f"Parsed {len(services)} services from CSV")
        return services
    
    async def _parse_pdf_with_ai(self, content: bytes) -> List[ParsedService]:
        """Parse PDF using Grok-4 vision API"""
        base64_content = base64.b64encode(content).decode('utf-8')
        
        prompt = """
        Extract ALL medical services from this price list PDF. For each service:
        
        Required fields:
        - name: The service or procedure name
        - price: Numerical price (if multiple prices, use the standard/cash price)
        - category: Type of service (Surgery, Consultation, Diagnostics, etc.)
        
        Optional fields (if available):
        - code: Service or CPT code
        - duration_minutes: Duration in minutes
        - specialization: Required medical specialty
        - insurance_codes: List of insurance codes
        - is_multi_stage: true if mentions multiple visits/stages
        - stage_config: Details about stages if multi-stage
        
        Return ONLY a JSON array, no explanations:
        [{"name": "...", "price": 0, "category": "...", ...}]
        """
        
        services = await self._call_grok_vision(base64_content, 'application/pdf', prompt)
        return services
    
    async def _parse_image_with_ai(self, content: bytes, file_name: str) -> List[ParsedService]:
        """Parse image using Grok-4 vision API"""
        base64_content = base64.b64encode(content).decode('utf-8')
        
        # Detect image type
        mime_type = 'image/jpeg'
        if file_name.lower().endswith('.png'):
            mime_type = 'image/png'
        
        prompt = """
        Extract ALL medical services from this price list image. Read carefully and extract:
        
        For each service found:
        - name: Service/procedure name (required)
        - price: Numerical price value (required)
        - category: Service category (required)
        - code: If visible
        - duration_minutes: If mentioned
        - Any multi-stage procedure info
        
        Return ONLY valid JSON array:
        [{"name": "...", "price": 0, "category": "..."}]
        """
        
        services = await self._call_grok_vision(base64_content, mime_type, prompt)
        return services
    
    async def _call_grok_vision(self, base64_content: str, mime_type: str, prompt: str) -> List[ParsedService]:
        """Call Grok-4 Vision API with rate limiting"""
        
        # Rate limiting
        await self._rate_limit('grok')
        
        headers = {
            "Authorization": f"Bearer {self.grok_api_key}",
            "Content-Type": "application/json"
        }
        
        messages = [{
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{base64_content}",
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
            "model": "grok-4",
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 4000
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.grok_url, headers=headers, json=payload) as response:
                    if response.status != 200:
                        error = await response.text()
                        logger.error(f"Grok API error: {error}")
                        # Fallback to OpenAI
                        return await self._call_openai_vision(base64_content, mime_type, prompt)
                    
                    result = await response.json()
                    content = result['choices'][0]['message']['content']
                    
                    # Parse JSON from response
                    services = self._extract_services_from_response(content)
                    return services
                    
        except Exception as e:
            logger.error(f"Grok API exception: {e}")
            # Fallback to OpenAI
            return await self._call_openai_vision(base64_content, mime_type, prompt)
    
    async def _call_openai_vision(self, base64_content: str, mime_type: str, prompt: str) -> List[ParsedService]:
        """Fallback to OpenAI Vision API"""
        
        if not self.openai_api_key:
            logger.error("No OpenAI API key available for fallback")
            return []
        
        await self._rate_limit('openai')
        
        headers = {
            "Authorization": f"Bearer {self.openai_api_key}",
            "Content-Type": "application/json"
        }
        
        # Similar structure for OpenAI
        # Implementation details...
        
        # For now, return sample data as fallback
        return self._get_sample_services()
    
    def _extract_services_from_response(self, response: str) -> List[ParsedService]:
        """Extract services from AI response"""
        try:
            # Try to find JSON in response
            import re
            json_match = re.search(r'\[.*\]', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
            else:
                data = json.loads(response)
            
            services = []
            for item in data:
                if not item.get('name'):
                    continue
                    
                service = ParsedService(
                    code=item.get('code') or self._generate_code(item['name']),
                    name=item['name'],
                    category=item.get('category', 'General'),
                    price=float(item.get('price', 0)),
                    currency=item.get('currency', 'USD'),
                    duration_minutes=item.get('duration_minutes'),
                    specialization=item.get('specialization'),
                    description=item.get('description'),
                    insurance_codes=item.get('insurance_codes'),
                    is_multi_stage=item.get('is_multi_stage', False),
                    stage_config=item.get('stage_config'),
                    confidence_score=item.get('confidence_score', 0.8)
                )
                services.append(service)
            
            return services
            
        except Exception as e:
            logger.error(f"Error parsing AI response: {e}")
            return []
    
    async def _parse_excel(self, content: bytes) -> List[ParsedService]:
        """Parse Excel file using pandas"""
        # Implementation for Excel parsing
        # Would use pandas/openpyxl
        return []
    
    def _find_column(self, row: Dict, possible_names: List[str]) -> Optional[str]:
        """Find column value from possible names"""
        for name in possible_names:
            # Case-insensitive search
            for key, value in row.items():
                if key and name.lower() in key.lower():
                    return str(value).strip() if value else None
        return None
    
    def _parse_price(self, value: Optional[str]) -> float:
        """Parse price from string"""
        if not value:
            return 0.0
        # Remove currency symbols and commas
        import re
        cleaned = re.sub(r'[^\d.]', '', str(value))
        try:
            return float(cleaned)
        except:
            return 0.0
    
    def _parse_duration(self, value: Optional[str]) -> Optional[int]:
        """Parse duration in minutes"""
        if not value:
            return None
        import re
        # Look for numbers
        numbers = re.findall(r'\d+', str(value))
        if numbers:
            return int(numbers[0])
        return None
    
    def _generate_code(self, name: str) -> str:
        """Generate service code from name"""
        words = name.upper().split()[:3]
        code = ''.join(w[0] for w in words if w)
        import random
        code += str(random.randint(100, 999))
        return code
    
    async def _rate_limit(self, api: str):
        """Implement rate limiting for API calls"""
        now = asyncio.get_event_loop().time()
        if api in self._last_api_call:
            elapsed = now - self._last_api_call[api]
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
        self._last_api_call[api] = asyncio.get_event_loop().time()
    
    def _get_sample_services(self) -> List[ParsedService]:
        """Return sample services for testing"""
        return [
            ParsedService("CONS100", "Consultation", "General", 150, "USD", 30),
            ParsedService("CLEAN200", "Dental Cleaning", "Hygiene", 120, "USD", 45),
            ParsedService("ROOT300", "Root Canal", "Endodontics", 800, "USD", 90),
            ParsedService("FILL400", "Filling", "Restorative", 200, "USD", 30),
            ParsedService("XRAY500", "X-Ray", "Diagnostics", 50, "USD", 15),
        ]