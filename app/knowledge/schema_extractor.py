"""
Schema.org and structured data extraction for clinic websites
Extracts rich structured data from JSON-LD, Microdata, and RDFa formats
"""

import json
import logging
from typing import Dict, Any, List, Optional
from bs4 import BeautifulSoup
import re

logger = logging.getLogger(__name__)


class SchemaOrgExtractor:
    """
    Extract Schema.org structured data from web pages
    Focuses on healthcare-related schemas
    """
    
    # Healthcare-related schema types we're interested in
    HEALTHCARE_SCHEMAS = [
        'MedicalOrganization',
        'Dentist', 
        'Hospital',
        'Physician',
        'MedicalClinic',
        'HealthAndBeautyBusiness',
        'LocalBusiness',
        'Organization',
        'Person',
        'MedicalProcedure',
        'MedicalCondition',
        'MedicalService',
        'OpeningHoursSpecification',
        'Review',
        'AggregateRating',
        'FAQPage',
        'Question',
        'Answer'
    ]
    
    def extract_all(self, html: str) -> Dict[str, Any]:
        """Extract all structured data from HTML"""
        soup = BeautifulSoup(html, 'html.parser')
        
        extracted = {
            'json_ld': self._extract_json_ld(soup),
            'microdata': self._extract_microdata(soup),
            'rdfa': self._extract_rdfa(soup),
            'open_graph': self._extract_open_graph(soup),
            'twitter_card': self._extract_twitter_card(soup),
            'clinic_specific': self._extract_clinic_specific(soup)
        }
        
        # Process and merge all structured data
        processed = self._process_structured_data(extracted)
        
        return processed
    
    def _extract_json_ld(self, soup: BeautifulSoup) -> List[Dict]:
        """Extract JSON-LD structured data"""
        json_ld_data = []
        
        scripts = soup.find_all('script', type='application/ld+json')
        for script in scripts:
            try:
                # Clean the JSON text
                json_text = script.string
                if json_text:
                    # Remove comments and clean up
                    json_text = re.sub(r'//.*?\n', '', json_text)
                    json_text = re.sub(r'/\*.*?\*/', '', json_text, flags=re.DOTALL)
                    
                    data = json.loads(json_text)
                    
                    # Handle @graph arrays
                    if isinstance(data, dict) and '@graph' in data:
                        for item in data['@graph']:
                            if self._is_relevant_schema(item):
                                json_ld_data.append(item)
                    elif self._is_relevant_schema(data):
                        json_ld_data.append(data)
                        
            except json.JSONDecodeError as e:
                logger.debug(f"Failed to parse JSON-LD: {e}")
            except Exception as e:
                logger.debug(f"Error extracting JSON-LD: {e}")
        
        return json_ld_data
    
    def _extract_microdata(self, soup: BeautifulSoup) -> List[Dict]:
        """Extract Microdata (itemscope, itemprop)"""
        microdata_items = []
        
        # Find all elements with itemscope
        items = soup.find_all(attrs={'itemscope': True})
        
        for item in items:
            item_type = item.get('itemtype', '')
            
            # Check if it's a relevant schema type
            if any(schema in item_type for schema in self.HEALTHCARE_SCHEMAS):
                item_data = {
                    '@type': item_type.split('/')[-1] if item_type else 'Thing',
                    'properties': {}
                }
                
                # Extract all itemprops within this scope
                props = item.find_all(attrs={'itemprop': True})
                for prop in props:
                    prop_name = prop.get('itemprop')
                    prop_value = self._get_microdata_value(prop)
                    
                    if prop_name and prop_value:
                        item_data['properties'][prop_name] = prop_value
                
                if item_data['properties']:
                    microdata_items.append(item_data)
        
        return microdata_items
    
    def _extract_rdfa(self, soup: BeautifulSoup) -> List[Dict]:
        """Extract RDFa data"""
        rdfa_items = []
        
        # Find elements with typeof attribute (RDFa)
        typed_elements = soup.find_all(attrs={'typeof': True})
        
        for element in typed_elements:
            type_value = element.get('typeof', '')
            
            # Check if it's a relevant type
            if any(schema in type_value for schema in self.HEALTHCARE_SCHEMAS):
                item_data = {
                    '@type': type_value,
                    'properties': {}
                }
                
                # Extract properties
                props = element.find_all(attrs={'property': True})
                for prop in props:
                    prop_name = prop.get('property')
                    prop_value = prop.get('content') or prop.get_text(strip=True)
                    
                    if prop_name and prop_value:
                        item_data['properties'][prop_name] = prop_value
                
                if item_data['properties']:
                    rdfa_items.append(item_data)
        
        return rdfa_items
    
    def _extract_open_graph(self, soup: BeautifulSoup) -> Dict:
        """Extract Open Graph metadata"""
        og_data = {}
        
        og_tags = soup.find_all('meta', property=re.compile(r'^og:'))
        for tag in og_tags:
            property_name = tag.get('property', '').replace('og:', '')
            content = tag.get('content', '')
            
            if property_name and content:
                og_data[property_name] = content
        
        return og_data
    
    def _extract_twitter_card(self, soup: BeautifulSoup) -> Dict:
        """Extract Twitter Card metadata"""
        twitter_data = {}
        
        twitter_tags = soup.find_all('meta', attrs={'name': re.compile(r'^twitter:')})
        for tag in twitter_tags:
            name = tag.get('name', '').replace('twitter:', '')
            content = tag.get('content', '')
            
            if name and content:
                twitter_data[name] = content
        
        return twitter_data
    
    def _extract_clinic_specific(self, soup: BeautifulSoup) -> Dict:
        """Extract clinic-specific information using patterns"""
        clinic_data = {}
        
        # Extract appointment booking links
        appointment_links = soup.find_all('a', href=re.compile(r'appointment|booking|schedule', re.I))
        if appointment_links:
            clinic_data['appointment_urls'] = list(set([
                link.get('href', '') for link in appointment_links[:5]
            ]))
        
        # Extract insurance information
        insurance_section = soup.find(string=re.compile(r'insurance|accept|coverage', re.I))
        if insurance_section:
            parent = insurance_section.parent
            if parent:
                # Look for lists of insurance providers
                insurance_list = parent.find_next(['ul', 'ol'])
                if insurance_list:
                    insurances = [li.get_text(strip=True) for li in insurance_list.find_all('li')[:20]]
                    clinic_data['accepted_insurance'] = insurances
        
        # Extract specialties
        specialties_section = soup.find(string=re.compile(r'specialt|expertise|focus', re.I))
        if specialties_section:
            parent = specialties_section.parent
            if parent:
                specialty_list = parent.find_next(['ul', 'ol'])
                if specialty_list:
                    specialties = [li.get_text(strip=True) for li in specialty_list.find_all('li')[:15]]
                    clinic_data['specialties'] = specialties
        
        # Extract certifications and affiliations
        cert_patterns = [
            r'board[\s-]certified',
            r'certified\s+in',
            r'member\s+of',
            r'affiliated\s+with',
            r'accredited\s+by'
        ]
        
        certifications = []
        for pattern in cert_patterns:
            matches = re.findall(pattern + r'[^.]+', str(soup), re.I)
            certifications.extend(matches[:5])
        
        if certifications:
            clinic_data['certifications'] = certifications
        
        # Extract technology and equipment
        tech_keywords = ['laser', 'digital', 'x-ray', '3d', 'scanner', 'imaging', 'technology']
        tech_mentions = []
        
        for keyword in tech_keywords:
            elements = soup.find_all(string=re.compile(keyword, re.I))
            for elem in elements[:3]:
                if elem.parent:
                    context = elem.parent.get_text(strip=True)[:200]
                    if context and len(context) > 20:
                        tech_mentions.append(context)
        
        if tech_mentions:
            clinic_data['technology'] = tech_mentions
        
        return clinic_data
    
    def _is_relevant_schema(self, data: Dict) -> bool:
        """Check if schema data is relevant to healthcare"""
        if not isinstance(data, dict):
            return False
        
        schema_type = data.get('@type', '')
        
        # Check if it's a healthcare-related schema
        if isinstance(schema_type, str):
            return any(schema in schema_type for schema in self.HEALTHCARE_SCHEMAS)
        elif isinstance(schema_type, list):
            return any(
                any(schema in t for schema in self.HEALTHCARE_SCHEMAS)
                for t in schema_type
            )
        
        return False
    
    def _get_microdata_value(self, element) -> Optional[str]:
        """Extract value from microdata element"""
        # Check for specific attributes first
        if element.get('content'):
            return element.get('content')
        elif element.get('href'):
            return element.get('href')
        elif element.get('src'):
            return element.get('src')
        elif element.get('datetime'):
            return element.get('datetime')
        else:
            # Fall back to text content
            return element.get_text(strip=True)
    
    def _process_structured_data(self, extracted: Dict) -> Dict[str, Any]:
        """Process and merge all extracted structured data"""
        processed = {
            'organization': {},
            'location': {},
            'contact': {},
            'hours': {},
            'services': [],
            'team': [],
            'reviews': [],
            'faqs': [],
            'insurance': [],
            'technology': [],
            'certifications': []
        }
        
        # Process JSON-LD data (usually most complete)
        for item in extracted.get('json_ld', []):
            self._process_json_ld_item(item, processed)
        
        # Process microdata
        for item in extracted.get('microdata', []):
            self._process_microdata_item(item, processed)
        
        # Add Open Graph data
        if extracted.get('open_graph'):
            og = extracted['open_graph']
            if not processed['organization'].get('name') and og.get('site_name'):
                processed['organization']['name'] = og['site_name']
            if not processed['organization'].get('description') and og.get('description'):
                processed['organization']['description'] = og['description']
            if og.get('image'):
                processed['organization']['image'] = og['image']
        
        # Add clinic-specific extractions
        if extracted.get('clinic_specific'):
            clinic = extracted['clinic_specific']
            if clinic.get('accepted_insurance'):
                processed['insurance'].extend(clinic['accepted_insurance'])
            if clinic.get('technology'):
                processed['technology'].extend(clinic['technology'])
            if clinic.get('certifications'):
                processed['certifications'].extend(clinic['certifications'])
            if clinic.get('appointment_urls'):
                processed['appointment_urls'] = clinic['appointment_urls']
        
        # Deduplicate lists
        for key in ['services', 'team', 'insurance', 'technology', 'certifications']:
            if isinstance(processed[key], list):
                processed[key] = list(set(processed[key]))[:20]  # Limit size
        
        return processed
    
    def _process_json_ld_item(self, item: Dict, processed: Dict):
        """Process a single JSON-LD item"""
        item_type = item.get('@type', '')
        
        # Handle organization/clinic data
        if any(t in item_type for t in ['Organization', 'Dentist', 'MedicalClinic', 'LocalBusiness']):
            if item.get('name'):
                processed['organization']['name'] = item['name']
            if item.get('description'):
                processed['organization']['description'] = item['description']
            if item.get('url'):
                processed['organization']['url'] = item['url']
            if item.get('logo'):
                processed['organization']['logo'] = item['logo']
            
            # Contact info
            if item.get('telephone'):
                processed['contact']['phone'] = item['telephone']
            if item.get('email'):
                processed['contact']['email'] = item['email']
            
            # Address
            if item.get('address'):
                addr = item['address']
                if isinstance(addr, dict):
                    processed['location'] = {
                        'street': addr.get('streetAddress', ''),
                        'city': addr.get('addressLocality', ''),
                        'state': addr.get('addressRegion', ''),
                        'zip': addr.get('postalCode', ''),
                        'country': addr.get('addressCountry', '')
                    }
            
            # Opening hours
            if item.get('openingHoursSpecification'):
                hours = item['openingHoursSpecification']
                if isinstance(hours, list):
                    for hour_spec in hours:
                        if isinstance(hour_spec, dict):
                            day = hour_spec.get('dayOfWeek', '')
                            opens = hour_spec.get('opens', '')
                            closes = hour_spec.get('closes', '')
                            if day:
                                processed['hours'][day] = f"{opens} - {closes}"
        
        # Handle person/doctor data
        elif 'Person' in item_type or 'Physician' in item_type:
            person = {
                'name': item.get('name', ''),
                'role': item.get('jobTitle', 'Staff'),
                'description': item.get('description', '')
            }
            if person['name']:
                processed['team'].append(person)
        
        # Handle reviews
        elif 'Review' in item_type:
            review = {
                'author': item.get('author', {}).get('name', 'Patient'),
                'rating': item.get('reviewRating', {}).get('ratingValue', ''),
                'text': item.get('reviewBody', '')
            }
            if review['text']:
                processed['reviews'].append(review)
        
        # Handle FAQs
        elif 'FAQPage' in item_type:
            if item.get('mainEntity'):
                for qa in item['mainEntity']:
                    if isinstance(qa, dict):
                        faq = {
                            'question': qa.get('name', ''),
                            'answer': qa.get('acceptedAnswer', {}).get('text', '')
                        }
                        if faq['question'] and faq['answer']:
                            processed['faqs'].append(faq)
        
        # Handle medical services
        elif 'MedicalProcedure' in item_type or 'MedicalService' in item_type:
            service = item.get('name', '')
            if service:
                processed['services'].append(service)
    
    def _process_microdata_item(self, item: Dict, processed: Dict):
        """Process a single microdata item"""
        item_type = item.get('@type', '')
        props = item.get('properties', {})
        
        # Similar processing to JSON-LD but adapted for microdata structure
        if any(t in item_type for t in ['Organization', 'Dentist', 'MedicalClinic']):
            if props.get('name') and not processed['organization'].get('name'):
                processed['organization']['name'] = props['name']
            if props.get('telephone') and not processed['contact'].get('phone'):
                processed['contact']['phone'] = props['telephone']
            if props.get('email') and not processed['contact'].get('email'):
                processed['contact']['email'] = props['email']


