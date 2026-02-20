# app/services/chatbot_service.py
"""
Chatbot Service - AI-Powered Step Validation
Supports both full-step and per-section validation.

Per-section: step_code is passed as "D2_five_w_2h", "D3_restart", etc.
The KB lookup uses the full scoped code as the section_hint key.
"""

import json
import re
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from sqlalchemy.orm import Session
from sqlalchemy import text
from openai import OpenAI, OpenAIError

from app.core.config import settings
from app.models.step_validation import StepValidation

logger = logging.getLogger(__name__)


# ============================================================
# D1 LOCAL VALIDATOR  (no KB, no OpenAI)
# ============================================================

VALID_ROLES = {
    "production", "maintenance", "engineering",
    "logistics", "team_leader", "other"
}

REQUIRED_ROLES = {"team_leader"}
REQUIRED_MEMBER_FIELDS = ["name", "function", "department", "role"]


class D1LocalValidator:
    def validate(self, step_data: Dict) -> Dict:
        missing_fields: List[str] = []
        incomplete_fields: List[str] = []
        quality_issues: List[str] = []
        suggestions: List[str] = []
        field_improvements: Dict[str, str] = {}

        members = step_data.get("team_members")

        if not isinstance(members, list):
            missing_fields.append("team_members")
            return self._build_result(
                decision="fail",
                missing_fields=missing_fields,
                overall_assessment="team_members field is missing or not a list.",
            )

        if len(members) < 2:
            incomplete_fields.append(
                "team_members: at least 2 members are required for a valid 8D team"
            )
            suggestions.append(
                "Add more cross-functional members (e.g. Quality, Production, Engineering)."
            )

        seen_names: List[str] = []
        leader_count = 0

        for idx, member in enumerate(members):
            label = f"Member #{idx + 1}"
            if not isinstance(member, dict):
                incomplete_fields.append(f"{label}: must be a dict/object")
                continue

            for field in REQUIRED_MEMBER_FIELDS:
                value = member.get(field, "")
                if not isinstance(value, str) or not value.strip():
                    incomplete_fields.append(f"{label}: '{field}' is empty or missing")

            role = member.get("role", "")
            if role and role not in VALID_ROLES:
                quality_issues.append(
                    f"{label}: role '{role}' is invalid. "
                    f"Must be one of: {', '.join(sorted(VALID_ROLES))}"
                )
                field_improvements[f"member_{idx+1}_role"] = (
                    f"Use one of: {', '.join(sorted(VALID_ROLES))}"
                )

            if role == "team_leader":
                leader_count += 1

            name = member.get("name", "").strip().lower()
            if name:
                if name in seen_names:
                    quality_issues.append(
                        f"{label}: duplicate name '{member.get('name')}' detected"
                    )
                else:
                    seen_names.append(name)

        if leader_count == 0 and len(members) >= 2:
            incomplete_fields.append(
                "team_members: no team_leader assigned — "
                "one member must have role = 'team_leader'"
            )
            suggestions.append(
                "Assign the role 'team_leader' to the person responsible "
                "for driving the 8D process."
            )
        elif leader_count > 1:
            quality_issues.append(
                f"team_members: {leader_count} members have role 'team_leader' — "
                "only one team leader is allowed"
            )

        has_issues = bool(missing_fields or incomplete_fields or quality_issues)
        decision = "fail" if has_issues else "pass"

        if decision == "pass":
            overall = (
                f"D1 validated ✅ — {len(members)} team member(s) correctly defined "
                f"with all required fields and a designated team leader."
            )
        else:
            total = len(incomplete_fields) + len(quality_issues) + len(missing_fields)
            overall = (
                f"D1 needs {total} correction(s) before it can be approved. "
                "Please fix the issues listed above."
            )

        return self._build_result(
            decision=decision,
            missing_fields=missing_fields,
            incomplete_fields=incomplete_fields,
            quality_issues=quality_issues,
            suggestions=suggestions,
            field_improvements=field_improvements,
            overall_assessment=overall,
        )

    @staticmethod
    def _build_result(
        decision: str,
        missing_fields: List[str] = None,
        incomplete_fields: List[str] = None,
        quality_issues: List[str] = None,
        rules_violations: List[str] = None,
        suggestions: List[str] = None,
        field_improvements: Dict[str, str] = None,
        overall_assessment: str = "",
        language_detected: str = "en",
    ) -> Dict:
        return {
            "decision": decision,
            "missing_fields": missing_fields or [],
            "incomplete_fields": incomplete_fields or [],
            "quality_issues": quality_issues or [],
            "rules_violations": rules_violations or [],
            "suggestions": suggestions or [],
            "field_improvements": field_improvements or {},
            "overall_assessment": overall_assessment,
            "language_detected": language_detected,
        }


# ============================================================
# KNOWLEDGE BASE RETRIEVER
# ============================================================

