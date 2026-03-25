"""
app/services/plan_push_service.py
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.file import File
from app.models.plan_push_log import PlanPushLog
from app.models.report_step import ReportStep
from app.models.step_file import StepFile
from app.services.member_directory import MemberDirectory

logger = logging.getLogger(__name__)

EXTERNAL_API_URL = "https://sales-feedback.azurewebsites.net/api/plans"
HTTP_TIMEOUT = 20.0
MAX_RETRY_ATTEMPTS = 5
API_BASE_URL = "https://complaint-back.azurewebsites.net/api/v1"


class PlanPushService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.directory = MemberDirectory(db)

    # ── Public entry point ────────────────────────────────────────────────────

    def push_on_d6_fulfilled(self, step_id: int, cqt_email: str) -> None:
        try:
            step = self.db.query(ReportStep).filter(ReportStep.id == step_id).first()
            log_row = self._upsert_log_row(step.report_id, step_id)
            self.db.flush()
            self._attempt_push(log_row, step, cqt_email)
            self.db.commit()
        except Exception as exc:
            logger.error(
                "plan_push: unexpected error for step %s: %s",
                step_id,
                exc,
                exc_info=True,
            )
            try:
                self.db.rollback()
            except Exception:
                pass

    # ── Core push logic ───────────────────────────────────────────────────────

    def _attempt_push(
        self, log_row: PlanPushLog, step: ReportStep, cqt_email: str
    ) -> None:
        log_row.attempt_count += 1
        log_row.last_attempt_at = datetime.now(timezone.utc)
        try:
            payload = self._build_payload(step, cqt_email)
            log_row.payload = payload
            result = self._call_api(payload)
            log_row.status = "success"
            log_row.external_root_sujet_id = result.get("root_sujet_id")
            log_row.last_error = None
            logger.info(
                "plan_push: success for report %s → root_sujet_id=%s",
                step.report_id,
                log_row.external_root_sujet_id,
            )
        except Exception as exc:
            log_row.status = "failed"
            log_row.last_error = str(exc)[:1000]
            logger.warning(
                "plan_push: failed for report %s (attempt %s): %s",
                step.report_id,
                log_row.attempt_count,
                exc,
            )

    # ── Payload builder ───────────────────────────────────────────────────────

    def _build_payload(self, step: ReportStep, cqt_email: str) -> dict[str, Any]:
        complaint = step.report.complaint
        ref = complaint.reference_number
        name = complaint.complaint_name or ref
        desc = complaint.complaint_description or name

        d4_data = self._get_step_data(step.report_id, "D4") or {}
        root_occ = (d4_data.get("root_cause_occurrence") or {}).get("root_cause", "")
        root_det = (d4_data.get("root_cause_non_detection") or {}).get("root_cause", "")

        step_data = step.data or {}
        actions_occ = step_data.get("corrective_actions_occurrence") or []
        actions_det = step_data.get("corrective_actions_detection") or []

        evidence = self._get_evidence_map(step.id)

        sujets = []
        if actions_occ:
            sujets.append(
                {
                    "titre": "Corrective Actions – Occurrence",
                    "code": f"8D-{ref}-OCC",
                    "description": root_occ or "Root cause of occurrence",
                    "sous_sujets": [],
                    "actions": [
                        self._build_action(a, i, "occurrence", step.id, evidence)
                        for i, a in enumerate(actions_occ)
                    ],
                }
            )
        if actions_det:
            sujets.append(
                {
                    "titre": "Corrective Actions – Detection",
                    "code": f"8D-{ref}-DET",
                    "description": root_det or "Root cause of non-detection",
                    "sous_sujets": [],
                    "actions": [
                        self._build_action(a, i, "detection", step.id, evidence)
                        for i, a in enumerate(actions_det)
                    ],
                }
            )

        return {
            "version": "1.0",
            "plan_code": f"8D-{ref}",
            "plan_title": f"8D – {name} [{ref}]",
            "inserted_by": cqt_email,
            "sujets": sujets,
        }

    def _build_action(
        self,
        action: dict,
        index: int,
        action_type: str,
        step_id: int,
        evidence: dict[tuple[str, int], tuple[int, str]],
    ) -> dict[str, Any]:
        responsible_name = action.get("responsible", "")

        # Resolve email from avomembers via existing MemberDirectory
        email = ""
        if responsible_name.strip():
            bare_name = responsible_name.split(" — ")[0].strip()
            matches = self.directory.search(bare_name, limit=1)
            if matches:
                email = matches[0].email or ""

        # Evidence: accessible URL + filename, fallback to action text
        file_info = evidence.get((action_type, index))
        if file_info:
            file_id, original_name = file_info
            description = (
                f"{original_name} — " f"{API_BASE_URL}/steps/{step_id}/files/{file_id}"
            )
        else:
            description = action.get("action", "")

        return {
            "titre": action.get("action", ""),
            "description": description,
            "type": "action",
            "responsable": responsible_name,
            "email_responsable": email,
            "due_date": action.get("due_date") or None,
            "closed_date": action.get("imp_date") or None,
            "status": "closed" if action.get("imp_date") else "open",
            "priorite": index + 1,
            "sous_actions": [],
        }

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _get_evidence_map(self, step_id: int) -> dict[tuple[str, int], tuple[int, str]]:
        rows = self.db.execute(
            select(
                StepFile.action_type,
                StepFile.action_index,
                File.id,
                File.original_name,
            )
            .join(File, StepFile.file_id == File.id)
            .where(
                StepFile.report_step_id == step_id,
                StepFile.action_type.isnot(None),
                StepFile.action_index.isnot(None),
            )
        ).fetchall()
        return {(r.action_type, r.action_index): (r.id, r.original_name) for r in rows}

    def _get_step_data(self, report_id: int, step_code: str) -> Optional[dict]:
        row = (
            self.db.query(ReportStep.data)
            .filter(
                ReportStep.report_id == report_id,
                ReportStep.step_code == step_code,
            )
            .first()
        )
        return row.data if row else None

    def _upsert_log_row(self, report_id: int, step_id: int) -> PlanPushLog:
        row = (
            self.db.query(PlanPushLog)
            .filter(PlanPushLog.report_id == report_id)
            .first()
        )
        if row:
            row.step_id = step_id
            row.status = "pending"
            row.last_error = None
        else:
            row = PlanPushLog(report_id=report_id, step_id=step_id, status="pending")
            self.db.add(row)
        return row

    @staticmethod
    def _call_api(payload: dict) -> dict:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            response = client.post(EXTERNAL_API_URL, json=payload)
            if response.status_code >= 400:
                logger.error(
                    "plan_push: API %s — %s", response.status_code, response.text[:500]
                )
            response.raise_for_status()
            return response.json()
