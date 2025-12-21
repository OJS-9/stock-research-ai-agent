"""
Report storage service integrating database, chunking, and embeddings.
"""

from typing import Dict, Any, Optional, List
import uuid

from database import get_database_manager
from report_chunker import ReportChunker
from embedding_service import EmbeddingService


class ReportStorage:
    """Service for storing reports with chunking and embeddings."""
    
    def __init__(self):
        """Initialize report storage service."""
        self._db = None  # Lazy initialization - only connect when needed
        self.chunker = ReportChunker()
        self.embedding_service = EmbeddingService()
    
    @property
    def db(self):
        """Lazy initialization of database manager."""
        if self._db is None:
            self._db = get_database_manager()
        return self._db
    
    def store_report(
        self,
        ticker: str,
        trade_type: str,
        report_text: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Store a report with chunking and embeddings.
        
        Args:
            ticker: Stock ticker symbol
            trade_type: Type of trade
            report_text: Full report text
            metadata: Optional metadata dictionary
        
        Returns:
            report_id: Generated report ID
        """
        # Save report to database
        report_id = self.db.save_report(
            ticker=ticker,
            trade_type=trade_type,
            report_text=report_text,
            metadata=metadata
        )
        
        # Chunk the report
        print(f"Chunking report {report_id}...")
        chunks = self.chunker.chunk_report(report_text, preserve_sections=True)
        print(f"Created {len(chunks)} chunks")
        
        # Create embeddings for chunks
        print(f"Creating embeddings for {len(chunks)} chunks...")
        chunk_texts = [chunk['chunk_text'] for chunk in chunks]
        embeddings = self.embedding_service.create_embeddings_batch(chunk_texts)
        
        # Add embeddings to chunks
        for i, chunk in enumerate(chunks):
            chunk['embedding'] = embeddings[i] if i < len(embeddings) else None
        
        # Save chunks to database
        print(f"Saving chunks to database...")
        self.db.save_chunks(report_id, chunks)
        
        print(f"✓ Report {report_id} stored with {len(chunks)} chunks")
        
        return report_id
    
    def get_report(self, report_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a report by ID.
        
        Args:
            report_id: Report ID
        
        Returns:
            Report dictionary or None if not found
        """
        return self.db.get_report(report_id)
    
    def get_report_chunks(
        self,
        report_id: str,
        include_embeddings: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Retrieve all chunks for a report.
        
        Args:
            report_id: Report ID
            include_embeddings: Whether to include embeddings
        
        Returns:
            List of chunk dictionaries
        """
        return self.db.get_chunks_by_report(report_id, include_embeddings=include_embeddings)
    
    def get_reports_by_ticker(self, ticker: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get recent reports for a ticker.
        
        Args:
            ticker: Stock ticker symbol
            limit: Maximum number of reports
        
        Returns:
            List of report dictionaries
        """
        return self.db.get_reports_by_ticker(ticker, limit=limit)
    
    def delete_report(self, report_id: str):
        """
        Delete a report and all its chunks.
        
        Args:
            report_id: Report ID
        """
        self.db.delete_report(report_id)

