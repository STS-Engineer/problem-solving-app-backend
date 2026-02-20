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
    report_step_id = Column(Integer, ForeignKey('report_steps.id', ondelete='CASCADE'), nullable=False, index=True)
    decision = Column(String(20), nullable=False, index=True, comment="pass|fail")
    missing = Column(ARRAY(Text), comment="Missing elements")
    issues = Column(ARRAY(Text), comment="Identified issues")
    suggestions = Column(ARRAY(Text), comment="Improvement suggestions")
    professional_rewrite = Column(Text, comment="AI-generated rewrite")
    validated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    notes = Column(Text)
    section_key          = Column(String(64), nullable=True,
                                  comment="NULL=full step, or section name e.g. 'five_w_2h'")
    # Relationships
    report_step = relationship("ReportStep", back_populates="validation")
    
    def __repr__(self):
        section = f" section={self.section_key}" if self.section_key else " full-step"
        return f"<StepValidation(id={self.id},{section}, decision='{self.decision}')>"