class ContentCleaner:
    """Clean and normalize extracted web content"""
    
    @staticmethod
    def clean_text(text: str) -> str:
        """Clean and normalize text content"""
        if not text:
            return ""
        
        # Remove excessive whitespace
        text = re.sub(r'\s+', ' ', text)
        
        # Remove special characters but keep medical terms
        text = re.sub(r'[^\w\s\-.,;:!?()/@$%]', '', text)
        
        # Remove repeated punctuation
        text = re.sub(r'([.!?])\1+', r'\1', text)
        
        # Remove very short lines (likely navigation items)
        lines = text.split('.')
        cleaned_lines = [line.strip() for line in lines if len(line.strip()) > 20]
        text = '. '.join(cleaned_lines)
        
        return text.strip()
    
    @staticmethod
    def extract_main_content(html: str) -> str:
        """Extract main content from HTML, removing navigation, ads, etc."""
        soup = BeautifulSoup(html, 'html.parser')
        
        # Remove script and style elements
        for element in soup(['script', 'style', 'noscript', 'iframe']):
            element.decompose()
        
        # Remove navigation and footer elements
        for element in soup.find_all(['nav', 'footer', 'header']):
            element.decompose()
        
        # Remove elements with ad-related classes
        ad_patterns = ['ad', 'advertisement', 'banner', 'popup', 'modal', 'cookie']
        for pattern in ad_patterns:
            for element in soup.find_all(class_=re.compile(pattern, re.I)):
                element.decompose()
        
        # Try to find main content area
        main_content = (
            soup.find('main') or
            soup.find('article') or
            soup.find('div', class_=re.compile(r'content|main', re.I)) or
            soup.find('body')
        )
        
        if main_content:
            text = main_content.get_text(separator=' ', strip=True)
            return ContentCleaner.clean_text(text)
        
        return ""
    
    @staticmethod
    def is_relevant_content(text: str, min_length: int = 100) -> bool:
        """Check if content is relevant and substantial"""
        if not text or len(text) < min_length:
            return False
        
        # Check for healthcare-related keywords
        healthcare_keywords = [
            'appointment', 'patient', 'doctor', 'clinic', 'medical',
            'health', 'treatment', 'service', 'care', 'dental',
            'physician', 'consultation', 'diagnosis', 'therapy'
        ]
        
        text_lower = text.lower()
        keyword_count = sum(1 for keyword in healthcare_keywords if keyword in text_lower)
        
        # Content should have at least 2 healthcare keywords
        return keyword_count >= 2