class KnowledgeBaseRetriever:
    def __init__(self, db: Session):
        self.db = db

    def get_step_coaching_content(self, step_code: str) -> str:
        section_hint = f"{step_code}_coaching_validation"
        query = text("""
            SELECT k.content
            FROM kb_chunks k
            JOIN files f ON k.file_id = f.id
            WHERE k.section_hint = :section_hint
            AND f.purpose = 'ikb'
            LIMIT 1
        """)
        result = self.db.execute(query, {"section_hint": section_hint}).fetchone()
        if not result or not result[0]:
            parent_code = step_code.split("_")[0]
            if parent_code != step_code:
                fallback_hint = f"{parent_code}_coaching_validation"
                fallback = self.db.execute(
                    query, {"section_hint": fallback_hint}
                ).fetchone()
                if fallback and fallback[0]:
                    logger.warning(
                        "⚠️ No section-specific coaching for '%s', falling back to '%s'",
                        section_hint, fallback_hint,
                    )
                    return fallback[0]
            raise ValueError(f"No coaching content found for {step_code} (hint: {section_hint})")
        logger.info("📚 Coaching loaded for %s (%d chars)", step_code, len(result[0]))
        return result[0]

    def get_twenty_rules(self) -> str:
        query = text("""
            SELECT k.content
            FROM kb_chunks k
            JOIN files f ON k.file_id = f.id
            WHERE k.section_hint = 'floor_rules_guidelines'
            AND f.purpose = 'ikb'
            LIMIT 1
        """)
        result = self.db.execute(query).fetchone()
        if result and result[0]:
            logger.info("📜 20 Rules loaded (%d chars)", len(result[0]))
            return result[0]
        logger.warning("⚠️ 20 Rules not found in KB")
        return ""

    def get_complaint_context(self, report_step_id: int) -> Dict:
        query = text("""
            SELECT
                c.complaint_name,
                c.complaint_description,
                c.product_line,
                c.avocarbon_plant,
                c.defects
            FROM complaints c
            JOIN reports r ON c.id = r.complaint_id
            JOIN report_steps rs ON r.id = rs.report_id
            WHERE rs.id = :report_step_id
        """)
        result = self.db.execute(query, {"report_step_id": report_step_id}).fetchone()
        if result:
            context = {
                "complaint_name": result[0] or "",
                "complaint_description": result[1] or "",
                "product_line": result[2] or "",
                "plant": result[3] or "",
                "defects": result[4] or "",
            }
            logger.info("📋 Complaint context loaded: %s", context["complaint_name"])
            return context
        logger.warning("⚠️ No complaint found for report_step_id %d", report_step_id)
        return {}


# ============================================================
# STEP DATA FORMATTER  (unchanged from previous version)
# ============================================================

def _val(v: Any, fallback: str = "—") -> str:
    if v is None or v == "" or v == []:
        return fallback
    return str(v).strip() or fallback


def _bool_str(v: Any) -> str:
    if v is True:
        return "Yes"
    if v is False:
        return "No"
    return _val(v)


def _row_table(headers: List[str], rows: List[Dict], key_map: Dict[str, str]) -> str:
    if not rows:
        return "  (no rows)\n"
    lines = []
    col_width = 22
    header_line = " | ".join(h.ljust(col_width) for h in headers)
    lines.append("  " + header_line)
    lines.append("  " + "-" * len(header_line))
    for row in rows:
        if not isinstance(row, dict):
            continue
        cells = []
        for h, k in zip(headers, key_map.values()):
            raw = row.get(k, "")
            cells.append(_val(raw).ljust(col_width))
        lines.append("  " + " | ".join(cells))
    return "\n".join(lines) + "\n"


