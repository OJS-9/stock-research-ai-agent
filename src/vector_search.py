"""
Vector search service for finding relevant chunks using cosine similarity.
"""

import json
import numpy as np
from typing import List, Dict, Any, Optional

from database import get_database_manager


class VectorSearch:
    """Service for vector similarity search over report chunks."""
    
    def __init__(self):
        """Initialize vector search service."""
        self._db = None  # Lazy initialization - only connect when needed
    
    @property
    def db(self):
        """Lazy initialization of database manager."""
        if self._db is None:
            self._db = get_database_manager()
        return self._db
    
    def search_chunks(
        self,
        report_id: str,
        query_embedding: List[float],
        top_k: int = 5,
        min_score: float = 0.0
    ) -> List[Dict[str, Any]]:
        """
        Search for top-k most relevant chunks using cosine similarity.
        
        Args:
            report_id: Report ID to search within
            query_embedding: Query embedding vector
            top_k: Number of top results to return
            min_score: Minimum similarity score threshold
        
        Returns:
            List of chunk dictionaries with similarity scores, sorted by relevance
        """
        # Get all chunks for the report with embeddings
        chunks = self.db.get_chunks_by_report(report_id, include_embeddings=True)
        
        if not chunks:
            return []
        
        # Convert query embedding to numpy array
        query_vec = np.array(query_embedding)
        
        # Calculate cosine similarity for each chunk
        results = []
        for chunk in chunks:
            if not chunk.get('embedding'):
                continue
            
            # Parse embedding if it's a JSON string
            embedding = chunk['embedding']
            if isinstance(embedding, str):
                embedding = json.loads(embedding)
            
            chunk_vec = np.array(embedding)
            
            # Calculate cosine similarity
            similarity = self._cosine_similarity(query_vec, chunk_vec)
            
            if similarity >= min_score:
                results.append({
                    'chunk_id': chunk['chunk_id'],
                    'chunk_text': chunk['chunk_text'],
                    'section': chunk.get('section'),
                    'chunk_index': chunk['chunk_index'],
                    'similarity_score': float(similarity),
                    'created_at': chunk.get('created_at')
                })
        
        # Sort by similarity score (descending)
        results.sort(key=lambda x: x['similarity_score'], reverse=True)
        
        # Return top-k
        return results[:top_k]
    
    def search_chunks_by_section(
        self,
        report_id: str,
        query_embedding: List[float],
        section: str,
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Search for chunks within a specific section.
        
        Args:
            report_id: Report ID
            query_embedding: Query embedding vector
            section: Section name to search within
            top_k: Number of top results
        
        Returns:
            List of relevant chunks from the specified section
        """
        # Get all chunks and filter by section
        chunks = self.db.get_chunks_by_report(report_id, include_embeddings=True)
        section_chunks = [c for c in chunks if c.get('section') == section]
        
        if not section_chunks:
            return []
        
        # Calculate similarities
        query_vec = np.array(query_embedding)
        results = []
        
        for chunk in section_chunks:
            if not chunk.get('embedding'):
                continue
            
            embedding = chunk['embedding']
            if isinstance(embedding, str):
                embedding = json.loads(embedding)
            
            chunk_vec = np.array(embedding)
            similarity = self._cosine_similarity(query_vec, chunk_vec)
            
            results.append({
                'chunk_id': chunk['chunk_id'],
                'chunk_text': chunk['chunk_text'],
                'section': chunk.get('section'),
                'chunk_index': chunk['chunk_index'],
                'similarity_score': float(similarity),
                'created_at': chunk.get('created_at')
            })
        
        # Sort and return top-k
        results.sort(key=lambda x: x['similarity_score'], reverse=True)
        return results[:top_k]
    
    def _cosine_similarity(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
        """
        Calculate cosine similarity between two vectors.
        
        Args:
            vec1: First vector
            vec2: Second vector
        
        Returns:
            Cosine similarity score (0 to 1)
        """
        # Normalize vectors
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        # Calculate cosine similarity
        dot_product = np.dot(vec1, vec2)
        similarity = dot_product / (norm1 * norm2)
        
        return float(similarity)
    
    def get_all_chunks(self, report_id: str) -> List[Dict[str, Any]]:
        """
        Get all chunks for a report (for full context retrieval).
        
        Args:
            report_id: Report ID
        
        Returns:
            List of all chunks
        """
        return self.db.get_chunks_by_report(report_id, include_embeddings=False)

