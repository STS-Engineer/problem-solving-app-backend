
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, DateTime,  ForeignKey, Enum as SQLEnum,
)
from sqlalchemy.orm import relationship
from app.db.base import Base

from app.models.enums import PlantEnum



class Report(Base):
    """Investigation reports with 8-step process (D1-D8)"""
    __tablename__ = 'reports'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    complaint_id = Column(Integer, ForeignKey('complaints.id', ondelete='CASCADE'), nullable=False, unique=True, index=True)
    report_number = Column(String(50), nullable=False, unique=True, index=True)
    title = Column(String(255), nullable=False)
    summary = Column(Text)
    plant = Column(SQLEnum(PlantEnum, name='plant_enum'), nullable=False, index=True)
    created_by = Column(Integer, ForeignKey('users.id', ondelete='RESTRICT'), nullable=False, index=True)
    reviewed_by = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), index=True)
    status = Column(String(50), nullable=False, default='draft', index=True, comment="draft|in_progress|submitted|under_review|approved|rejected")
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=datetime.utcnow)
    submitted_at = Column(DateTime)
    approved_at = Column(DateTime)
    
    # OPTIONAL: Add these fields for time tracking (uncomment when ready)
    # d1_date = Column(DateTime, nullable=True, comment="D1 step completion date")
    # d3_date = Column(DateTime, nullable=True, comment="D3 step completion date")
    # d5_date = Column(DateTime, nullable=True, comment="D5 step completion date")
    # d8_date = Column(DateTime, nullable=True, comment="D8 step completion date")
    # llc_date = Column(DateTime, nullable=True, comment="Lessons Learned completion date")
    
    # OPTIONAL: Add these fields for cost tracking (uncomment when ready)
    # d13_cost = Column(Numeric(12, 2), nullable=True, comment="Cost from D1 to D3")
    # d45_cost = Column(Numeric(12, 2), nullable=True, comment="Cost from D4 to D5")
    # d68_cost = Column(Numeric(12, 2), nullable=True, comment="Cost from D6 to D8")
    # llc_cost = Column(Numeric(12, 2), nullable=True, comment="Lessons Learned Cost")
    


    # Relationships
    complaint = relationship("Complaint", back_populates="report")
    creator = relationship("User", foreign_keys=[created_by], back_populates="created_reports")
    reviewer = relationship("User", foreign_keys=[reviewed_by], back_populates="reviewed_reports")
    steps = relationship("ReportStep", back_populates="report", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Report(id={self.id}, number='{self.report_number}', status='{self.status}')>"

