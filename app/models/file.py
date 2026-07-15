from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    BigInteger,
    ForeignKey,
    CheckConstraint,
)
from sqlalchemy.orm import relationship
from app.db.base import Base


class File(Base):
    """File storage with purpose classification"""

    __tablename__ = "files"

    id = Column(Integer, primary_key=True, autoincrement=True)
    purpose = Column(
        String(50), nullable=False, index=True, comment="report|ikb|evidence"
    )
    original_name = Column(String(255), nullable=False)
    stored_path = Column(String(500), nullable=False, unique=True)
    size_bytes = Column(BigInteger, nullable=False)
    mime_type = Column(String(100))
    # Nullable: files that arrive by email (via the MCP intake) have no user.
    uploaded_by = Column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    # Origin of the file — e.g. 'email_intake' for MCP-ingested attachments.
    source = Column(String(30), nullable=True, index=True)
    # Direct ownership links (no StepFile needed for intake attachments).
    intake_id = Column(
        Integer,
        ForeignKey("email_intake.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    complaint_id = Column(
        Integer,
        ForeignKey("complaints.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    description = Column(String(1000), nullable=True, comment="Agent-generated file description")
    created_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True
    )
    checksum = Column(String(64), comment="SHA-256 hash")

    # Relationships
    uploader = relationship("User", back_populates="uploaded_files")
    step_files = relationship(
        "StepFile", back_populates="file", cascade="all, delete-orphan"
    )
    kb_chunks = relationship(
        "KBChunk", back_populates="file", cascade="all, delete-orphan"
    )

    # Constraints
    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="check_file_size"),
        CheckConstraint(
            "purpose IN ('report', 'ikb', 'evidence')", name="check_purpose"
        ),
    )

    def __repr__(self):
        return (
            f"<File(id={self.id}, "
            f"name='{self.original_name}', "
            f"purpose='{self.purpose}')>"
        )