class StepDataFormatter:
    @staticmethod
    def format_section(step_code: str, step_data: Dict) -> str:
        formatters = {
            "D2_five_w_2h":             StepDataFormatter._fmt_d2_five_w_2h,
            "D2_deviation":             StepDataFormatter._fmt_d2_deviation,
            "D2_is_is_not":             StepDataFormatter._fmt_d2_is_is_not,
            "D3_defected_parts":        StepDataFormatter._fmt_d3_defected_parts,
            "D3_suspected_parts":       StepDataFormatter._fmt_d3_suspected_parts,
            "D3_restart":               StepDataFormatter._fmt_d3_restart,
            "D4_four_m_occurrence":     StepDataFormatter._fmt_d4_four_m_occurrence,
            "D4_four_m_non_detection":  StepDataFormatter._fmt_d4_four_m_non_detection,
            "D5_corrective_occurrence": StepDataFormatter._fmt_d5_corrective_occurrence,
            "D5_corrective_detection":  StepDataFormatter._fmt_d5_corrective_detection,
            "D6_implementation":        StepDataFormatter._fmt_d6_implementation,
            "D6_monitoring_checklist":  StepDataFormatter._fmt_d6_monitoring_checklist,
            "D7_prevention":            StepDataFormatter._fmt_d7_prevention,
            "D7_knowledge":             StepDataFormatter._fmt_d7_knowledge,
            "D7_lessons_learned":       StepDataFormatter._fmt_d7_lessons_learned,
            "D8_closure":               StepDataFormatter._fmt_d8_closure,
        }
        fn = formatters.get(step_code)
        if fn:
            try:
                return fn(step_data)
            except Exception as exc:
                logger.warning("Formatter %s raised %s — falling back to generic", step_code, exc)
        return StepDataFormatter._fmt_generic(step_data)

    @staticmethod
    def _fmt_generic(data: Dict) -> str:
        lines = []
        for key, value in data.items():
            label = key.replace("_", " ").title()
            if isinstance(value, dict):
                lines.append(f"{label}:")
                for k, v in value.items():
                    lines.append(f"  {k.replace('_', ' ').title()}: {_val(v)}")
            elif isinstance(value, list):
                lines.append(f"{label}: ({len(value)} items)")
                for i, item in enumerate(value, 1):
                    if isinstance(item, dict):
                        parts = ", ".join(f"{k}: {_val(v)}" for k, v in item.items())
                        lines.append(f"  [{i}] {parts}")
                    else:
                        lines.append(f"  [{i}] {_val(item)}")
            else:
                lines.append(f"{label}: {_val(value)}")
        return "\n".join(lines)

    @staticmethod
    def _fmt_d2_five_w_2h(data: Dict) -> str:
        lines = ["=== PROBLEM DESCRIPTION ==="]
        lines.append(_val(data.get("problem_description")))
        lines.append("")
        lines.append("=== 5W2H ANALYSIS ===")
        five_w = data.get("five_w_2h") or {}
        mapping = {
            "Who":           "who",
            "What":          "what",
            "When":          "when",
            "Where":         "where",
            "Why":           "why",
            "How":           "how",
            "How Much/Many": "how_much",
        }
        for label, key in mapping.items():
            lines.append(f"  {label}: {_val(five_w.get(key))}")
        return "\n".join(lines)

    @staticmethod
    def _fmt_d2_deviation(data: Dict) -> str:
        lines = ["=== DEVIATION ANALYSIS ==="]
        lines.append(f"  Applicable Standard : {_val(data.get('standard_applicable'))}")
        lines.append(f"  Expected Situation  : {_val(data.get('expected_situation'))}")
        lines.append(f"  Observed Situation  : {_val(data.get('observed_situation'))}")
        ev = data.get("evidence_documents") or []
        if isinstance(ev, list):
            lines.append(f"  Evidence Documents  : {', '.join(str(e) for e in ev) or '—'}")
        else:
            lines.append(f"  Evidence Documents  : {_val(ev)}")
        return "\n".join(lines)

    @staticmethod
    def _fmt_d2_is_is_not(data: Dict) -> str:
        lines = ["=== IS / IS NOT ANALYSIS ==="]
        factors = data.get("is_is_not_factors") or []
        if not factors:
            lines.append("  (no factors provided)")
            return "\n".join(lines)
        headers = ["Factor", "IS", "IS NOT", "Distinction"]
        key_map = {"Factor": "factor", "IS": "is_value", "IS NOT": "is_not_value", "Distinction": "distinction"}
        lines.append(_row_table(headers, factors, key_map))
        return "\n".join(lines)

    @staticmethod
    def _fmt_d3_defected_parts(data: Dict) -> str:
        lines = ["=== DEFECTED PARTS STATUS ==="]
        dp = data.get("defected_part_status") or {}
        lines.append(f"  Returned               : {_bool_str(dp.get('returned'))}")
        lines.append(f"  Isolated               : {_bool_str(dp.get('isolated'))}")
        if dp.get("isolated"):
            lines.append(f"    Isolation Location   : {_val(dp.get('isolated_location'))}")
        lines.append(f"  Identified             : {_bool_str(dp.get('identified'))}")
        if dp.get("identified"):
            lines.append(f"    Identification Method: {_val(dp.get('identified_method'))}")
        lines.append(f"  Quantity Affected      : {_val(dp.get('quantity'))}")
        lines.append(f"  Disposition            : {_val(dp.get('disposition'))}")
        lines.append(f"  Notes                  : {_val(dp.get('notes'))}")
        return "\n".join(lines)

    @staticmethod
    def _fmt_d3_suspected_parts(data: Dict) -> str:
        lines = ["=== SUSPECTED PARTS STATUS ==="]
        sp = data.get("suspected_parts_status") or {}
        lines.append(f"  Status                : {_val(sp.get('status'))}")
        lines.append(f"  Quantity              : {_val(sp.get('quantity'))}")
        lines.append(f"  Location              : {_val(sp.get('location'))}")
        lines.append(f"  Alert Communicated To : {_val(data.get('alert_communicated_to'))}")
        lines.append(f"  Alert Number          : {_val(data.get('alert_number'))}")
        return "\n".join(lines)

    @staticmethod
    def _fmt_d3_restart(data: Dict) -> str:
        lines = ["=== PRODUCTION RESTART & CONTAINMENT ==="]
        rp = data.get("restart_production") or {}
        lines.append(f"  Restart Authorised    : {_bool_str(rp.get('authorised'))}")
        lines.append(f"  Restart Date          : {_val(rp.get('date'))}")
        lines.append(f"  Restart Conditions    : {_val(rp.get('conditions'))}")
        lines.append(f"  Containment Responsible: {_val(data.get('containment_responsible'))}")
        return "\n".join(lines)

    @staticmethod
    def _fmt_d4_four_m(label: str, four_m_key: str, whys_key: str, rc_key: str, data: Dict) -> str:
        lines = [f"=== {label} ==="]
        four_m = data.get(four_m_key) or {}
        categories = ["Man", "Machine", "Method", "Material", "Measurement", "Environment"]
        lines.append("  4M / Ishikawa Factors:")
        for cat in categories:
            val = four_m.get(cat.lower()) or four_m.get(cat, "")
            if val:
                lines.append(f"    {cat}: {_val(val)}")
        lines.append("")
        lines.append("  5 Whys:")
        whys = data.get(whys_key) or []
        if isinstance(whys, list):
            for i, why in enumerate(whys, 1):
                if isinstance(why, dict):
                    lines.append(f"    Why {i}: {_val(why.get('why') or why.get('question') or why.get('text'))}")
                    lines.append(f"     → Because: {_val(why.get('because') or why.get('answer'))}")
                else:
                    lines.append(f"    Why {i}: {_val(why)}")
        elif isinstance(whys, dict):
            for i in range(1, 6):
                w = whys.get(f"why_{i}") or whys.get(str(i), "")
                if w:
                    lines.append(f"    Why {i}: {_val(w)}")
        lines.append("")
        rc = data.get(rc_key) or {}
        lines.append("  Root Cause:")
        lines.append(f"    Statement         : {_val(rc.get('root_cause'))}")
        lines.append(f"    Validation Method : {_val(rc.get('validation_method'))}")
        lines.append(f"    Validated By      : {_val(rc.get('validated_by'))}")
        lines.append(f"    Validation Date   : {_val(rc.get('validation_date'))}")
        return "\n".join(lines)

    @staticmethod
    def _fmt_d4_four_m_occurrence(data: Dict) -> str:
        return StepDataFormatter._fmt_d4_four_m(
            "ROOT CAUSE — OCCURRENCE",
            "four_m_occurrence", "five_whys_occurrence", "root_cause_occurrence", data,
        )

    @staticmethod
    def _fmt_d4_four_m_non_detection(data: Dict) -> str:
        return StepDataFormatter._fmt_d4_four_m(
            "ROOT CAUSE — NON-DETECTION",
            "four_m_non_detection", "five_whys_non_detection", "root_cause_non_detection", data,
        )

    @staticmethod
    def _fmt_corrective_actions(title: str, actions: List) -> str:
        lines = [f"=== {title} ==="]
        if not actions:
            lines.append("  (no actions defined)")
            return "\n".join(lines)
        for i, a in enumerate(actions, 1):
            if not isinstance(a, dict):
                continue
            lines.append(f"  Action #{i}:")
            lines.append(f"    Description : {_val(a.get('action'))}")
            lines.append(f"    Responsible : {_val(a.get('responsible'))}")
            lines.append(f"    Due Date    : {_val(a.get('due_date'))}")
            lines.append(f"    Status      : {_val(a.get('status'))}")
            lines.append(f"    Category    : {_val(a.get('category'))}")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _fmt_d5_corrective_occurrence(data: Dict) -> str:
        return StepDataFormatter._fmt_corrective_actions(
            "CORRECTIVE ACTIONS — OCCURRENCE",
            data.get("corrective_actions_occurrence") or [],
        )

    @staticmethod
    def _fmt_d5_corrective_detection(data: Dict) -> str:
        return StepDataFormatter._fmt_corrective_actions(
            "CORRECTIVE ACTIONS — DETECTION",
            data.get("corrective_actions_detection") or [],
        )

    @staticmethod
    def _fmt_d6_action_table(title: str, actions: List) -> str:
        lines = [f"  {title}:"]
        if not actions:
            lines.append("    (no actions)")
            return "\n".join(lines)
        for i, a in enumerate(actions, 1):
            if not isinstance(a, dict):
                continue
            has_impl = bool(_val(a.get("imp_date"), "") or _val(a.get("evidence"), ""))
            lines.append(f"    #{i}: {_val(a.get('action'))}")
            lines.append(f"         Responsible  : {_val(a.get('responsible'))}")
            lines.append(f"         Due Date     : {_val(a.get('due_date'))}")
            lines.append(f"         Imp. Date    : {_val(a.get('imp_date'))}")
            lines.append(f"         Evidence     : {_val(a.get('evidence'))}")
            lines.append(f"         Implemented? : {'✅ Yes' if has_impl else '❌ Not yet'}")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _fmt_d6_implementation(data: Dict) -> str:
        lines = ["=== CORRECTIVE ACTION IMPLEMENTATION ==="]
        lines.append(StepDataFormatter._fmt_d6_action_table(
            "Occurrence Actions", data.get("corrective_actions_occurrence") or []
        ))
        lines.append(StepDataFormatter._fmt_d6_action_table(
            "Detection Actions", data.get("corrective_actions_detection") or []
        ))
        return "\n".join(lines)

    @staticmethod
    def _fmt_d6_monitoring_checklist(data: Dict) -> str:
        lines = ["=== MONITORING & EFFECTIVENESS ==="]
        m = data.get("monitoring") or {}
        lines.append(f"  Monitoring Interval   : {_val(m.get('monitoring_interval'))}")
        pieces = m.get("pieces_produced")
        lines.append(f"  Pieces Produced       : {pieces if pieces is not None else '—'}")
        rej = m.get("rejection_rate")
        lines.append(f"  Rejection Rate        : {f'{rej}%' if rej is not None else '—'}")
        lines.append(f"  Shift 1 Data          : {_val(m.get('shift_1_data'))}")
        lines.append(f"  Shift 2 Data          : {_val(m.get('shift_2_data'))}")
        lines.append("")
        lines.append("=== AUDIT INFO ===")
        lines.append(f"  Audited By            : {_val(data.get('audited_by'))}")
        lines.append(f"  Audit Date            : {_val(data.get('audit_date'))}")
        num_shifts = data.get("num_shifts", 3)
        lines.append(f"  Active Shifts         : {num_shifts}")
        lines.append("")
        lines.append("=== IMPLEMENTATION CHECKLIST ===")
        checklist = data.get("checklist") or []
        if not checklist:
            lines.append("  (no checklist items)")
        else:
            shift_keys = [f"shift_{i}" for i in range(1, num_shifts + 1)]
            checked = [item for item in checklist if isinstance(item, dict) and any(item.get(k) for k in shift_keys)]
            total = len(checklist)
            pct = round(len(checked) / total * 100) if total else 0
            lines.append(f"  Completion: {len(checked)}/{total} items ({pct}%)")
            lines.append("")
            lines.append("  Checked items:")
            for item in checked:
                if isinstance(item, dict):
                    shift_marks = ", ".join(
                        f"S{i}" for i in range(1, num_shifts + 1) if item.get(f"shift_{i}")
                    )
                    lines.append(f"    ✅ [{shift_marks}] {_val(item.get('question'))}")
            unchecked = [item for item in checklist if item not in checked]
            if unchecked:
                lines.append("")
                lines.append("  Unchecked items:")
                for item in unchecked:
                    if isinstance(item, dict):
                        lines.append(f"    ○ {_val(item.get('question'))}")
        return "\n".join(lines)

    @staticmethod
    def _fmt_d7_prevention(data: Dict) -> str:
        lines = ["=== RISK OF RECURRENCE ELSEWHERE ==="]
        risks = data.get("recurrence_risks") or []
        if not risks:
            lines.append("  (no risks defined)")
        else:
            for i, r in enumerate(risks, 1):
                if not isinstance(r, dict):
                    continue
                lines.append(f"  Risk #{i}:")
                lines.append(f"    Area / Line / Product : {_val(r.get('area_line_product'))}")
                lines.append(f"    Similar Risk Present  : {_val(r.get('similar_risk_present'))}")
                lines.append(f"    Action Taken          : {_val(r.get('action_taken'))}")
                lines.append("")
        lines.append("=== REPLICATION VALIDATION ===")
        reps = data.get("replication_validations") or []
        if not reps:
            lines.append("  (no replication records)")
        else:
            for i, r in enumerate(reps, 1):
                if not isinstance(r, dict):
                    continue
                lines.append(f"  Replication #{i}:")
                lines.append(f"    Line / Site           : {_val(r.get('line_site'))}")
                lines.append(f"    Action Replicated     : {_val(r.get('action_replicated'))}")
                lines.append(f"    Confirmation Method   : {_val(r.get('confirmation_method'))}")
                lines.append(f"    Confirmed By          : {_val(r.get('confirmed_by'))}")
                lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _fmt_d7_knowledge(data: Dict) -> str:
        lines = ["=== KNOWLEDGE BASE UPDATES ==="]
        kbs = data.get("knowledge_base_updates") or []
        if not kbs:
            lines.append("  (no documents)")
        else:
            for i, k in enumerate(kbs, 1):
                if not isinstance(k, dict):
                    continue
                lines.append(f"  Document #{i}:")
                lines.append(f"    Type              : {_val(k.get('document_type'))}")
                lines.append(f"    Topic / Reference : {_val(k.get('topic_reference'))}")
                lines.append(f"    Owner             : {_val(k.get('owner'))}")
                lines.append(f"    Location / Link   : {_val(k.get('location_link'))}")
                lines.append("")
        lines.append("=== LONG-TERM MONITORING ===")
        monitors = data.get("long_term_monitoring") or []
        if not monitors:
            lines.append("  (no monitoring checkpoints)")
        else:
            for i, m in enumerate(monitors, 1):
                if not isinstance(m, dict):
                    continue
                lines.append(f"  Checkpoint #{i}:")
                lines.append(f"    Type       : {_val(m.get('checkpoint_type'))}")
                lines.append(f"    Frequency  : {_val(m.get('frequency'))}")
                lines.append(f"    Owner      : {_val(m.get('owner'))}")
                lines.append(f"    Start Date : {_val(m.get('start_date'))}")
                lines.append(f"    Notes      : {_val(m.get('notes'))}")
                lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _fmt_d7_lessons_learned(data: Dict) -> str:
        lines = ["=== LESSON LEARNING DISSEMINATION ==="]
        disseminations = data.get("lesson_disseminations") or []
        if not disseminations:
            lines.append("  (no dissemination records)")
        else:
            for i, d in enumerate(disseminations, 1):
                if not isinstance(d, dict):
                    continue
                lines.append(f"  Dissemination #{i}:")
                lines.append(f"    Audience / Team : {_val(d.get('audience_team'))}")
                lines.append(f"    Method          : {_val(d.get('method'))}")
                lines.append(f"    Date            : {_val(d.get('date'))}")
                lines.append(f"    Owner           : {_val(d.get('owner'))}")
                lines.append(f"    Evidence        : {_val(d.get('evidence'))}")
                lines.append("")
        lines.append("=== LESSONS LEARNED CONCLUSION ===")
        conclusion = data.get("ll_conclusion", "").strip()
        lines.append(conclusion if conclusion else "  (no conclusion written)")
        return "\n".join(lines)

    @staticmethod
    def _fmt_d8_closure(data: Dict) -> str:
        lines = ["=== CLOSURE STATEMENT ==="]
        lines.append(_val(data.get("closure_statement")))
        lines.append("")
        lines.append("=== SIGNATURES ===")
        sigs = data.get("signatures") or {}
        if isinstance(sigs, dict):
            lines.append(f"  Closed By     : {_val(sigs.get('closed_by'))}")
            lines.append(f"  Closure Date  : {_val(sigs.get('closure_date'))}")
            lines.append(f"  Approved By   : {_val(sigs.get('approved_by'))}")
            lines.append(f"  Approval Date : {_val(sigs.get('approval_date'))}")
        return "\n".join(lines)


