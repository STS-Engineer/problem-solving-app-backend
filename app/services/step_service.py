import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.core.exceptions import (
    ComplaintNotFoundError,
    InvalidStepCodeError,
    ReportNotFoundError,
    StepNotFoundError,
)
from app.models.complaint import Complaint
from app.models.report import Report
from app.models.report_step import ReportStep
from app.schemas.step_data import (
    D1Data,
    D2Data,
    D3Data,
    D4Data,
    D5Data,
    D6Data,
    D7Data,
    D8Data,
)

logger = logging.getLogger(__name__)

STEP_SCHEMAS = {
    "D1": D1Data,
    "D2": D2Data,
    "D3": D3Data,
    "D4": D4Data,
    "D5": D5Data,
    "D6": D6Data,
    "D7": D7Data,
    "D8": D8Data,
}

VALID_STEP_CODES = tuple(STEP_SCHEMAS.keys())


class StepService:
    @staticmethod
    def validate_step_code(step_code: str) -> None:
        if step_code not in VALID_STEP_CODES:
            raise InvalidStepCodeError("Invalid step code")

    @staticmethod
    def get_complaint_by_reference(
        db: Session,
        reference_number: str,
    ) -> Complaint:
        complaint = (
            db.query(Complaint)
            .filter(Complaint.reference_number == reference_number)
            .first()
        )

        if complaint is None:
            raise ComplaintNotFoundError("Complaint not found")

        return complaint

    @staticmethod
    def get_report_by_complaint_id(
        db: Session,
        complaint_id: int,
    ) -> Report:
        report = (
            db.query(Report)
            .filter(Report.complaint_id == complaint_id)
            .first()
        )

        if report is None:
            raise ReportNotFoundError("No 8D report found for this complaint")

        return report

    @staticmethod
    def get_step_by_code(
        db: Session,
        report_id: int,
        step_code: str,
    ) -> Optional[ReportStep]:
        return (
            db.query(ReportStep)
            .filter(
                ReportStep.report_id == report_id,
                ReportStep.step_code == step_code,
            )
            .first()
        )

    @staticmethod
    def get_step_by_complaint_and_code(
        db: Session,
        reference_number: str,
        step_code: str,
    ) -> ReportStep:
        StepService.validate_step_code(step_code)

        complaint = StepService.get_complaint_by_reference(
            db=db,
            reference_number=reference_number,
        )
        report = StepService.get_report_by_complaint_id(
            db=db,
            complaint_id=complaint.id,
        )

        step = StepService.get_step_by_code(
            db=db,
            report_id=report.id,
            step_code=step_code,
        )

        if step is None:
            raise StepNotFoundError("Step not found")

        return step

    @staticmethod
    def get_steps_summary_by_complaint(
        db: Session,
        reference_number: str,
    ) -> dict:
        complaint = StepService.get_complaint_by_reference(
            db=db,
            reference_number=reference_number,
        )
        report = StepService.get_report_by_complaint_id(
            db=db,
            complaint_id=complaint.id,
        )

        steps = (
            db.query(ReportStep.id, ReportStep.step_code, ReportStep.status)
            .filter(ReportStep.report_id == report.id)
            .order_by(ReportStep.step_code)
            .all()
        )

        return {
            "complaint_status": complaint.status,
            "steps": [
                {
                    "id": step.id,
                    "step_code": step.step_code,
                    "status": step.status,
                }
                for step in steps
            ],
        }