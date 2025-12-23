"""Embedding generation utilities for semantic search.

Reuses patterns from rag_cache.py and conversation_memory.py.
"""
import os
import logging
from typing import List, Optional
import numpy as np

logger = logging.getLogger(__name__)

# Configuration
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536
BATCH_SIZE = 100  # OpenAI supports up to 2048


class EmbeddingGenerator:
    """Generate embeddings using OpenAI API.

    Thread-safe singleton for efficient embedding generation.
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.client = None
        self.model = EMBEDDING_MODEL
        self.dimensions = EMBEDDING_DIMENSIONS
        self._initialized = True

    def _ensure_client(self):
        """Lazy initialization of OpenAI client."""
        if self.client is None:
            from openai import OpenAI
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY environment variable required")
            self.client = OpenAI(api_key=api_key)

    def generate(self, text: str) -> np.ndarray:
        """Generate embedding for a single text.

        Args:
            text: Text to embed

        Returns:
            1536-dimensional numpy array
        """
        if not text or not text.strip():
            return np.zeros(self.dimensions)

        try:
            self._ensure_client()
            response = self.client.embeddings.create(
                model=self.model,
                input=text.strip()
            )
            return np.array(response.data[0].embedding)
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            return np.zeros(self.dimensions)

    def generate_batch(self, texts: List[str]) -> List[np.ndarray]:
        """Generate embeddings for multiple texts efficiently.

        Args:
            texts: List of texts to embed

        Returns:
            List of 1536-dimensional numpy arrays
        """
        if not texts:
            return []

        # Filter and track empty texts
        valid_texts = []
        valid_indices = []
        for i, text in enumerate(texts):
            if text and text.strip():
                valid_texts.append(text.strip())
                valid_indices.append(i)

        if not valid_texts:
            return [np.zeros(self.dimensions) for _ in texts]

        # Initialize results with zeros
        results = [np.zeros(self.dimensions) for _ in texts]

        try:
            self._ensure_client()
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI client: {e}")
            return results

        # Process in batches
        for batch_start in range(0, len(valid_texts), BATCH_SIZE):
            batch_texts = valid_texts[batch_start:batch_start + BATCH_SIZE]
            batch_indices = valid_indices[batch_start:batch_start + BATCH_SIZE]

            try:
                response = self.client.embeddings.create(
                    model=self.model,
                    input=batch_texts
                )

                for j, embedding_data in enumerate(response.data):
                    original_idx = batch_indices[j]
                    results[original_idx] = np.array(embedding_data.embedding)

            except Exception as e:
                logger.error(f"Batch embedding failed: {e}")
                # Results already initialized to zeros

        return results


def cosine_similarity(vec1: np.ndarray, vec2: np.ndarray) -> float:
    """Calculate cosine similarity between two vectors.

    Extracted from rag_cache.py for reuse.

    Args:
        vec1: First vector
        vec2: Second vector

    Returns:
        Cosine similarity score (-1 to 1, higher = more similar)
    """
    try:
        dot_product = np.dot(vec1, vec2)
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return float(dot_product / (norm1 * norm2))
    except Exception:
        return 0.0


# Singleton accessor
def get_embedding_generator() -> EmbeddingGenerator:
    """Get the singleton embedding generator instance."""
    return EmbeddingGenerator()