# ============================================================
# SECTION-SPECIFIC PASS CRITERIA
# Explicit, non-negotiable minimums given to the AI per section.
# These prevent the AI from setting its own arbitrary bar.
# ============================================================

SECTION_PASS_CRITERIA: Dict[str, str] = {
    # D2
    "D2_five_w_2h": """
MINIMUM PASS CRITERIA for this section:
- Problem description must be specific (not generic like "quality issue")
- At least 5 of the 7 5W2H fields must be filled
- "What" and "Where" are mandatory — fail immediately if either is blank or generic
- "How Much/Many" must include a number (quantity, %, ppm, etc.)
- Answers must be specific to this complaint context, not copy-paste boilerplate
""",
    "D2_deviation": """
MINIMUM PASS CRITERIA for this section:
- Standard/specification must be named explicitly (not just "our standard")
- Both expected and observed situation must be filled and clearly contrasted
- At least one evidence document or reference must be provided
""",
    "D2_is_is_not": """
MINIMUM PASS CRITERIA for this section:
- At least 3 factors must be filled (Who, What, Where are the most critical)
- Each factor must have a meaningful IS and IS NOT — not just a rephrasing
- The "Distinction" column must explain WHY the difference matters
- Empty or near-identical IS/IS NOT pairs should be flagged as incomplete
""",

    # D3
    "D3_defected_parts": """
MINIMUM PASS CRITERIA for this section:
- At least one disposition action (returned, isolated, or identified) must be checked
- If "isolated" is checked, isolation location must be specified
- If "identified" is checked, identification method must be specified
- Quantity affected must be provided — do not pass if blank
""",
    "D3_suspected_parts": """
MINIMUM PASS CRITERIA for this section:
- Suspected parts status must be defined (not blank)
- Alert number should be present if an alert was communicated
- If alert was communicated, recipient must be named
""",
    "D3_restart": """
MINIMUM PASS CRITERIA for this section:
- Restart authorisation status must be explicitly stated (yes/no)
- If restart is authorised, conditions for restart must be documented
- Containment responsible person must be named (not just a department)
""",

    # D4
    "D4_four_m_occurrence": """
MINIMUM PASS CRITERIA for this section:
- At least 2 of the 4M categories must have meaningful content (not single words)
- All 5 Whys must be completed — a chain that stops at Why 2 or 3 is insufficient
- Each "Because" must logically follow from the "Why" above it
- Root cause statement must be a specific mechanism, not a general observation
- Validation method must be named (test, audit, data analysis, etc.) — not blank
""",
    "D4_four_m_non_detection": """
MINIMUM PASS CRITERIA for this section:
- At least 2 of the 4M categories must be filled explaining WHY the defect was not caught
- 5 Whys must trace to a systemic detection failure, not just "operator missed it"
- Root cause must explain a gap in the detection/control system
- Validation method must be stated
""",

    # D5
    "D5_corrective_occurrence": """
MINIMUM PASS CRITERIA for this section:
- At least 1 corrective action must be defined with action, responsible person, and due date
- Actions must directly address the root cause identified in D4 — generic actions fail
- Each action must have a named responsible person (not just a department)
- Due dates must be realistic and specific (not "TBD" or "ASAP")
""",
    "D5_corrective_detection": """
MINIMUM PASS CRITERIA for this section:
- At least 1 detection improvement action must be defined
- Actions must directly address the detection gap from D4 non-detection root cause
- Named responsible person and due date are mandatory per action
""",

    # D6
    "D6_implementation": """
MINIMUM PASS CRITERIA for this section:
- Every action carried from D5 must have either an implementation date OR documented evidence
- Actions without both imp_date AND evidence are acceptable only if they have one of the two
- All actions still showing "not yet implemented" with no evidence will cause a FAIL
- Evidence references must be specific (document name, photo reference, test report number)
""",
    "D6_monitoring_checklist": """
MINIMUM PASS CRITERIA for this section:
- Monitoring interval must be defined (number of shifts, days, or pieces — not just "ongoing")
- At least one quantitative metric must be present: pieces produced OR rejection rate
- Checklist completion must be ≥ 50% across active shifts — if below 50%, always FAIL
- Audited by and audit date are expected — flag if missing but do not fail on them alone
- Shift data fields add value but are not blocking for a pass
""",

    # D7
    "D7_prevention": """
MINIMUM PASS CRITERIA for this section:
- At least 1 recurrence risk area must be identified with a named area/line/product
- Each risk with "Yes" similar risk must have a documented action taken
- At least 1 replication validation record must exist with line/site and action
- "Confirmed by" must name a person, not just a role or department
""",
    "D7_knowledge": """
MINIMUM PASS CRITERIA for this section:
- At least 1 knowledge base document must be referenced with type and topic
- At least 1 long-term monitoring checkpoint must be defined with frequency and owner
- Start dates for monitoring should be specified — flag if missing, but not blocking alone
""",
    "D7_lessons_learned": """
MINIMUM PASS CRITERIA for this section:
- At least 1 dissemination record must name a real audience and a communication method
- The LL conclusion must be substantive — minimum 2 sentences summarising what was learned
  and what systemic change was made; a one-line conclusion is not acceptable
- Vague conclusions like "we will be more careful" always FAIL
""",

    # D8
    "D8_closure": """
MINIMUM PASS CRITERIA for this section:
- Closure statement must explain WHY the problem is considered resolved (not just "closed")
- It must reference outcomes from previous steps (containment lifted, root cause fixed, etc.)
- At least "closed by" name must be present in signatures
- A closure statement that is one sentence or fewer should be flagged as insufficient
""",
}


