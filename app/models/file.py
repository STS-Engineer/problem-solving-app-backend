from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String,  DateTime,
    BigInteger, ForeignKey, CheckConstraint
)
from sqlalchemy.orm import relationship
from app.db.base import Base



class File(Base):
    """File storage with purpose classification"""
    __tablename__ = 'files'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    purpose = Column(String(50), nullable=False, index=True, comment="report|ikb|evidence")
    original_name = Column(String(255), nullable=False)
    stored_path = Column(String(500), nullable=False, unique=True)
    size_bytes = Column(BigInteger, nullable=False)
    mime_type = Column(String(100))
    uploaded_by = Column(Integer, ForeignKey('users.id', ondelete='RESTRICT'), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    checksum = Column(String(64), comment="SHA-256 hash")
    
    # Relationships
    uploader = relationship("User", back_populates="uploaded_files")
    step_files = relationship("StepFile", back_populates="file", cascade="all, delete-orphan")
    kb_chunks = relationship("KBChunk", back_populates="file", cascade="all, delete-orphan")
    
    # Constraints
    __table_args__ = (
        CheckConstraint('size_bytes > 0', name='check_file_size'),
        CheckConstraint("purpose IN ('report', 'ikb', 'evidence')", name='check_purpose'),
    )
    
    def __repr__(self):
        return f"<File(id={self.id}, name='{self.original_name}', purpose='{self.purpose}')>"
