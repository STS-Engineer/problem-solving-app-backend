from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.models.enums import PlantEnum, ProductLineEnum


class ComplaintBase(BaseModel):
    complaint_name: str = Field(..., max_length=255)
    quality_issue_warranty: Optional[str] = Field(None, max_length=100)
    customer: Optional[str] = Field(None, max_length=255)
    customer_plant_name: Optional[str] = Field(None, max_length=255)
    avocarbon_plant: Optional[PlantEnum] = None
    avocarbon_product_type: Optional[str] = Field(None, max_length=100)
    potential_avocarbon_process_linked_to_problem: Optional[str] = Field(None, max_length=500)

    product_line: ProductLineEnum
    concerned_application: Optional[str] = Field(None, max_length=255)
    customer_complaint_date: Optional[date] = None
    complaint_opening_date: Optional[date] = None

    complaint_description: Optional[str] = None
    defects: Optional[str] = Field(None, max_length=255)
    repetitive_complete_with_number: Optional[str] = None

    cqt_email: Optional[str] = None
    quality_manager_email: Optional[str] = None
    plant_manager_email: Optional[str] = None
    approved_by_email: Optional[str] = None
    status: Optional[str] = Field("open", max_length=50)
    priority: Optional[str] = Field("normal", max_length=20)
    
    due_date: Optional[datetime] = None

class ComplaintCreate(ComplaintBase):
    # the authenticated user is the reporter; we still accept it explicitly for now
    reported_by: int


class ComplaintUpdate(BaseModel):
    complaint_name: Optional[str] = Field(None, max_length=255)
    quality_issue_warranty: Optional[str] = Field(None, max_length=100)
    customer: Optional[str] = Field(None, max_length=255)
    customer_plant_name: Optional[str] = Field(None, max_length=255)
    avocarbon_plant: Optional[PlantEnum] = None
    avocarbon_product_type: Optional[str] = Field(None, max_length=100)
    potential_avocarbon_process_linked_to_problem: Optional[str] = Field(None, max_length=500)

    product_line: Optional[ProductLineEnum] = None
    concerned_application: Optional[str] = Field(None, max_length=255)
    customer_complaint_date: Optional[date] = None
    complaint_opening_date: Optional[date] = None

    complaint_description: Optional[str] = None
    defects: Optional[str] = Field(None, max_length=255)
    quality_manager: Optional[int] = None
    repetitive_complete_with_number: Optional[str] = None

    cqt_email: Optional[str] = None
    status: Optional[str] = Field(None, max_length=50)
    severity: Optional[str] = Field(None, max_length=20)
    priority: Optional[str] = Field(None, max_length=20)
    resolved_at: Optional[datetime] = None

    # due_date: Optional[datetime] = None
    # closed_at: Optional[datetime] = None

class ComplaintRead(ComplaintBase):
    id: int
    reported_by: int
    created_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime] = None
    reference_number: str
    class Config:
        from_attributes = True

class ComplaintListItem(BaseModel):
    """Lightweight schema for list views"""
    id: int
    reference_number: str
    complaint_name: str
    customer: Optional[str]
    customer_plant_name: Optional[str]
    avocarbon_product_type: Optional[str]
    concerned_application: Optional[str]
    product_line: str
    avocarbon_plant: Optional[str]
    status: str
    quality_issue_warranty: Optional[str]
    defects: Optional[str]
    customer_complaint_date: Optional[date]
    complaint_opening_date: Optional[date]
    repetitive_complete_with_number: Optional[str]
    created_at: datetime
    potential_avocarbon_process_linked_to_problem: Optional[str]
    complaint_description: Optional[str]  
    closed_at: Optional[datetime] 

    has_export_report: bool = False
    export_filename: Optional[str] = None
    current_step_code: Optional[str] = None    
    all_completed: bool = False
    has_report: bool = False
    cqt_email: Optional[str]                 
    quality_manager_email: Optional[str]     
    plant_manager_email: Optional[str]       
    class Config:
        from_attributes = True

class ComplaintSyncResponse(BaseModel):
    complaints: List[ComplaintRead]
    count: int
    next_poll_after: str
    
    class Config:
        from_attributes = True