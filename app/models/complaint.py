
from datetime import datetime, date, timezone
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Date, ForeignKey, Enum as SQLEnum,
)
from sqlalchemy.orm import relationship
from app.db.base import Base

from app.models.enums import PlantEnum, ProductLineEnum


class Complaint(Base):
    """Product complaints matching exact form structure"""
    __tablename__ = 'complaints'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    

    complaint_name = Column(String(255), nullable=False, comment="Form: Complaint name *")
    quality_issue_warranty = Column(String(100), comment="Form: Quality issue / Warranty *")
    customer = Column(String(255), comment="Form: Customer *")
    customer_plant_name = Column(String(255), comment="Form: Customer plant name *")
    avocarbon_plant = Column(SQLEnum(PlantEnum, name='plant_enum'), comment="Form: AVOCarbon plant *")
    avocarbon_product_type = Column(String(100), comment="Form: AVOCarbon product type *")
    potential_avocarbon_process_linked_to_problem = Column(String(500), comment="Form: Potential AVOCarbon process *")
    

    product_line = Column(SQLEnum(ProductLineEnum, name='product_line_enum'), nullable=False, index=True, comment="Form: Product line *")
    concerned_application = Column(String(255), comment="Form: Concerned application *")
    customer_complaint_date = Column(Date, index=True, comment="Form: Customer complaint date *")
    complaint_opening_date = Column(Date, nullable=False, default=date.today, comment="Form: Complaint opening date *")
    complaint_description = Column(Text, comment="Form: Complaint description * (max 2000 chars)")
    defects = Column(String(255), comment="Form: Defects *")
    quality_manager = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), index=True, comment="Form: Quality manager *")
    repetitive_complete_with_number = Column(Text, comment="Form: REPETITIVE number *")
    

    reported_by = Column(Integer, ForeignKey('users.id', ondelete='RESTRICT'), nullable=False, index=True, comment="User who created complaint")
    assigned_to = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), index=True, comment="User assigned to handle complaint")
    status = Column(String(50), nullable=False, default='open', index=True, comment="open|in_progress|under_review|resolved|closed|rejected")
    severity = Column(String(20), default='medium', index=True, comment="low|medium|high|critical")
    priority = Column(String(20), default='normal', index=True, comment="low|normal|high|urgent")
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    resolved_at = Column(DateTime)
    
    # Relationships
    reporter = relationship("User", foreign_keys=[reported_by], back_populates="reported_complaints")
    assignee = relationship("User", foreign_keys=[assigned_to], back_populates="assigned_complaints")
    manager = relationship("User", foreign_keys=[quality_manager], back_populates="managed_complaints")
    report = relationship("Report", back_populates="complaint", uselist=False)
    
    def __repr__(self):
        return f"<Complaint(id={self.id}, name='{self.complaint_name}', status='{self.status}')>"

