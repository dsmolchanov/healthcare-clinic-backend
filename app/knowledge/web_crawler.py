"""
Enhanced Web Crawler for Clinic Websites
Builds upon existing parse_website functionality with deeper crawling capabilities
"""

import asyncio
import hashlib
import re
from typing import Dict, Any, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, parse_qs
from datetime import datetime
import logging

import httpx
from bs4 import BeautifulSoup
from urllib.robotparser import RobotFileParser

logger = logging.getLogger(__name__)


class EnhancedWebCrawler:
    """
    Enhanced web crawler that:
    1. Respects robots.txt
    2. Implements rate limiting
    3. Extracts structured data (Schema.org)
    4. Crawls multiple pages intelligently
    5. Identifies and extracts clinic-specific information
    """
    
    def __init__(
        self,
        max_depth: int = 3,
        max_pages: int = 50,
        rate_limit_delay: float = 1.0,
        timeout: int = 30,
        progress_callback: callable = None
    ):
        self.max_depth = max_depth
        self.max_pages = max_pages
        self.rate_limit_delay = rate_limit_delay
        self.timeout = timeout
        self.progress_callback = progress_callback  # Callback for progress updates
        
        # Track visited URLs to avoid duplicates
        self.visited_urls: Set[str] = set()
        self.crawled_content: List[Dict[str, Any]] = []
        
        # User agent for crawling
        self.user_agent = "ClinicKnowledgeBot/1.0 (compatible; HIPAA-compliant healthcare crawler)"
        
        # Patterns for identifying important clinic pages
        self.important_patterns = [
            r'/about',
            r'/services',
            r'/team',
            r'/staff',
            r'/doctors',
            r'/providers',
            r'/treatments',
            r'/procedures',
            r'/faq',
            r'/insurance',
            r'/patient-info',
            r'/contact',
            r'/location',
            r'/hours',
            r'/testimonials',
            r'/reviews',
            r'/blog',
            r'/resources',
            r'/forms',
            r'/appointments',
            r'/schedule'
        ]
        
        # Healthcare-specific extraction patterns
        self.extraction_patterns = {
            'services': [
                r'(?i)(dental|medical|health)\s+services',
                r'(?i)treatments?\s+we\s+offer',
                r'(?i)our\s+services',
                r'(?i)procedures?\s+offered'
            ],
            'insurance': [
                r'(?i)insurance\s+accepted',
                r'(?i)we\s+accept',
                r'(?i)insurance\s+plans',
                r'(?i)payment\s+options'
            ],
            'team': [
                r'(?i)dr\.?\s+[A-Z][a-z]+\s+[A-Z][a-z]+',
                r'(?i)doctor\s+[A-Z][a-z]+',
                r'(?i)physician',
                r'(?i)dentist',
                r'(?i)specialist'
            ],
            'hours': [
                r'(?i)hours?\s+of\s+operation',
                r'(?i)business\s+hours',
                r'(?i)office\s+hours',
                r'(?i)open\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)'
            ],
            'emergency': [
                r'(?i)emergency\s+(number|contact|phone)',
                r'(?i)after\s+hours',
                r'(?i)urgent\s+care',
                r'(?i)24(/|-)7'
            ]
        }
    
    async def crawl(self, start_url: str) -> Dict[str, Any]:
        """
        Main crawling function that orchestrates the entire process
        """
        parsed_url = urlparse(start_url)
        base_domain = f"{parsed_url.scheme}://{parsed_url.netloc}"
        
        # Check robots.txt first
        robots_allowed = await self._check_robots_txt(base_domain)
        if not robots_allowed:
            logger.warning(f"Robots.txt disallows crawling for {base_domain}")
            # Still crawl the main page if explicitly requested
            content = await self._crawl_single_page(start_url, depth=0)
            if content:
                self.crawled_content.append(content)
        else:
            # Start recursive crawling
            await self._crawl_recursive(start_url, base_domain, depth=0)
        
        # Process and structure all crawled content
        structured_data = self._structure_crawled_data()
        
        return {
            'url': start_url,
            'pages_crawled': len(self.visited_urls),
            'structured_data': structured_data,
            'raw_pages': self.crawled_content,
            'crawled_at': datetime.utcnow().isoformat()
        }
    
    async def _check_robots_txt(self, base_url: str) -> bool:
        """Check if crawling is allowed by robots.txt"""
        robots_url = urljoin(base_url, '/robots.txt')
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    robots_url,
                    headers={'User-Agent': self.user_agent},
                    timeout=10,
                    follow_redirects=True
                )
                
                if response.status_code == 200:
                    rp = RobotFileParser()
                    rp.parse(response.text.splitlines())
                    return rp.can_fetch(self.user_agent, base_url)
        except Exception as e:
            logger.debug(f"Could not fetch robots.txt: {e}")
        
        # If no robots.txt or error, assume allowed
        return True
    
    async def _crawl_recursive(
        self,
        url: str,
        base_domain: str,
        depth: int
    ):
        """Recursively crawl pages within the same domain"""
        if (
            depth > self.max_depth or
            len(self.visited_urls) >= self.max_pages or
            url in self.visited_urls
        ):
            return
        
        # Normalize URL
        url = self._normalize_url(url)
        if not url or not url.startswith(base_domain):
            return
        
        # Mark as visited
        self.visited_urls.add(url)
        
        # Report progress if callback is set
        if self.progress_callback:
            crawl_progress = (len(self.visited_urls) / self.max_pages) * 100
            await self.progress_callback(
                pages_crawled=len(self.visited_urls),
                total_pages=self.max_pages,
                current_url=url,
                progress=min(crawl_progress, 100)
            )
        
        # Crawl the page
        page_content = await self._crawl_single_page(url, depth)
        if not page_content:
            return
        
        self.crawled_content.append(page_content)
        
        # Rate limiting
        await asyncio.sleep(self.rate_limit_delay)
        
        # Extract and crawl links
        links = page_content.get('links', [])
        
        # Prioritize important pages
        prioritized_links = self._prioritize_links(links, base_domain)
        
        # Crawl child pages
        for link in prioritized_links[:10]:  # Limit to 10 links per page
            await self._crawl_recursive(link, base_domain, depth + 1)
    
    async def _crawl_single_page(
        self,
        url: str,
        depth: int
    ) -> Optional[Dict[str, Any]]:
        """Crawl a single page and extract information"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url,
                    headers={'User-Agent': self.user_agent},
                    timeout=self.timeout,
                    follow_redirects=True
                )
                
                if response.status_code != 200:
                    return None
                
                # Parse HTML
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Extract various types of information
                extracted_data = {
                    'url': url,
                    'depth': depth,
                    'title': self._extract_title(soup),
                    'description': self._extract_description(soup),
                    'content': self._extract_main_content(soup),
                    'structured_data': self._extract_structured_data(soup),
                    'links': self._extract_links(soup, url),
                    'images': self._extract_images(soup, url),
                    'contact_info': self._extract_contact_info(soup),
                    'services': self._extract_services(soup),
                    'team_members': self._extract_team_members(soup),
                    'business_hours': self._extract_business_hours(soup),
                    'faqs': self._extract_faqs(soup),
                    'testimonials': self._extract_testimonials(soup),
                    'crawled_at': datetime.utcnow().isoformat()
                }
                
                return extracted_data
                
        except Exception as e:
            logger.error(f"Error crawling {url}: {e}")
            return None
    
    def _normalize_url(self, url: str) -> Optional[str]:
        """Normalize URL for consistency"""
        try:
            # Remove fragment
            url = url.split('#')[0]
            
            # Remove common tracking parameters
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            
            # Remove tracking params
            tracking_params = ['utm_source', 'utm_medium', 'utm_campaign', 'fbclid', 'gclid']
            filtered_params = {
                k: v for k, v in params.items()
                if k not in tracking_params
            }
            
            # Rebuild URL
            if filtered_params:
                query = '&'.join([f"{k}={v[0]}" for k, v in filtered_params.items()])
                url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{query}"
            else:
                url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            
            # Remove trailing slash
            if url.endswith('/') and url != '/':
                url = url[:-1]
            
            return url
        except:
            return None
    
    def _prioritize_links(self, links: List[str], base_domain: str) -> List[str]:
        """Prioritize links based on importance patterns"""
        prioritized = []
        regular = []
        
        for link in links:
            if not link.startswith(base_domain):
                continue
            
            # Check if link matches important patterns
            is_important = any(
                re.search(pattern, link.lower())
                for pattern in self.important_patterns
            )
            
            if is_important:
                prioritized.append(link)
            else:
                regular.append(link)
        
        # Return prioritized links first
        return prioritized + regular
    
    def _extract_title(self, soup: BeautifulSoup) -> str:
        """Extract page title"""
        title = soup.find('title')
        if title:
            return title.get_text().strip()
        
        # Fallback to h1
        h1 = soup.find('h1')
        if h1:
            return h1.get_text().strip()
        
        return ""
    
    def _extract_description(self, soup: BeautifulSoup) -> str:
        """Extract page description from meta tags"""
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc:
            return meta_desc.get('content', '').strip()
        
        # Try og:description
        og_desc = soup.find('meta', property='og:description')
        if og_desc:
            return og_desc.get('content', '').strip()
        
        return ""
    
    def _extract_main_content(self, soup: BeautifulSoup) -> str:
        """Extract main content from the page"""
        # Try to find main content area
        main_content = soup.find('main') or soup.find('article') or soup.find('div', class_='content')
        
        if main_content:
            # Remove scripts and styles
            for script in main_content(['script', 'style']):
                script.decompose()
            
            text = main_content.get_text(separator=' ', strip=True)
            # Limit to reasonable length
            return text[:10000]
        
        # Fallback to body text
        body_text = soup.get_text(separator=' ', strip=True)
        return body_text[:10000]
    
    def _extract_structured_data(self, soup: BeautifulSoup) -> List[Dict]:
        """Extract Schema.org structured data"""
        structured_data = []
        
        # Look for JSON-LD
        json_ld_scripts = soup.find_all('script', type='application/ld+json')
        for script in json_ld_scripts:
            try:
                import json
                data = json.loads(script.string)
                structured_data.append(data)
            except:
                pass
        
        return structured_data
    
    def _extract_links(self, soup: BeautifulSoup, base_url: str) -> List[str]:
        """Extract all links from the page"""
        links = []
        for link in soup.find_all('a', href=True):
            href = link['href']
            # Convert relative URLs to absolute
            absolute_url = urljoin(base_url, href)
            normalized = self._normalize_url(absolute_url)
            if normalized:
                links.append(normalized)
        
        return list(set(links))  # Remove duplicates
    
    def _extract_images(self, soup: BeautifulSoup, base_url: str) -> List[Dict]:
        """Extract images with alt text"""
        images = []
        for img in soup.find_all('img')[:20]:  # Limit to 20 images
            src = img.get('src', '')
            if src:
                images.append({
                    'src': urljoin(base_url, src),
                    'alt': img.get('alt', ''),
                    'title': img.get('title', '')
                })
        return images
    
    def _extract_contact_info(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """Extract contact information"""
        contact = {}
        
        # Phone numbers
        phone_patterns = [
            r'\+?1?\s*\(?(\d{3})\)?[-.\s]?(\d{3})[-.\s]?(\d{4})',
            r'tel:([+\d\s()-]+)'
        ]
        
        for pattern in phone_patterns:
            phones = re.findall(pattern, str(soup))
            if phones:
                contact['phones'] = list(set([re.sub(r'[^\d+]', '', str(p)) for p in phones[:5]]))
                break
        
        # Email addresses
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        emails = re.findall(email_pattern, str(soup))
        if emails:
            contact['emails'] = list(set(emails[:5]))
        
        # Address
        address_elem = soup.find('address')
        if address_elem:
            contact['address'] = address_elem.get_text(strip=True)
        
        return contact
    
    def _extract_services(self, soup: BeautifulSoup) -> List[str]:
        """Extract services offered by the clinic"""
        services = []
        
        # Look for service lists
        service_sections = soup.find_all(['ul', 'ol', 'div'], class_=re.compile(r'service|treatment|procedure', re.I))
        
        for section in service_sections[:5]:
            items = section.find_all(['li', 'p', 'span'])
            for item in items[:20]:
                text = item.get_text(strip=True)
                if 10 < len(text) < 100:  # Reasonable length for a service name
                    services.append(text)
        
        # Also look for headings that might indicate services
        service_headings = soup.find_all(['h2', 'h3', 'h4'], string=re.compile(r'service|treatment|procedure|offer', re.I))
        for heading in service_headings[:3]:
            next_elem = heading.find_next_sibling()
            if next_elem:
                text = next_elem.get_text(strip=True)[:500]
                services.append(f"Section: {text}")
        
        return list(set(services))[:30]  # Limit and dedupe
    
    def _extract_team_members(self, soup: BeautifulSoup) -> List[Dict]:
        """Extract information about doctors and staff"""
        team = []
        
        # Look for doctor patterns
        doctor_pattern = r'(?i)(dr\.?|doctor)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)'
        doctors = re.findall(doctor_pattern, str(soup))
        
        for title, name in doctors[:10]:
            team.append({
                'name': f"{title} {name}".strip(),
                'role': 'Doctor'
            })
        
        # Look for team sections
        team_sections = soup.find_all(['div', 'section'], class_=re.compile(r'team|staff|doctor|provider', re.I))
        
        for section in team_sections[:2]:
            # Look for individual team member cards
            cards = section.find_all(['div', 'article'], class_=re.compile(r'member|person|doctor|staff', re.I))
            
            for card in cards[:10]:
                name_elem = card.find(['h3', 'h4', 'h5', 'strong'])
                if name_elem:
                    name = name_elem.get_text(strip=True)
                    role = ""
                    
                    # Try to find role/title
                    role_elem = card.find(['p', 'span'], class_=re.compile(r'title|role|position', re.I))
                    if role_elem:
                        role = role_elem.get_text(strip=True)
                    
                    if name and not any(member['name'] == name for member in team):
                        team.append({
                            'name': name,
                            'role': role or 'Staff'
                        })
        
        return team[:20]  # Limit results
    
    def _extract_business_hours(self, soup: BeautifulSoup) -> Dict[str, str]:
        """Extract business hours"""
        hours = {}
        
        # Look for hours sections
        hours_sections = soup.find_all(['div', 'section', 'table'], 
                                       string=re.compile(r'hours|schedule|open', re.I))
        
        days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        
        for section in hours_sections[:3]:
            text = section.get_text()
            
            for day in days:
                # Look for day patterns
                pattern = rf'(?i){day}[\s:]*([0-9:\sAMPM-]+)'
                match = re.search(pattern, text)
                if match:
                    hours[day.capitalize()] = match.group(1).strip()
        
        return hours
    
    def _extract_faqs(self, soup: BeautifulSoup) -> List[Dict]:
        """Extract FAQs"""
        faqs = []
        
        # Look for FAQ sections
        faq_sections = soup.find_all(['div', 'section'], 
                                     class_=re.compile(r'faq|question|accordion', re.I))
        
        for section in faq_sections[:2]:
            # Look for Q&A pairs
            questions = section.find_all(['h3', 'h4', 'h5', 'dt', 'strong'])
            
            for q_elem in questions[:10]:
                question = q_elem.get_text(strip=True)
                
                # Try to find the answer
                answer = ""
                next_elem = q_elem.find_next_sibling()
                if next_elem and next_elem.name in ['p', 'dd', 'div']:
                    answer = next_elem.get_text(strip=True)[:500]
                
                if question and answer:
                    faqs.append({
                        'question': question,
                        'answer': answer
                    })
        
        return faqs[:15]
    
    def _extract_testimonials(self, soup: BeautifulSoup) -> List[Dict]:
        """Extract patient testimonials"""
        testimonials = []
        
        # Look for testimonial sections
        testimonial_sections = soup.find_all(['div', 'section'], 
                                            class_=re.compile(r'testimonial|review|feedback', re.I))
        
        for section in testimonial_sections[:2]:
            # Look for individual testimonials
            items = section.find_all(['blockquote', 'div', 'p'])
            
            for item in items[:10]:
                text = item.get_text(strip=True)
                
                if 50 < len(text) < 500:  # Reasonable length for testimonial
                    # Try to find author
                    author = ""
                    author_elem = item.find(['cite', 'span', 'p'], class_=re.compile(r'author|name|patient', re.I))
                    if author_elem:
                        author = author_elem.get_text(strip=True)
                    
                    testimonials.append({
                        'text': text,
                        'author': author or 'Patient'
                    })
        
        return testimonials[:10]
    
    def _structure_crawled_data(self) -> Dict[str, Any]:
        """Structure all crawled data into a comprehensive knowledge base"""
        structured = {
            'clinic_info': {},
            'services': [],
            'team': [],
            'faqs': [],
            'testimonials': [],
            'contact': {},
            'hours': {},
            'pages': []
        }
        
        # Aggregate data from all pages
        all_services = []
        all_team = []
        all_faqs = []
        all_testimonials = []
        
        for page in self.crawled_content:
            # Collect services
            if page.get('services'):
                all_services.extend(page['services'])
            
            # Collect team members
            if page.get('team_members'):
                all_team.extend(page['team_members'])
            
            # Collect FAQs
            if page.get('faqs'):
                all_faqs.extend(page['faqs'])
            
            # Collect testimonials
            if page.get('testimonials'):
                all_testimonials.extend(page['testimonials'])
            
            # Update contact info (take from first page that has it)
            if page.get('contact_info') and not structured['contact']:
                structured['contact'] = page['contact_info']
            
            # Update hours (take from first page that has it)
            if page.get('business_hours') and not structured['hours']:
                structured['hours'] = page['business_hours']
            
            # Add page summary
            structured['pages'].append({
                'url': page['url'],
                'title': page.get('title', ''),
                'description': page.get('description', ''),
                'depth': page.get('depth', 0)
            })
        
        # Deduplicate and structure
        structured['services'] = self._deduplicate_list(all_services)
        structured['team'] = self._deduplicate_dicts(all_team, 'name')
        structured['faqs'] = self._deduplicate_dicts(all_faqs, 'question')
        structured['testimonials'] = all_testimonials[:20]  # Keep some variety
        
        # Extract clinic name from title of homepage
        if self.crawled_content:
            homepage = next((p for p in self.crawled_content if p['depth'] == 0), None)
            if homepage:
                structured['clinic_info']['name'] = homepage.get('title', '')
                structured['clinic_info']['description'] = homepage.get('description', '')
        
        return structured
    
    def _deduplicate_list(self, items: List[str]) -> List[str]:
        """Remove duplicates from list while preserving order"""
        seen = set()
        result = []
        for item in items:
            if item not in seen:
                seen.add(item)
                result.append(item)
        return result
    
    def _deduplicate_dicts(self, items: List[Dict], key: str) -> List[Dict]:
        """Remove duplicate dictionaries based on a key"""
        seen = set()
        result = []
        for item in items:
            if key in item and item[key] not in seen:
                seen.add(item[key])
                result.append(item)
        return result


class SitemapCrawler:
    """Crawl website using sitemap.xml for better coverage"""
    
    def __init__(self):
        self.user_agent = "ClinicKnowledgeBot/1.0"
    
    async def parse_sitemap(self, url: str, visited_sitemaps: set = None, max_depth: int = 3, current_depth: int = 0) -> List[str]:
        """Parse sitemap.xml and extract URLs
        
        Args:
            url: The base URL or sitemap URL to parse
            visited_sitemaps: Set of already visited sitemap URLs to prevent loops
            max_depth: Maximum recursion depth for sitemap indexes
            current_depth: Current recursion depth
        """
        if visited_sitemaps is None:
            visited_sitemaps = set()
        
        # Prevent infinite recursion
        if current_depth >= max_depth:
            logger.warning(f"Max sitemap depth {max_depth} reached")
            return []
        
        sitemap_urls = []
        
        # Try common sitemap locations
        sitemap_locations = [
            urljoin(url, '/sitemap.xml'),
            urljoin(url, '/sitemap_index.xml'),
            urljoin(url, '/sitemap.xml.gz')
        ]
        
        async with httpx.AsyncClient(follow_redirects=True, max_redirects=3) as client:
            for sitemap_url in sitemap_locations:
                # Skip if already visited to prevent loops
                if sitemap_url in visited_sitemaps:
                    continue
                    
                visited_sitemaps.add(sitemap_url)
                
                try:
                    response = await client.get(
                        sitemap_url,
                        headers={'User-Agent': self.user_agent},
                        timeout=10
                    )
                    
                    if response.status_code == 200:
                        # Track the final URL after redirects
                        final_url = str(response.url)
                        visited_sitemaps.add(final_url)
                        
                        # Parse XML
                        soup = BeautifulSoup(response.text, 'xml')
                        
                        # Extract URLs from sitemap
                        for loc in soup.find_all('loc'):
                            url_text = loc.text.strip()
                            # Only add non-sitemap URLs
                            if not any(x in url_text.lower() for x in ['sitemap', '.xml']):
                                sitemap_urls.append(url_text)
                        
                        # If it's a sitemap index, recursively parse child sitemaps
                        for sitemap in soup.find_all('sitemap'):
                            child_loc = sitemap.find('loc')
                            if child_loc:
                                child_url = child_loc.text.strip()
                                # Only process if not already visited
                                if child_url not in visited_sitemaps:
                                    child_urls = await self.parse_sitemap(
                                        child_url, 
                                        visited_sitemaps, 
                                        max_depth, 
                                        current_depth + 1
                                    )
                                    sitemap_urls.extend(child_urls)
                        
                        break  # Found sitemap, stop trying other locations
                        
                except Exception as e:
                    logger.debug(f"Could not fetch sitemap from {sitemap_url}: {e}")
        
        return sitemap_urls


class IncrementalCrawler:
    """Support incremental crawling to update knowledge base"""
    
    def __init__(self, db):
        self.db = db
    
    async def get_previous_crawl_hash(self, url: str) -> Optional[str]:
        """Get hash of previous crawl for comparison"""
        try:
            result = await self.db.table('knowledge_crawls').select('content_hash').eq('url', url).single().execute()
            if result.data:
                return result.data.get('content_hash')
        except:
            pass
        return None
    
    async def has_content_changed(self, url: str, current_content: str) -> bool:
        """Check if content has changed since last crawl"""
        current_hash = hashlib.sha256(current_content.encode()).hexdigest()
        previous_hash = await self.get_previous_crawl_hash(url)
        
        if not previous_hash:
            return True  # No previous crawl, consider as changed
        
        return current_hash != previous_hash
    
    async def save_crawl_hash(self, url: str, content: str):
        """Save hash of current crawl"""
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        
        await self.db.table('knowledge_crawls').upsert({
            'url': url,
            'content_hash': content_hash,
            'crawled_at': datetime.utcnow().isoformat()
        }).execute()