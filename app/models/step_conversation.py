# app/models/step_conversation.py
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, Index
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.db.base import Base

class StepConversation(Base):
    """
    Stores the chatbot conversation messages for each section of a step.

    role:          'assistant' (bot question/feedback) | 'user' (user answer)
    section_key:   e.g. 'five_w_2h', 'deviation', 'is_is_not'
    message_index: 0-based order within the section conversation
    meta:          optional JSONB — extracted fields, confidence scores, etc.
    """
    __tablename__ = 'step_conversations'

    id             = Column(Integer, primary_key=True, autoincrement=True)
    report_step_id = Column(
        Integer,
        ForeignKey('report_steps.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    section_key    = Column(String(64),  nullable=False)
    role           = Column(String(16),  nullable=False)   # 'assistant' | 'user'
    content        = Column(Text,        nullable=False)
    message_index  = Column(Integer,     nullable=False)
    meta           = Column(JSONB,       nullable=True)
    created_at     = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    report_step = relationship('ReportStep', back_populates='conversations')

    __table_args__ = (
        Index('idx_step_conv_step_section', 'report_step_id', 'section_key'),
        Index('idx_step_conv_ordering',     'report_step_id', 'section_key', 'message_index'),
    )

    def __repr__(self) -> str:
        return (
            f"<StepConversation(id={self.id}, step={self.report_step_id}, "
            f"section='{self.section_key}', role='{self.role}', idx={self.message_index})>"
        )