# app/schemas/report.py
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime

class ReportCreate(BaseModel):
    title: str
    plant: str
    summary: Optional[str] = None

class StepDataUpdate(BaseModel):
    data: Dict[Any, Any]

class StepSubmit(BaseModel):
    """Marquer l'étape comme complète"""
    pass

class StepValidationCreate(BaseModel):
    decision: str  # pass | fail
    missing: Optional[List[str]] = None
    issues: Optional[List[str]] = None
    suggestions: Optional[List[str]] = None
    notes: Optional[str] = None

class StepResponse(BaseModel):
    id: int
    step_code: str
    step_name: str
    status: str
    data: Dict[Any, Any]
    completed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

class ReportProgressResponse(BaseModel):
    total_steps: int
    completed_steps: int
    progress_percentage: float
    current_step: Optional[StepResponse]