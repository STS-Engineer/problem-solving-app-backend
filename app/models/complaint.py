from datetime import datetime, date, timezone
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    Date,
    ForeignKey,
    Enum as SQLEnum,
)
from sqlalchemy.orm import relationship
from app.db.base import Base

from app.models.enums import PlantEnum, ProductLineEnum


class Complaint(Base):
    """Product complaints matching exact form structure"""

    __tablename__ = "complaints"

    id = Column(Integer, primary_key=True, autoincrement=True)
    reference_number = Column(String(50), unique=True, nullable=False, index=True)
    complaint_name = Column(
        String(255), nullable=False, comment="Form: Complaint name *"
    )

    quality_issue_warranty = Column(
        String(100), comment="Form: Quality issue / Warranty *"
    )

    customer = Column(String(255), comment="Form: Customer *")
    customer_plant_name = Column(String(255), comment="Form: Customer plant name *")
    customer_complaint_date = Column(
        Date, index=True, comment="Form: Customer complaint date *"
    )

    avocarbon_plant = Column(
        SQLEnum(PlantEnum, name="plant_enum"), comment="Form: AVOCarbon plant *"
    )
    avocarbon_product_type = Column(
        String(100), comment="Form: AVOCarbon product type *"
    )
    potential_avocarbon_process_linked_to_problem = Column(
        String(500), comment="Form: Potential AVOCarbon process *"
    )
    concerned_application = Column(String(255), comment="Form: Concerned application *")

    product_line = Column(
        SQLEnum(ProductLineEnum, name="product_line_enum"),
        nullable=False,
        index=True,
        comment="Form: Product line *",
    )
    complaint_opening_date = Column(
        Date,
        nullable=False,
        default=date.today,
        comment="Form: Complaint opening date *",
    )
    complaint_description = Column(
        Text, comment="Form: Complaint description * (max 2000 chars)"
    )

    defects = Column(String(255), comment="Defects *")

    repetitive_complete_with_number = Column(Text, comment="Form: REPETITIVE number *")
    status = Column(
        String(50),
        nullable=False,
        default="open",
        index=True,
        comment="open|in_progress|under_review|resolved|closed|rejected",
    )
    priority = Column(
        String(20), default="normal", index=True, comment="low|normal|high|urgent"
    )

    reported_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="User who created complaint",
    )

    created_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True
    )
    updated_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    due_date = Column(
        DateTime, nullable=True, index=True, comment="Expected resolution date"
    )
    closed_at = Column(
        DateTime, nullable=True, index=True, comment="When complaint was closed"
    )

    cqt_email = Column(
        String(255),
        nullable=True,
        index=True,
        comment="Customer Quality Technician email (external or internal)",
    )
    quality_manager_email = Column(
        String(255),
        nullable=True,
        index=True,
        comment="AVOCarbon quality manager email",
    )
    plant_manager_email = Column(
        String(255),
        nullable=True,
        index=True,
        comment="Plant manager email — used for L3/L4 escalation",
    )
    approved_by_email = Column(
        String(255),
        nullable=True,
        comment="Email of person who approved/closed the complaint",
    )

    # Relationships
    reporter = relationship(
        "User", foreign_keys=[reported_by], back_populates="reported_complaints"
    )
    report = relationship("Report", back_populates="complaint", uselist=False)
    audit_logs = relationship(
        "ComplaintAuditLog",
        back_populates="complaint",
        cascade="all, delete-orphan",
        order_by="ComplaintAuditLog.created_at",
    )

    def __repr__(self):
        return (
            f"<Complaint(id={self.id}, "
            f"ref='{self.reference_number}', "
            f"status='{self.status}')>"
        )
