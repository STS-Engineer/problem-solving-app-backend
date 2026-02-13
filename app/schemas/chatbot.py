from pydantic import BaseModel, Field, validator
from typing import Dict, List, Optional
from datetime import datetime


class StepValidationRequest(BaseModel):
    report_step_id: int = Field(..., gt=0)
    step_code: str = Field(..., min_length=2, max_length=2)
    step_data: Optional[Dict] = None  # ← MODIFIÉ : rendre optionnel

    @validator("step_code")
    def validate_step_code(cls, v):
        valid = [f"D{i}" for i in range(1, 9)]
        if v not in valid:
            raise ValueError(f"step_code must be one of {valid}")
        return v


class ValidationFeedback(BaseModel):
    decision: str
    missing_fields: List[str] = []
    incomplete_fields: List[str] = []
    quality_issues: List[str] = []
    rules_violations: List[str] = []
    suggestions: List[str] = []
    field_improvements: Dict[str, str] = {}
    overall_assessment: Optional[str] = None
    language_detected: Optional[str] = "en"
    validated_at: Optional[datetime] = None

    @validator("decision")
    def validate_decision(cls, v):
        if v not in ["pass", "fail"]:
            raise ValueError("decision must be 'pass' or 'fail'")
        return v


class StepValidationResponse(BaseModel):
    success: bool
    validation: ValidationFeedback
    message: str
    can_proceed: bool
    report_step_id: int


class HealthCheckResponse(BaseModel):
    status: str
    service: str
    kb_chunks_available: int
    twenty_rules_loaded: bool
    message: str