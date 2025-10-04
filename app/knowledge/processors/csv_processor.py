"""CSV Document Processor"""

import csv
import io
import logging
from typing import Dict, Any, List

from ..router import BaseProcessor, InputData, ProcessedDocument

logger = logging.getLogger(__name__)

class CSVProcessor(BaseProcessor):
    """Process CSV files"""
    
    supported_mime_types = ['text/csv', 'application/csv']
    
    def can_process(self, mime_type: str) -> bool:
        return mime_type in self.supported_mime_types
    
    async def process(self, input_data: InputData) -> ProcessedDocument:
        """Process CSV document"""
        try:
            # Get CSV content
            if isinstance(input_data.content, bytes):
                content = input_data.content.decode('utf-8', errors='ignore')
            else:
                content = input_data.content
            
            # Parse CSV
            reader = csv.DictReader(io.StringIO(content))
            rows = list(reader)
            
            # Convert to text representation
            text_parts = []
            if reader.fieldnames:
                text_parts.append("Headers: " + ", ".join(reader.fieldnames))
            
            for i, row in enumerate(rows[:100]):  # Limit to first 100 rows
                text_parts.append(f"Row {i+1}: " + " | ".join(f"{k}: {v}" for k, v in row.items()))
            
            text = "\n".join(text_parts)
            
            # Create chunks
            chunks = [{
                'index': 0,
                'content': text,
                'section': 'csv_data',
                'tokens': len(text.split()),
            }]
            
            # Extract facts
            facts = {
                'column_count': len(reader.fieldnames) if reader.fieldnames else 0,
                'row_count': len(rows),
                'headers': reader.fieldnames[:10] if reader.fieldnames else [],
            }
            
            return ProcessedDocument(
                content=text,
                chunks=chunks,
                facts=facts,
                metadata={'format': 'csv', 'rows': len(rows)},
                processing_time_ms=0,
                tokens_count=len(text.split())
            )
        except Exception as e:
            logger.error(f"Error processing CSV: {str(e)}")
            raise