# ============================================================
# PROMPT BUILDER
# ============================================================

# System prompt: rich persona and calibration instructions live here,
# freeing the user message to focus purely on the data and task.
SYSTEM_PROMPT = """You are a senior 8D Quality Coach with 20+ years of experience in automotive,
manufacturing, and process industries. You have validated thousands of 8D reports and
know exactly what separates a superficial response from a rigorous one.

Your role is to validate a SINGLE SECTION of an 8D report submitted by a quality engineer.
You are demanding but fair. You do not fail people for missing optional details.
You DO fail people for vague language, missing mandatory data, or answers that are
copy-paste boilerplate with no connection to the actual complaint.

CALIBRATION — your scoring must be consistent:
- A PASS means: a competent quality auditor reviewing this section would accept it as complete
  and specific enough to act on. Not perfect — acceptable.
- A FAIL means: the section has at least one BLOCKING issue that would prevent a real
  quality auditor from accepting it (missing mandatory field, too vague to be actionable,
  logically inconsistent with the complaint).

NEVER fail a section for stylistic preferences or optional fields.
NEVER pass a section that has a mandatory field empty or filled with generic boilerplate.

You must return ONLY valid JSON. No preamble, no explanation outside the JSON."""


class PromptBuilder:
    @staticmethod
    def format_step_data(step_code: str, step_data: Dict) -> str:
        return StepDataFormatter.format_section(step_code, step_data)

    @staticmethod
    def format_complaint_context(complaint: Dict) -> str:
        if not complaint:
            return "No complaint context available."
        return "\n".join([
            "COMPLAINT CONTEXT (use this to assess relevance and specificity of answers):",
            "=" * 60,
            f"  Problem Name  : {complaint.get('complaint_name', 'N/A')}",
            f"  Description   : {complaint.get('complaint_description', 'N/A')}",
            f"  Product Line  : {complaint.get('product_line', 'N/A')}",
            f"  Plant         : {complaint.get('plant', 'N/A')}",
            f"  Defects       : {complaint.get('defects', 'N/A')}",
            "",
            "⚠️  Answers that do not reference this specific complaint context",
            "    (e.g. copy-paste boilerplate) must be flagged as quality issues.",
        ])

    @staticmethod
    def _get_relevant_rules(step_code: str, twenty_rules: str) -> str:
        """
        Only include the 20 Rules block for sections where floor compliance
        is actually relevant. Avoids diluting the prompt for documentation sections.
        """
        # Rules are most relevant for D3 (containment), D4 (process analysis),
        # D6 (implementation on the floor) — less so for D2 description or D7/D8 closure
        floor_relevant = {
            "D3_defected_parts", "D3_suspected_parts", "D3_restart",
            "D4_four_m_occurrence", "D4_four_m_non_detection",
            "D6_implementation", "D6_monitoring_checklist",
        }
        if step_code not in floor_relevant or not twenty_rules:
            return ""
        return f"""
## FLOOR COMPLIANCE RULES (apply only when you see an obvious violation)
{twenty_rules}

Note: Only flag a rules violation if it is CLEARLY and DIRECTLY violated by
the content above. Do not speculate or invent violations.
{"-" * 60}
"""

    @staticmethod
    def build_enriched_validation_prompt(
        step_code: str,
        coaching: str,
        twenty_rules: str,
        complaint: Dict,
        step_data: Dict,
    ) -> str:
        formatted_complaint = PromptBuilder.format_complaint_context(complaint)
        formatted_data      = PromptBuilder.format_step_data(step_code, step_data)
        rules_section       = PromptBuilder._get_relevant_rules(step_code, twenty_rules)
        pass_criteria       = SECTION_PASS_CRITERIA.get(step_code, "")

        # Human-readable label: "D6_monitoring_checklist" → "D6 / Monitoring Checklist"
        parts = step_code.split("_", 1)
        display_code = f"{parts[0]} / {parts[1].replace('_', ' ').title()}" if len(parts) == 2 else step_code

        return f"""
## SECTION BEING VALIDATED: {display_code}

{"-" * 60}
## COACHING REFERENCE FOR THIS SECTION
(This is what a correct and complete response should look like)

{coaching}
{"-" * 60}

{formatted_complaint}
{"-" * 60}

{rules_section}

## DATA SUBMITTED BY THE USER
{formatted_data}

{"-" * 60}
{pass_criteria}
{"-" * 60}

## EVALUATION INSTRUCTIONS

Evaluate ONLY the data shown above. Do not penalise for fields belonging to
other sections — those are validated separately.

Work through these checks in order:

1. BLOCKING ISSUES (any one of these → immediate FAIL):
   - A mandatory field listed in the pass criteria is empty or contains only "—"
   - An answer is generic boilerplate with no connection to the complaint context
   - A logical chain (e.g. 5 Whys) is broken or circular
   - A quantitative field that must have a number is blank

2. QUALITY ISSUES (flag but not necessarily blocking):
   - Answers exist but lack specificity or measurable detail
   - Named persons are missing where expected (department name used instead)
   - Dates are vague ("soon", "TBD")

3. POSITIVE NOTES:
   - Where the response genuinely meets or exceeds the standard, say so briefly
     in overall_assessment — this helps the user understand what good looks like

## OUTPUT FORMAT — return this JSON and nothing else:

{{
    "decision": "pass" or "fail",
    "missing_fields": [
        "field_name — brief reason why it is missing or empty"
    ],
    "incomplete_fields": [
        "field_name — what specific detail is lacking and why it matters"
    ],
    "quality_issues": [
        "Specific issue with specific field — e.g. 'Root cause states \\"operator error\\" which is not a systemic cause'"
    ],
    "rules_violations": [
        "Rule N violated: explain exactly what in the response triggers this"
    ],
    "suggestions": [
        "Actionable suggestion — one concrete thing the user should change or add"
    ],
    "field_improvements": {{
        "field_name": "A rewritten version of this field at the quality level required — 1-3 sentences max, grounded in the complaint context"
    }},
    "overall_assessment": "2-4 sentence summary: what is good, what is blocking, and what the user must fix to pass. Be specific, not generic.",
    "language_detected": "en"
}}

STRICT OUTPUT RULES:
- field_improvements values must be 1–3 sentences, grounded in the actual complaint
- overall_assessment must name the specific section and specific issues — never generic
- If decision is "pass", missing_fields and incomplete_fields must be empty lists
- Respond in the same language the user wrote in (check the submitted data language)
"""


