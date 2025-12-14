"""
Embedding service for creating vector embeddings using OpenAI API.
"""

import os
from typing import List, Optional
from dotenv import load_dotenv
import openai

load_dotenv()


class EmbeddingService:
    """Service for creating text embeddings using OpenAI."""
    
    def __init__(self, api_key: Optional[str] = None, model: str = "text-embedding-3-small"):
        """
        Initialize the embedding service.
        
        Args:
            api_key: OpenAI API key (optional, uses env var if not provided)
            model: Embedding model to use (default: text-embedding-3-small)
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key is required. Set OPENAI_API_KEY environment variable.")
        
        self.model = model
        self.client = openai.OpenAI(api_key=self.api_key)
    
    def create_embedding(self, text: str) -> List[float]:
        """
        Create an embedding for a single text.
        
        Args:
            text: Text to embed
        
        Returns:
            Embedding vector as list of floats
        """
        try:
            response = self.client.embeddings.create(
                model=self.model,
                input=text
            )
            return response.data[0].embedding
        except Exception as e:
            raise RuntimeError(f"Failed to create embedding: {e}")
    
    def create_embeddings_batch(self, texts: List[str], batch_size: int = 100) -> List[List[float]]:
        """
        Create embeddings for multiple texts in batches.
        
        Args:
            texts: List of texts to embed
            batch_size: Number of texts to process per batch (OpenAI limit is typically 2048)
        
        Returns:
            List of embedding vectors
        """
        embeddings = []
        
        # Process in batches
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            
            try:
                response = self.client.embeddings.create(
                    model=self.model,
                    input=batch
                )
                
                # Extract embeddings from response
                batch_embeddings = [item.embedding for item in response.data]
                embeddings.extend(batch_embeddings)
                
            except Exception as e:
                # If batch fails, try individual embeddings
                print(f"Warning: Batch embedding failed, processing individually: {e}")
                for text in batch:
                    try:
                        embedding = self.create_embedding(text)
                        embeddings.append(embedding)
                    except Exception as e2:
                        print(f"Error embedding text: {e2}")
                        # Add zero vector as placeholder
                        embeddings.append([0.0] * 1536)  # text-embedding-3-small dimension
        
        return embeddings
    
    def get_embedding_dimension(self) -> int:
        """
        Get the dimension of embeddings for the current model.
        
        Returns:
            Embedding dimension
        """
        # Common dimensions for OpenAI models
        dimensions = {
            "text-embedding-ada-002": 1536,
            "text-embedding-3-small": 1536,
            "text-embedding-3-large": 3072
        }
        return dimensions.get(self.model, 1536)

