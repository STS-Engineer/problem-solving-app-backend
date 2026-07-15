from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.models.enums import PlantEnum


class PlantContactRead(BaseModel):
    plant: PlantEnum
    cqe_emails: List[str] = Field(default_factory=list)
    quality_manager_emails: List[str] = Field(default_factory=list)
    plant_manager_email: Optional[str] = None
    general_manager_email: Optional[str] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PlantContactUpdate(BaseModel):
    """All fields optional — only the provided ones are updated."""

    cqe_emails: Optional[List[str]] = None
    quality_manager_emails: Optional[List[str]] = None
    plant_manager_email: Optional[str] = None
    general_manager_email: Optional[str] = None
