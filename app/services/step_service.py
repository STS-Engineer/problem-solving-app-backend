# app/services/step_service.py

import json
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder

from app.models.complaint import Complaint
from app.models.report import Report
from app.models.report_step import ReportStep
from app.models.step_validation import StepValidation
from app.schemas.step_data import (
    D1Data, D2Data, D3Data, D4Data, D5Data, D6Data, D7Data, D8Data
)
from app.services.chatbot_service import ChatbotService
from app.services.section_config import (
    STEP_SECTIONS,
    get_section_fields,
    get_all_section_keys,
)


STEP_SCHEMAS = {
    'D1': D1Data,
    'D2': D2Data,
    'D3': D3Data,
    'D4': D4Data,
    'D5': D5Data,
    'D6': D6Data,
    'D7': D7Data,
    'D8': D8Data,
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
        return db.query(ReportStep).filter(
            ReportStep.report_id == report_id,
            ReportStep.step_code == step_code,
        ).first()

    @staticmethod
    def list_steps(db: Session, report_id: int) -> List[ReportStep]:
        return db.query(ReportStep).filter(
            ReportStep.report_id == report_id,
        ).order_by(ReportStep.step_code).all()

    @staticmethod
    def get_next_step(db: Session, report_id: int) -> Optional[ReportStep]:
        return db.query(ReportStep).filter(
            ReportStep.report_id == report_id,
            ReportStep.status.in_(['draft', 'rejected']),
        ).order_by(ReportStep.step_code).first()

    # ─────────────────────────────────────────────────────────────────────────
    # SAVE DRAFT
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def save_step_progress(
        db: Session,
        step_id: int,
        data: Dict[Any, Any],
        validate_schema: bool = True,
    ) -> ReportStep:
        step = db.query(ReportStep).filter(ReportStep.id == step_id).first()
        if not step:
            raise HTTPException(status_code=404, detail="Step not found")

        if validate_schema:
            schema_class = STEP_SCHEMAS.get(step.step_code)
            if schema_class:
                try:
                    validated_data = schema_class(**data)
                    data = validated_data.model_dump(mode="json")
                except Exception as e:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Invalid data format for {step.step_code}: {str(e)}",
                    )

        if step.data is None:
            step.data = {}
        merged = {**step.data, **data}
        step.data = jsonable_encoder(merged)
        step.updated_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(step)
        return step

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION VALIDATION  ← NEW
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def submit_section(
        db: Session,
        step_id: int,
        section_key: str,
    ) -> Dict[str, Any]:
        """
        Validate a single named section of a step via the AI coach.

        Flow:
          1. Load step + verify it exists and is in draft/rejected state.
          2. Extract only the fields relevant to this section.
          3. Call the AI (chatbot_service) with a section-scoped coaching hint.
          4. Upsert a StepSectionValidation row.
          5. If ALL sections for this step now have decision='pass',
             mark the step as 'validated' (same behaviour as old submit_step).

        Returns:
            {
              "validation": ValidationResult dict,
              "all_sections_passed": bool,
              "passed_sections": [str],
              "remaining_sections": [str],
            }
        """
        step = db.query(ReportStep).filter(ReportStep.id == step_id).first()
        if not step:
            raise HTTPException(status_code=404, detail="Step not found")

        if step.status not in ('draft', 'rejected', 'submitted'):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot validate section on a step with status '{step.status}'",
            )

        # D1 uses local-only validation — no sections
        if step.step_code == "D1":
            raise HTTPException(
                status_code=400,
                detail="D1 uses full-step local validation. Use /submit instead.",
            )

        # Verify section_key is valid for this step
        step_sections = STEP_SECTIONS.get(step.step_code)
        if not step_sections:
            raise HTTPException(
                status_code=400,
                detail=f"Per-section validation not configured for {step.step_code}",
            )
        if section_key not in step_sections:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown section '{section_key}' for {step.step_code}. "
                       f"Valid sections: {list(step_sections.keys())}",
            )

        # Extract section data slice
        full_data = step.data or {}
        fields = get_section_fields(step.step_code, section_key)
        section_data = {k: full_data.get(k) for k in fields}

        # Build a scoped step_code string so the AI loads the right coaching chunk
        # e.g.  "D2_five_w_2h"  →  KB hint: "D2_five_w_2h_coaching_validation"
        scoped_code = f"{step.step_code}_{section_key}"

        # ── AI validation ────────────────────────────────────────────────────
        try:
            chatbot = ChatbotService(db)
            validation_result = chatbot.validate_step(
                report_step_id=step_id,
                step_code=scoped_code,
                step_data=section_data,
            )
        except ValueError as e:
            raise HTTPException(status_code=500, detail=f"Validation failed: {str(e)}")
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=f"AI service unavailable: {str(e)}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")

        # ── Persist per-section result ────────────────────────────────────────
        StepService._upsert_section_validation(
            db, step_id, section_key, validation_result
        )
        db.commit()

        # ── Check if all sections have now passed ─────────────────────────────
        all_section_keys = get_all_section_keys(step.step_code)
        passed, remaining = StepService._get_section_status(
            db, step_id, all_section_keys
        )
        all_sections_passed = len(remaining) == 0

        if all_sections_passed:
            # Persist full-step validation summary as well (for coach panel)
            StepService._write_full_step_validation_from_sections(
                db, step_id, step.step_code
            )
            # Mark step as validated
            step.status = "validated"
            step.completed_at = datetime.now(timezone.utc)

            # Update complaint status
            report = db.query(Report).filter(Report.id == step.report_id).first()
            if report:
                complaint = db.query(Complaint).filter(
                    Complaint.id == report.complaint_id
                ).first()
                if complaint:
                    complaint.status = step.step_code

            db.commit()

        return {
            "validation": validation_result,
            "all_sections_passed": all_sections_passed,
            "passed_sections": passed,
            "remaining_sections": remaining,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # FULL STEP SUBMIT  (D1 + legacy fallback)
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def submit_step(db: Session, step_id: int) -> Dict[str, Any]:
        """
        Full-step validation (used by D1 and as a fallback).
        For D2-D8 the preferred path is submit_section().
        """
        step = db.query(ReportStep).filter(ReportStep.id == step_id).first()
        if not step:
            raise HTTPException(status_code=404, detail="Step not found")

        if step.status not in ('draft', 'rejected'):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot submit step with status '{step.status}'",
            )

        if not StepService._validate_required_fields(step.step_code, step.data):
            raise HTTPException(
                status_code=422,
                detail="Please fill in all required fields before continuing.",
            )

        step.status = 'submitted'
        step.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(step)

        try:
            chatbot = ChatbotService(db)
            validation_result = chatbot.validate_step(
                report_step_id=step_id,
                step_code=step.step_code,
                step_data=None,
            )

            if validation_result['decision'] == 'pass':
                step.status = 'validated'
                step.completed_at = datetime.now(timezone.utc)

                report = db.query(Report).filter(Report.id == step.report_id).first()
                if report:
                    complaint = db.query(Complaint).filter(
                        Complaint.id == report.complaint_id
                    ).first()
                    if complaint:
                        complaint.status = step.step_code
            else:
                step.status = 'rejected'

            db.commit()
            db.refresh(step)

            return {
                "step": step,
                "validation": validation_result,
                "message": (
                    "Step validated successfully"
                    if validation_result['decision'] == 'pass'
                    else "Step rejected — please review feedback"
                ),
            }

        except ValueError as e:
            db.rollback()
            step.status = 'draft'
            db.commit()
            raise HTTPException(status_code=500, detail=f"Validation failed: {str(e)}")
        except RuntimeError as e:
            db.rollback()
            step.status = 'draft'
            db.commit()
            raise HTTPException(status_code=503, detail=f"AI service unavailable: {str(e)}")
        except Exception as e:
            db.rollback()
            step.status = 'draft'
            db.commit()
            raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")

    # ─────────────────────────────────────────────────────────────────────────
    # VALIDATION READ
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def get_step_validation(
        db: Session, step_id: int
    ) -> Optional[StepValidation]:
        return db.query(StepValidation).filter(
            StepValidation.report_step_id == step_id
        ).first()

    @staticmethod
    def get_step_validation(db: Session, step_id: int) -> Optional[StepValidation]:
        """Get the full-step validation row (section_key IS NULL)."""
        return db.query(StepValidation).filter(
            StepValidation.report_step_id == step_id,
            StepValidation.section_key.is_(None),
        ).first()


    @staticmethod
    def get_all_section_validations(db: Session, step_id: int) -> List[StepValidation]:
        """Get all per-section rows (section_key IS NOT NULL)."""
        return db.query(StepValidation).filter(
            StepValidation.report_step_id == step_id,
            StepValidation.section_key.isnot(None),
        ).all()
    # ─────────────────────────────────────────────────────────────────────────
    # INTERNAL HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _upsert_section_validation(
        db: Session,
        step_id: int,
        section_key: str,   # e.g. "five_w_2h"
        data: Dict,
    ) -> None:
        """Upsert a per-section row in step_validation."""
        missing      = list(data.get("missing_fields", []))
        incomplete   = list(data.get("incomplete_fields", []))
        quality      = list(data.get("quality_issues", []))
        rules        = list(data.get("rules_violations", []))
        suggestions  = list(data.get("suggestions", []))
        improvements = data.get("field_improvements", {})
        assessment   = str(data.get("overall_assessment", ""))

        combined_issues = incomplete + quality + rules
        rewrite_json    = json.dumps(improvements, ensure_ascii=False)

        existing = db.query(StepValidation).filter(
            StepValidation.report_step_id == step_id,
            StepValidation.section_key    == section_key,
        ).first()

        if existing:
            existing.decision             = data["decision"]
            existing.missing              = missing
            existing.issues               = combined_issues
            existing.suggestions          = suggestions
            existing.professional_rewrite = rewrite_json
            existing.notes                = assessment
            existing.validated_at         = datetime.now(timezone.utc)
        else:
            db.add(StepValidation(
                report_step_id        = step_id,
                section_key           = section_key,   # ← non-NULL
                decision              = data["decision"],
                missing               = missing,
                issues                = combined_issues,
                suggestions           = suggestions,
                professional_rewrite  = rewrite_json,
                notes                 = assessment,
                validated_at          = datetime.now(timezone.utc),
            ))


    @staticmethod
    def _get_section_status(db: Session, step_id: int, all_keys: List[str]):
        """Returns (passed_keys, remaining_keys)."""
        rows = db.query(StepValidation).filter(
            StepValidation.report_step_id == step_id,
            StepValidation.section_key.isnot(None),   # exclude full-step rows
        ).all()
        passed_map = {r.section_key: r.decision for r in rows}
        passed    = [k for k in all_keys if passed_map.get(k) == "pass"]
        remaining = [k for k in all_keys if passed_map.get(k) != "pass"]
        return passed, remaining


    @staticmethod
    def _write_full_step_validation_from_sections(
        db: Session, step_id: int, step_code: str
    ) -> None:
        """Synthetic full-step summary row (section_key=NULL) once all sections pass."""
        rows = db.query(StepValidation).filter(
            StepValidation.report_step_id == step_id,
            StepValidation.section_key.isnot(None),
        ).all()

        combined_missing:     list = []
        combined_issues:      list = []
        combined_suggestions: list = []
        combined_improvements: dict = {}
        notes_parts: list = []

        for row in rows:
            combined_missing.extend(row.missing or [])
            combined_issues.extend(row.issues or [])
            combined_suggestions.extend(row.suggestions or [])
            notes_parts.append(f"[{row.section_key}] {row.notes or ''}")
            try:
                combined_improvements.update(json.loads(row.professional_rewrite or "{}"))
            except (json.JSONDecodeError, TypeError):
                pass

        rewrite_json = json.dumps(combined_improvements, ensure_ascii=False)
        overall      = " | ".join(notes_parts)

        # Full-step row has section_key = NULL
        existing = db.query(StepValidation).filter(
            StepValidation.report_step_id == step_id,
            StepValidation.section_key.is_(None),
        ).first()

        if existing:
            existing.decision             = "pass"
            existing.missing              = combined_missing
            existing.issues               = combined_issues
            existing.suggestions          = combined_suggestions
            existing.professional_rewrite = rewrite_json
            existing.notes                = overall
            existing.validated_at         = datetime.now(timezone.utc)
        else:
            db.add(StepValidation(
                report_step_id        = step_id,
                section_key           = None,            # ← NULL = full step summary
                decision              = "pass",
                missing               = combined_missing,
                issues                = combined_issues,
                suggestions           = combined_suggestions,
                professional_rewrite  = rewrite_json,
                notes                 = overall,
                validated_at          = datetime.now(timezone.utc),
            ))

    # ─────────────────────────────────────────────────────────────────────────
    # REQUIRED FIELDS (unchanged from original)
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _validate_required_fields(step_code: str, data: dict) -> bool:
        required_fields = {
            'D1': ['team_members'],
            'D2': ['five_w_2h'],
            'D3': ['defected_part_status'],
            'D4': ['root_cause_occurrence'],
            'D5': ['corrective_actions_occurrence'],
            'D6': ['monitoring', 'checklist'],
            'D7': ['ll_conclusion'],
            'D8': ['closure_statement'],
        }

        if not isinstance(data, dict) or not data:
            return False

        if step_code == "D1":
            members = data.get("team_members")
            if not isinstance(members, list) or len(members) < 2:
                return False
            required_member_fields = ["name", "function", "department"]
            if not all(all(m.get(k) for k in required_member_fields) for m in members):
                return False
            return True

        if step_code == "D2":
            five_w_2h = data.get("five_w_2h")
            if not isinstance(five_w_2h, dict):
                return False
            filled_count = sum(1 for v in five_w_2h.values() if v not in (None, "", [], {}))
            return filled_count >= 3

        if step_code == "D3":
            defected = data.get("defected_part_status")
            if not isinstance(defected, dict):
                return False
            has_checkbox = any(bool(defected.get(k)) for k in ["returned", "isolated", "identified"])
            if not has_checkbox:
                return False
            if defected.get("isolated") and not defected.get("isolated_location", "").strip():
                return False
            if defected.get("identified") and not defected.get("identified_method", "").strip():
                return False
            return True

        if step_code == "D4":
            occ  = data.get("root_cause_occurrence") or {}
            nond = data.get("root_cause_non_detection") or {}
            return (
                bool(occ.get("root_cause", "").strip()) and
                bool(occ.get("validation_method", "").strip()) and
                bool(nond.get("root_cause", "").strip()) and
                bool(nond.get("validation_method", "").strip())
            )

        if step_code == "D5":
            occ = data.get("corrective_actions_occurrence")
            det = data.get("corrective_actions_detection")
            if not isinstance(occ, list) or not isinstance(det, list):
                return False

            def row_ok(r):
                return (isinstance(r, dict) and
                        str(r.get("action", "")).strip() and
                        str(r.get("responsible", "")).strip() and
                        str(r.get("due_date", "")).strip())

            return any(row_ok(r) for r in occ) or any(row_ok(r) for r in det)

        if step_code == "D6":
            occ = data.get("corrective_actions_occurrence")
            det = data.get("corrective_actions_detection")

            def impl_ok(r):
                if not isinstance(r, dict):
                    return False
                if not str(r.get("action", "")).strip():
                    return False
                if not str(r.get("responsible", "")).strip():
                    return False
                if not str(r.get("due_date", "")).strip():
                    return False
                return (bool(str(r.get("imp_date", "")).strip()) or
                        bool(str(r.get("evidence", "")).strip()))

            has_impl = (isinstance(occ, list) and any(impl_ok(r) for r in occ)) or \
                       (isinstance(det, list) and any(impl_ok(r) for r in det))
            if not has_impl:
                return False

            monitoring = data.get("monitoring")
            if not isinstance(monitoring, dict):
                return False
            has_monitoring = bool(
                (monitoring.get("monitoring_interval") or "").strip() or
                monitoring.get("pieces_produced") or
                monitoring.get("rejection_rate") or
                (monitoring.get("audited_by") or "").strip() or
                (monitoring.get("audit_date") or "").strip()
            )
            if not has_monitoring:
                return False

            checklist = data.get("checklist")
            if not isinstance(checklist, list) or len(checklist) == 0:
                return False
            verified = sum(
                1 for item in checklist
                if isinstance(item, dict) and
                (item.get("shift_1") or item.get("shift_2") or item.get("shift_3"))
            )
            return (verified / len(checklist)) >= 0.5

        if step_code == "D7":
            ll = data.get("ll_conclusion", "").strip()

            def list_has_content(lst):
                return isinstance(lst, list) and any(
                    any(str(v).strip() for v in item.values() if v is not None)
                    for item in lst if isinstance(item, dict)
                )

            return bool(ll) or any([
                list_has_content(data.get("recurrence_risks", [])),
                list_has_content(data.get("lesson_disseminations", [])),
                list_has_content(data.get("replication_validations", [])),
                list_has_content(data.get("knowledge_base_updates", [])),
                list_has_content(data.get("long_term_monitoring", [])),
            ])

        if step_code == "D8":
            if not data.get("closure_statement", "").strip():
                return False
            signatures = data.get("signatures")
            if signatures and not signatures.get("closed_by", "").strip():
                return False
            return True

        step_required = required_fields.get(step_code, [])
        if not step_required:
            return True

        def is_filled(value) -> bool:
            if value is None:
                return False
            if isinstance(value, str):
                return value.strip() != ""
            if isinstance(value, (list, dict)):
                return len(value) > 0
            return bool(value)

        return all(is_filled(data.get(field)) for field in step_required)

    # ─────────────────────────────────────────────────────────────────────────
    # REJECT / APPROVE  (unchanged)
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def reject_step(db: Session, step_id: int, reason: str) -> ReportStep:
        step = db.query(ReportStep).filter(ReportStep.id == step_id).first()
        if not step:
            raise HTTPException(status_code=404, detail="Step not found")

        step.status = 'rejected'
        step.updated_at = datetime.now(timezone.utc)

        validation = StepValidation(
            report_step_id=step_id,
            decision='fail',
            issues=[reason],
            validated_at=datetime.now(timezone.utc),
        )
        db.add(validation)
        db.commit()
        db.refresh(step)
        return step

    @staticmethod
    def approve_step(db: Session, step_id: int, validation_data: dict) -> ReportStep:
        step = db.query(ReportStep).filter(ReportStep.id == step_id).first()
        if not step:
            raise HTTPException(status_code=404, detail="Step not found")

        step.status = 'validated'
        validation = StepValidation(
            report_step_id=step_id,
            decision='pass',
            missing=validation_data.get('missing'),
            issues=validation_data.get('issues'),
            suggestions=validation_data.get('suggestions'),
            professional_rewrite=validation_data.get('professional_rewrite'),
            notes=validation_data.get('notes'),
        )
        db.add(validation)
        db.commit()
        db.refresh(step)
        return step