from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, DateTime,  ForeignKey, ARRAY
)
from sqlalchemy.orm import relationship
from app.db.base import Base



class StepValidation(Base):
    """Validation results for report steps"""
    __tablename__ = 'step_validation'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    report_step_id = Column(Integer, ForeignKey('report_steps.id', ondelete='CASCADE'), nullable=False, unique=True, index=True)
    decision = Column(String(20), nullable=False, index=True, comment="pass|fail")
    missing = Column(ARRAY(Text), comment="Missing elements")
    issues = Column(ARRAY(Text), comment="Identified issues")
    suggestions = Column(ARRAY(Text), comment="Improvement suggestions")
    professional_rewrite = Column(Text, comment="AI-generated rewrite")
    validated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    notes = Column(Text)
    
    # Relationships
    report_step = relationship("ReportStep", back_populates="validation")
    
    def __repr__(self):
        return f"<StepValidation(id={self.id}, decision='{self.decision}')>"
