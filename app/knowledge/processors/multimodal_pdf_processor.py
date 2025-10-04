"""
Enhanced PDF Processor using Multimodal AI for Rich Content Extraction
Uses GPT-5-mini/GPT-4o-mini to extract tables, images, and complex formatting
"""

import logging
import io
import base64
import time
from typing import List, Dict, Any, Optional
import fitz  # PyMuPDF
from PIL import Image
import openai
import os
import json
from langchain.text_splitter import RecursiveCharacterTextSplitter
from app.knowledge.router import InputData, ProcessedDocument

logger = logging.getLogger(__name__)


class MultimodalPDFProcessor:
    """Enhanced PDF processor using multimodal AI for rich content extraction"""
    
    supported_mime_types = ['application/pdf']
    
    def __init__(self):
        """Initialize multimodal PDF processor"""
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            separators=["\n\n", "\n", ". ", " ", ""]
        )
        self.client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        
        # Model selection with fallback
        self.primary_model = "gpt-5-mini"  # $0.4/1M tokens
        self.fallback_model = "gpt-4o-mini"  # Fallback if gpt-5-mini not available
        
    def can_process(self, mime_type: str) -> bool:
        """Check if this processor can handle the given mime type"""
        return mime_type in self.supported_mime_types
    
    async def process(self, input_data: InputData) -> ProcessedDocument:
        """
        Process PDF using multimodal AI for rich content extraction
        
        Args:
            input_data: Input data containing PDF content
            
        Returns:
            ProcessedDocument with extracted content including tables and images
        """
        start_time = time.time()
        
        try:
            # Get bytes content
            if isinstance(input_data.content, bytes):
                file_content = input_data.content
            else:
                file_content = input_data.content.encode('utf-8')
            
            # Create a BytesIO object from the content
            pdf_stream = io.BytesIO(file_content)
            
            # Open PDF from bytes
            doc = fitz.open(stream=pdf_stream, filetype="pdf")
            
            # Process each page with multimodal AI
            all_extracted_content = []
            page_images = []
            extracted_tables = []
            extracted_images = []
            
            # Store page count before processing
            total_pages = len(doc)
            pages_to_process = min(total_pages, 20)  # Limit to 20 pages for cost
            
            for page_num in range(pages_to_process):
                page = doc[page_num]
                
                # Convert page to image for multimodal processing
                mat = fitz.Matrix(2.0, 2.0)  # 2x zoom for better quality
                pix = page.get_pixmap(matrix=mat)
                img_data = pix.tobytes("png")
                
                # Encode image to base64
                img_base64 = base64.b64encode(img_data).decode('utf-8')
                page_images.append(img_base64)
                
                # Extract text with AI for better structure preservation
                page_content = await self._extract_page_content_with_ai(
                    img_base64, 
                    page_num + 1,
                    input_data.metadata.get('clinic_name', 'the clinic')
                )
                
                if page_content:
                    all_extracted_content.append(page_content)
                    
                    # Collect tables and images
                    if 'tables' in page_content:
                        extracted_tables.extend(page_content['tables'])
                    if 'images' in page_content:
                        extracted_images.extend(page_content['images'])
                
                # Also get plain text as fallback
                plain_text = page.get_text()
                if not page_content and plain_text.strip():
                    all_extracted_content.append({
                        'page': page_num + 1,
                        'text': plain_text,
                        'type': 'fallback_text'
                    })
            
            # Close the document
            doc.close()
            
            # Combine all extracted content
            full_content = self._combine_extracted_content(all_extracted_content)
            
            # Create intelligent chunks that preserve context
            chunks = await self._create_intelligent_chunks(
                full_content, 
                extracted_tables,
                input_data.metadata
            )
            
            # Extract comprehensive facts
            facts = await self._extract_comprehensive_facts(
                full_content,
                extracted_tables,
                extracted_images
            )
            
            # Add metadata about extraction
            enhanced_metadata = {
                **(input_data.metadata or {}),
                "processor": "multimodal_pdf",
                "model_used": self.primary_model if hasattr(self, '_model_used') else self.fallback_model,
                "total_pages": total_pages,
                "pages_processed": len(all_extracted_content),
                "tables_found": len(extracted_tables),
                "images_found": len(extracted_images),
                "extraction_method": "vision_ai"
            }
            
            processing_time_ms = (time.time() - start_time) * 1000
            
            logger.info(f"Successfully processed PDF with multimodal AI: {len(chunks)} chunks, "
                       f"{len(extracted_tables)} tables, {len(extracted_images)} images")
            
            return ProcessedDocument(
                content=full_content,
                chunks=chunks,
                facts=facts,
                metadata=enhanced_metadata,
                processing_time_ms=processing_time_ms,
                tokens_count=len(full_content.split())
            )
            
        except Exception as e:
            logger.error(f"Error in multimodal PDF processing: {e}")
            # Fallback to basic text extraction
            return await self._fallback_to_text_extraction(input_data, start_time)
    
    async def _extract_page_content_with_ai(
        self, 
        img_base64: str, 
        page_num: int,
        clinic_name: str
    ) -> Optional[Dict[str, Any]]:
        """Extract structured content from page image using multimodal AI"""
        
        prompt = f"""
        Analyze this page from a {clinic_name} document and extract ALL information in a structured format.
        
        Return a JSON object with:
        - page: {page_num}
        - text: All readable text content
        - tables: Array of tables with headers and rows
        - lists: Any bullet points or numbered lists
        - images: Descriptions of any charts, diagrams, or images
        - forms: Any form fields or structured data
        - contact_info: Phone numbers, emails, addresses if present
        - medical_info: Any medical procedures, services, or health information
        - business_info: Hours, policies, pricing if present
        - key_points: Important information that should be highlighted
        
        Focus on preserving structure and extracting ALL information including:
        - Tables with their headers and data
        - Lists and bullet points
        - Contact information
        - Medical/clinical information
        - Business hours and policies
        - Any forms or structured layouts
        
        Return ONLY valid JSON, no explanations.
        """
        
        try:
            # Try primary model first
            try:
                response = self.client.chat.completions.create(
                    model=self.primary_model,
                    messages=[
                        {
                            "role": "system", 
                            "content": "You are a document analysis expert. Extract and structure all information from documents."
                        },
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/png;base64,{img_base64}",
                                        "detail": "high"
                                    }
                                }
                            ]
                        }
                    ],
                    temperature=0.1,
                    max_tokens=2000
                )
                self._model_used = self.primary_model
            except Exception as e:
                logger.info(f"Primary model {self.primary_model} not available, using fallback: {e}")
                # Fallback to gpt-4o-mini
                response = self.client.chat.completions.create(
                    model=self.fallback_model,
                    messages=[
                        {
                            "role": "system", 
                            "content": "You are a document analysis expert. Extract and structure all information from documents."
                        },
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/png;base64,{img_base64}",
                                        "detail": "high"
                                    }
                                }
                            ]
                        }
                    ],
                    temperature=0.1,
                    max_tokens=2000
                )
                self._model_used = self.fallback_model
            
            # Parse response
            extracted_text = response.choices[0].message.content.strip()
            
            # Clean up JSON
            if extracted_text.startswith('```json'):
                extracted_text = extracted_text[7:]
            if extracted_text.startswith('```'):
                extracted_text = extracted_text[3:]
            if extracted_text.endswith('```'):
                extracted_text = extracted_text[:-3]
            
            return json.loads(extracted_text)
            
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse AI response for page {page_num}: {e}")
            # Return basic structure with raw text
            return {
                'page': page_num,
                'text': extracted_text if 'extracted_text' in locals() else '',
                'type': 'raw_extraction'
            }
        except Exception as e:
            logger.error(f"Error extracting page {page_num} with AI: {e}")
            return None
    
    def _combine_extracted_content(self, all_content: List[Dict]) -> str:
        """Combine all extracted content into a single structured text"""
        combined = []
        
        for content in all_content:
            page_text = f"\n\n--- Page {content.get('page', '?')} ---\n\n"
            
            # Add main text
            if 'text' in content and content['text']:
                page_text += content['text'] + "\n"
            
            # Add tables in readable format
            if 'tables' in content:
                for i, table in enumerate(content['tables']):
                    page_text += f"\n[Table {i+1}]\n"
                    if isinstance(table, dict):
                        if 'headers' in table:
                            page_text += " | ".join(str(h) for h in table['headers']) + "\n"
                        if 'rows' in table:
                            for row in table['rows']:
                                page_text += " | ".join(str(cell) for cell in row) + "\n"
                    else:
                        page_text += str(table) + "\n"
            
            # Add lists
            if 'lists' in content:
                for lst in content['lists']:
                    if isinstance(lst, list):
                        for item in lst:
                            page_text += f"• {item}\n"
                    else:
                        page_text += f"• {lst}\n"
            
            # Add key points
            if 'key_points' in content:
                page_text += "\nKey Points:\n"
                for point in content['key_points']:
                    page_text += f"★ {point}\n"
            
            combined.append(page_text)
        
        return "\n".join(combined)
    
    async def _create_intelligent_chunks(
        self, 
        full_content: str,
        tables: List[Dict],
        metadata: Dict
    ) -> List[Dict[str, Any]]:
        """Create intelligent chunks that preserve context and structure"""
        
        chunks = []
        
        # First, create text chunks
        text_chunks = self.text_splitter.split_text(full_content)
        
        for i, chunk_text in enumerate(text_chunks):
            chunk_metadata = {
                **metadata,
                "chunk_index": i,
                "total_chunks": len(text_chunks) + len(tables),
                "chunk_type": "text",
                "processor": "multimodal_pdf"
            }
            
            chunks.append({
                "text": chunk_text,
                "metadata": chunk_metadata
            })
        
        # Add tables as separate chunks for better retrieval
        for i, table in enumerate(tables):
            table_text = f"Table {i+1}:\n"
            if isinstance(table, dict):
                if 'headers' in table:
                    table_text += " | ".join(str(h) for h in table['headers']) + "\n"
                if 'rows' in table:
                    for row in table['rows']:
                        table_text += " | ".join(str(cell) for cell in row) + "\n"
            else:
                table_text += str(table)
            
            chunk_metadata = {
                **metadata,
                "chunk_index": len(text_chunks) + i,
                "total_chunks": len(text_chunks) + len(tables),
                "chunk_type": "table",
                "table_index": i,
                "processor": "multimodal_pdf"
            }
            
            chunks.append({
                "text": table_text,
                "metadata": chunk_metadata
            })
        
        return chunks
    
    async def _extract_comprehensive_facts(
        self,
        content: str,
        tables: List[Dict],
        images: List[Dict]
    ) -> Dict[str, Any]:
        """Extract comprehensive facts from the processed content"""
        
        facts = {
            "has_tables": len(tables) > 0,
            "table_count": len(tables),
            "has_images": len(images) > 0,
            "image_count": len(images),
            "extraction_method": "multimodal_ai"
        }
        
        # Use AI to extract key facts
        try:
            response = self.client.chat.completions.create(
                model=self.fallback_model,  # Use cheaper model for fact extraction
                messages=[
                    {
                        "role": "system",
                        "content": "Extract key facts from this medical/clinical document."
                    },
                    {
                        "role": "user",
                        "content": f"""
                        Extract key facts from this document content:
                        {content[:3000]}
                        
                        Return a JSON object with:
                        - services: List of medical services mentioned
                        - procedures: Medical procedures offered
                        - contact: Contact information (phone, email, address)
                        - hours: Business hours if mentioned
                        - policies: Important policies or requirements
                        - staff: Names and titles of staff mentioned
                        - specialties: Medical specialties
                        
                        Return ONLY valid JSON.
                        """
                    }
                ],
                temperature=0.1,
                max_tokens=1000
            )
            
            facts_text = response.choices[0].message.content.strip()
            if facts_text.startswith('```'):
                facts_text = facts_text[facts_text.find('{'):facts_text.rfind('}')+1]
            
            extracted_facts = json.loads(facts_text)
            facts.update(extracted_facts)
            
        except Exception as e:
            logger.warning(f"Could not extract facts with AI: {e}")
        
        return facts
    
    async def _fallback_to_text_extraction(
        self, 
        input_data: InputData, 
        start_time: float
    ) -> ProcessedDocument:
        """Fallback to basic text extraction if multimodal processing fails"""
        
        try:
            # Get bytes content
            if isinstance(input_data.content, bytes):
                file_content = input_data.content
            else:
                file_content = input_data.content.encode('utf-8')
            
            pdf_stream = io.BytesIO(file_content)
            doc = fitz.open(stream=pdf_stream, filetype="pdf")
            
            # Basic text extraction
            full_text = ""
            for page_num in range(len(doc)):
                page = doc[page_num]
                text = page.get_text()
                if text.strip():
                    full_text += f"\n\n--- Page {page_num + 1} ---\n\n{text}"
            
            doc.close()
            
            # Create basic chunks
            chunks = []
            if full_text.strip():
                text_chunks = self.text_splitter.split_text(full_text)
                for i, chunk in enumerate(text_chunks):
                    chunks.append({
                        "text": chunk,
                        "metadata": {
                            **(input_data.metadata or {}),
                            "chunk_index": i,
                            "total_chunks": len(text_chunks),
                            "processor": "multimodal_pdf_fallback"
                        }
                    })
            
            return ProcessedDocument(
                content=full_text,
                chunks=chunks,
                facts={"extraction_method": "text_fallback"},
                metadata={
                    **(input_data.metadata or {}),
                    "processor": "multimodal_pdf_fallback"
                },
                processing_time_ms=(time.time() - start_time) * 1000,
                tokens_count=len(full_text.split())
            )
            
        except Exception as e:
            logger.error(f"Fallback text extraction also failed: {e}")
            raise