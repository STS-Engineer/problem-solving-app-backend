from sqlalchemy import (
    Column, Integer, String,  DateTime, ForeignKey, Index,
    UniqueConstraint, 
)
from sqlalchemy.orm import relationship
from app.db.base import Base
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime, timezone


class ReportStep(Base):
    """Individual steps (D1-D8) within investigation reports"""
    __tablename__ = 'report_steps'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    report_id = Column(Integer, ForeignKey('reports.id', ondelete='CASCADE'), nullable=False, index=True)
    step_code = Column(String(10), nullable=False, comment="D1|D2|D3|D4|D5|D6|D7|D8")
    step_name = Column(String(255), nullable=False)
    status = Column(String(50), nullable=False, default='draft', index=True, comment="draft|submitted|validated|rejected")
    data = Column(JSONB, comment="Flexible JSON storage for step-specific data")
    completed_by = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'))
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    completed_at = Column(DateTime)
    
    # Relationships
    report = relationship("Report", back_populates="steps")
    completer = relationship("User", foreign_keys=[completed_by], back_populates="completed_steps")
    validation = relationship("StepValidation", back_populates="report_step", uselist=False)
    step_files = relationship("StepFile", back_populates="report_step", cascade="all, delete-orphan")
    
    # Constraints
    __table_args__ = (
        UniqueConstraint('report_id', 'step_code', name='unique_report_step'),
        Index('idx_report_steps_data', 'data', postgresql_using='gin'),
    )
    
    def __repr__(self):
        return f"<ReportStep(id={self.id}, code='{self.step_code}', status='{self.status}')>"
