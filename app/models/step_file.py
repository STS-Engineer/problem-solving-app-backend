
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, Text, DateTime, ForeignKey,
    UniqueConstraint
)
from sqlalchemy.orm import relationship
from app.db.base import Base


class StepFile(Base):
    """Junction table linking files to report steps"""
    __tablename__ = 'step_files'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    report_step_id = Column(Integer, ForeignKey('report_steps.id', ondelete='CASCADE'), nullable=False, index=True)
    file_id = Column(Integer, ForeignKey('files.id', ondelete='CASCADE'), nullable=False, index=True)
    attachment_order = Column(Integer, default=0)
    description = Column(Text)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    
    # Relationships
    report_step = relationship("ReportStep", back_populates="step_files")
    file = relationship("File", back_populates="step_files")
    
    # Constraints
    __table_args__ = (
        UniqueConstraint('report_step_id', 'file_id', name='unique_step_file'),
    )
    
    def __repr__(self):
        return f"<StepFile(id={self.id}, report_step_id={self.report_step_id}, file_id={self.file_id})>"
