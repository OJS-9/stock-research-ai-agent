"""
Report chunking service for splitting reports into semantic chunks.
"""

import re
from typing import List, Dict, Any, Optional


class ReportChunker:
    """Service for chunking reports into semantic segments."""
    
    def __init__(self, chunk_size: int = 600, overlap: int = 100):
        """
        Initialize the report chunker.
        
        Args:
            chunk_size: Target chunk size in tokens (approximate, using character count)
            overlap: Overlap between chunks in tokens (to preserve context)
        """
        # Approximate: 1 token ≈ 4 characters
        self.chunk_size_chars = chunk_size * 4
        self.overlap_chars = overlap * 4
    
    def chunk_report(
        self,
        report_text: str,
        chunk_size: Optional[int] = None,
        preserve_sections: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Split report into semantic chunks.
        
        Args:
            report_text: Full report text
            chunk_size: Override default chunk size (in tokens)
            preserve_sections: Whether to preserve section boundaries
        
        Returns:
            List of chunk dictionaries with:
            - chunk_text: Text content
            - section: Section name (if available)
            - chunk_index: Index in sequence
            - start_char: Starting character position
            - end_char: Ending character position
        """
        if chunk_size:
            chunk_size_chars = chunk_size * 4
        else:
            chunk_size_chars = self.chunk_size_chars
        
        chunks = []
        
        if preserve_sections:
            # Split by sections first, then chunk each section
            sections = self._split_into_sections(report_text)
            
            chunk_index = 0
            for section_name, section_text in sections:
                section_chunks = self._chunk_text(
                    section_text,
                    chunk_size_chars,
                    section_name
                )
                
                for chunk in section_chunks:
                    chunk['chunk_index'] = chunk_index
                    chunk_index += 1
                    chunks.append(chunk)
        else:
            # Simple chunking without section awareness
            chunks = self._chunk_text(report_text, chunk_size_chars, None)
            for i, chunk in enumerate(chunks):
                chunk['chunk_index'] = i
        
        return chunks
    
    def _split_into_sections(self, text: str) -> List[tuple]:
        """
        Split text into sections based on headers.
        
        Args:
            text: Report text
        
        Returns:
            List of (section_name, section_text) tuples
        """
        sections = []
        
        # Pattern to match markdown-style headers (# ## ###) or numbered sections
        header_pattern = r'^(#{1,3}\s+.+?|^\d+\.\s+[A-Z][^\n]+|^[A-Z][^\n:]+:)$'
        
        lines = text.split('\n')
        current_section = "Introduction"
        current_text = []
        
        for line in lines:
            # Check if line is a header
            if re.match(header_pattern, line.strip(), re.MULTILINE):
                # Save previous section
                if current_text:
                    sections.append((current_section, '\n'.join(current_text)))
                
                # Start new section
                current_section = line.strip().lstrip('#').strip()
                current_text = [line]
            else:
                current_text.append(line)
        
        # Add final section
        if current_text:
            sections.append((current_section, '\n'.join(current_text)))
        
        # If no sections found, treat entire text as one section
        if not sections:
            sections = [("Full Report", text)]
        
        return sections
    
    def _chunk_text(
        self,
        text: str,
        chunk_size_chars: int,
        section: Optional[str]
    ) -> List[Dict[str, Any]]:
        """
        Chunk text into segments of approximately chunk_size_chars.
        
        Args:
            text: Text to chunk
            chunk_size_chars: Target chunk size in characters
            section: Section name (optional)
        
        Returns:
            List of chunk dictionaries
        """
        chunks = []
        text_length = len(text)
        
        if text_length <= chunk_size_chars:
            # Text fits in one chunk
            chunks.append({
                'chunk_text': text,
                'section': section,
                'start_char': 0,
                'end_char': text_length
            })
            return chunks
        
        # Split into chunks with overlap
        start = 0
        chunk_index = 0
        
        while start < text_length:
            end = start + chunk_size_chars
            
            # If not the last chunk, try to break at sentence boundary
            if end < text_length:
                # Look for sentence endings within the last 20% of chunk
                search_start = max(start, end - int(chunk_size_chars * 0.2))
                sentence_end = self._find_sentence_boundary(text, search_start, end)
                
                if sentence_end > start:
                    end = sentence_end
            
            chunk_text = text[start:end].strip()
            
            if chunk_text:
                chunks.append({
                    'chunk_text': chunk_text,
                    'section': section,
                    'start_char': start,
                    'end_char': end
                })
            
            # Move start position with overlap
            start = end - self.overlap_chars
            if start >= text_length:
                break
        
        return chunks
    
    def _find_sentence_boundary(self, text: str, start: int, end: int) -> int:
        """
        Find the best sentence boundary near the end position.
        
        Args:
            text: Full text
            start: Start of search range
            end: End of search range
        
        Returns:
            Character position of sentence boundary
        """
        # Look for sentence endings (., !, ?) followed by space and capital letter
        pattern = r'[.!?]\s+[A-Z]'
        
        # Search backwards from end
        search_text = text[start:end]
        matches = list(re.finditer(pattern, search_text))
        
        if matches:
            # Use the last match
            match = matches[-1]
            return start + match.end() - 1  # Position before the capital letter
        
        # If no sentence boundary found, return original end
        return end
    
    def estimate_tokens(self, text: str) -> int:
        """
        Estimate token count for text (approximate).
        
        Args:
            text: Text to estimate
        
        Returns:
            Estimated token count
        """
        # Rough approximation: 1 token ≈ 4 characters
        return len(text) // 4

