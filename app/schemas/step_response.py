from pydantic import BaseModel


class StepSummaryItem(BaseModel):
    id: int
    step_code: str
    status: str


class StepsSummaryResponse(BaseModel):
    complaint_status: str
    steps: list[StepSummaryItem]