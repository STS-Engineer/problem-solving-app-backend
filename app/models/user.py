from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, 
     
)
from sqlalchemy.orm import relationship
from app.db.base import Base

class User(Base):
    """System users with role-based access control"""
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(100), nullable=False, unique=True, index=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    first_name = Column(String(100))
    last_name = Column(String(100))
    role = Column(String(50), nullable=False, default='user', index=True)
    # Roles: 'admin', 'quality_manager', 'engineer', 'validator', 'user'
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    # Relationships
    reported_complaints = relationship("Complaint", foreign_keys="Complaint.reported_by", back_populates="reporter")
    assigned_complaints = relationship("Complaint", foreign_keys="Complaint.assigned_to", back_populates="assignee")
    managed_complaints = relationship("Complaint", foreign_keys="Complaint.quality_manager", back_populates="manager")
    created_reports = relationship("Report", foreign_keys="Report.created_by", back_populates="creator")
    reviewed_reports = relationship("Report", foreign_keys="Report.reviewed_by", back_populates="reviewer")
    completed_steps = relationship("ReportStep", foreign_keys="ReportStep.completed_by", back_populates="completer")
    uploaded_files = relationship("File", back_populates="uploader")
    
    def __repr__(self):
        return f"<User(id={self.id}, username='{self.username}', role='{self.role}')>"
