"""
Storage management for media and documents
"""

import aiohttp
from typing import Optional


async def download_media(url: str) -> bytes:
    """
    Download media from URL

    Args:
        url: Media URL

    Returns:
        Media bytes
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return await response.read()


async def process_document(document_data: bytes) -> Dict[str, Any]:
    """
    Process document (e.g., PDF)

    Args:
        document_data: Document bytes

    Returns:
        Processed document data
    """
    # Mock implementation
    return {
        'extracted_text': 'Document content'
    }