# ============================================================
# OPENAI CLIENT
# ============================================================

class OpenAIClient:
    def __init__(self):
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)

    def validate_step(self, prompt: str) -> str:
        try:
            logger.info("🤖 Calling OpenAI %s...", settings.OPENAI_MODEL)
            response = self.client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT,
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=settings.OPENAI_TEMPERATURE,
                max_tokens=settings.OPENAI_MAX_TOKENS,
                response_format={"type": "json_object"},
                timeout=30,
            )
            content = response.choices[0].message.content
            logger.info("✅ OpenAI response received (%d chars)", len(content))
            return content
        except OpenAIError as e:
            logger.error("❌ OpenAI API error: %s", str(e))
            raise RuntimeError(f"OpenAI validation failed: {str(e)}")


# ============================================================
# RESPONSE PARSER
# ============================================================

class ResponseParser:
    @staticmethod
    def parse(ai_text: str) -> Dict:
        try:
            data = json.loads(ai_text)
        except json.JSONDecodeError:
            logger.warning("⚠️ JSON parsing failed, attempting recovery...")
            match = re.search(r"\{.*\}", ai_text, re.DOTALL)
            if not match:
                raise ValueError("Invalid JSON returned by AI")
            data = json.loads(match.group())

        decision = data.get("decision")
        if decision not in {"pass", "fail"}:
            raise ValueError("Invalid or missing decision field")

        return {
            "decision": decision,
            "missing_fields": data.get("missing_fields", []),
            "incomplete_fields": data.get("incomplete_fields", []),
            "quality_issues": data.get("quality_issues", []),
            "rules_violations": data.get("rules_violations", []),
            "suggestions": data.get("suggestions", []),
            "field_improvements": data.get("field_improvements", {}),
            "overall_assessment": data.get("overall_assessment", ""),
            "language_detected": data.get("language_detected", "en"),
        }


