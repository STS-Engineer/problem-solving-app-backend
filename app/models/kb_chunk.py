from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, 
    ForeignKey , Index, 
    UniqueConstraint, 
)
from sqlalchemy.orm import relationship
from app.db.base import Base
from sqlalchemy.dialects.postgresql import  TSVECTOR


class KBChunk(Base):
    """Chunked knowledge base content with full-text search"""
    __tablename__ = 'kb_chunks'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    file_id = Column(Integer, ForeignKey('files.id', ondelete='CASCADE'), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)
    page_from = Column(Integer)
    page_to = Column(Integer)
    content = Column(Text, nullable=False)
    tsv = Column(TSVECTOR, comment="Full-text search vector")
    section_hint = Column(String(255))
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    
    # Relationships
    file = relationship("File", back_populates="kb_chunks")
    
    # Constraints
    __table_args__ = (
        UniqueConstraint('file_id', 'chunk_index', name='unique_file_chunk'),
        Index('idx_kb_chunks_tsv', 'tsv', postgresql_using='gin'),
    )
    
    def __repr__(self):
        return f"<KBChunk(id={self.id}, file_id={self.file_id}, chunk_index={self.chunk_index})>"

