# app/services/step_service.py

from typing import Optional, List
from sqlalchemy.orm import Session

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

import logging

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


class StepService:

    # ─────────────────────────────────────────────────────────────────────────
    # READ
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def get_step_by_id(db: Session, step_id: int) -> Optional[ReportStep]:
        return db.query(ReportStep).filter(ReportStep.id == step_id).first()

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
    def list_steps(db: Session, report_id: int) -> List[ReportStep]:
        return (
            db.query(ReportStep)
            .filter(
                ReportStep.report_id == report_id,
            )
            .order_by(ReportStep.step_code)
            .all()
        )

    @staticmethod
    def get_next_step(db: Session, report_id: int) -> Optional[ReportStep]:
        return (
            db.query(ReportStep)
            .filter(
                ReportStep.report_id == report_id,
                ReportStep.status == "draft",
            )
            .order_by(ReportStep.step_code)
            .first()
        )