# ============================================================
# VALIDATION STORAGE
# ============================================================

class ValidationStorage:
    def __init__(self, db: Session):
        self.db = db

    def store_validation(self, report_step_id: int, data: Dict) -> None:
        missing_fields     = list(data.get("missing_fields", []))
        incomplete_fields  = list(data.get("incomplete_fields", []))
        quality_issues     = list(data.get("quality_issues", []))
        rules_violations   = list(data.get("rules_violations", []))
        suggestions        = list(data.get("suggestions", []))
        field_improvements = data.get("field_improvements", {})
        overall_assessment = str(data.get("overall_assessment", ""))

        combined_issues: list = incomplete_fields + quality_issues + rules_violations
        rewrite_json: str = json.dumps(field_improvements, ensure_ascii=False)

        existing: Optional[StepValidation] = (
            self.db.query(StepValidation)
            .filter(StepValidation.report_step_id == report_step_id)
            .first()
        )
        if existing:
            existing.decision             = data["decision"]
            existing.missing              = missing_fields
            existing.issues               = combined_issues
            existing.suggestions          = suggestions
            existing.professional_rewrite = rewrite_json
            existing.notes                = overall_assessment
            existing.validated_at         = datetime.now(timezone.utc)
        else:
            self.db.add(StepValidation(
                report_step_id        = report_step_id,
                decision              = data["decision"],
                missing               = missing_fields,
                issues                = combined_issues,
                suggestions           = suggestions,
                professional_rewrite  = rewrite_json,
                notes                 = overall_assessment,
                validated_at          = datetime.now(timezone.utc),
            ))


# ============================================================
# STEPS THAT USE LOCAL VALIDATION
# ============================================================

LOCAL_VALIDATION_STEPS = {"D1"}


# ============================================================
# MAIN CHATBOT SERVICE
# ============================================================

class ChatbotService:
    def __init__(self, db: Session):
        self.db           = db
        self.kb           = KnowledgeBaseRetriever(db)
        self.prompt       = PromptBuilder()
        self.ai           = OpenAIClient()
        self.parser       = ResponseParser()
        self.storage      = ValidationStorage(db)
        self.d1_validator = D1LocalValidator()

    def validate_step(
        self,
        report_step_id: int,
        step_code: str,
        step_data: Optional[Dict] = None,
    ) -> Dict:
        logger.info("🚀 Starting validation for %s (ID: %d)", step_code, report_step_id)

        base_code = step_code.split("_")[0]
        is_section_call = step_code != base_code

        if not step_data and not is_section_call:
            logger.info("📖 Reading step_data from database...")
            from sqlalchemy import text as sa_text
            query = sa_text("SELECT data FROM report_steps WHERE id = :step_id")
            result = self.db.execute(query, {"step_id": report_step_id}).fetchone()
            if not result:
                raise ValueError(f"Report step {report_step_id} not found")
            if not result[0]:
                raise ValueError(
                    f"No data in report_steps.data for step {report_step_id}. "
                    "Please save the step first."
                )
            step_data = result[0]
            logger.info("✅ Step data loaded from DB (%d fields)", len(step_data))

        if not step_data:
            raise ValueError(f"No step_data provided for section validation of {step_code}")

        if base_code in LOCAL_VALIDATION_STEPS:
            validation = self._validate_locally(base_code, step_data)
        else:
            validation = self._validate_with_ai(step_code, report_step_id, step_data)

        if not is_section_call:
            try:
                self.storage.store_validation(report_step_id, validation)
                self.db.commit()
            except Exception:
                self.db.rollback()
                logger.exception("❌ DB transaction failed during store_validation")
                raise

        logger.info("✅ Validation completed: %s", validation["decision"])
        return validation

    def _validate_locally(self, step_code: str, step_data: Dict) -> Dict:
        logger.info("🔍 Running LOCAL validation for %s", step_code)
        if step_code == "D1":
            return self.d1_validator.validate(step_data)
        raise ValueError(f"No local validator implemented for {step_code}")

    def _validate_with_ai(
        self,
        step_code: str,
        report_step_id: int,
        step_data: Dict,
    ) -> Dict:
        logger.info("🤖 Running AI validation for %s", step_code)

        coaching     = self.kb.get_step_coaching_content(step_code)
        twenty_rules = self.kb.get_twenty_rules()
        complaint    = self.kb.get_complaint_context(report_step_id)

        prompt = self.prompt.build_enriched_validation_prompt(
            step_code=step_code,
            coaching=coaching,
            twenty_rules=twenty_rules,
            complaint=complaint,
            step_data=step_data,
        )

        ai_raw = self.ai.validate_step(prompt)
        return self.parser.parse(ai_raw)

    def health_check(self) -> Dict:
        try:
            from sqlalchemy import text as sa_text
            count = self.db.execute(sa_text("""
                SELECT COUNT(*) FROM kb_chunks
                WHERE section_hint LIKE '%_coaching_validation'
            """)).scalar()
            rules_exist = self.db.execute(sa_text("""
                SELECT EXISTS(
                    SELECT 1 FROM kb_chunks
                    WHERE section_hint = 'floor_rules_guidelines'
                )
            """)).scalar()
            return {
                "status": "healthy",
                "service": "chatbot",
                "kb_chunks_available": count,
                "twenty_rules_loaded": rules_exist,
                "message": "D1=local, D2-D8=per-section AI",
            }
        except Exception as e:
            logger.error("Health check failed: %s", str(e))
            return {
                "status": "unhealthy",
                "service": "chatbot",
                "kb_chunks_available": 0,
                "twenty_rules_loaded": False,
                "message": str(e),
            }