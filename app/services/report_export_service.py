# app/services/report_export_service.py

import io
from typing import Dict, Any, List
from sqlalchemy.orm import Session
from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from app.models.complaint import Complaint
from app.models.report import Report
from app.models.report_step import ReportStep

TEMPLATE_PATH = "app/templates/Problem_solving_template_2026.xlsx"


def _s(value, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, bool):
        return "✓" if value else ""
    return str(value).strip()


def _step(steps: Dict[str, ReportStep], code: str) -> Dict[str, Any]:
    step = steps.get(code)
    return (step.data or {}) if step else {}


def _w(ws: Worksheet, coord: str, value) -> None:
    ws[coord] = value


def _wb(ws: Worksheet, coord: str, flag: bool) -> None:
    ws[coord] = bool(flag)


# -----------------------------
# FIX 1: D1 team members + checkboxes
# Template reality (verified):
# - Member text areas are merged B6:D6 ... B12:D12 (top-left is B{row})
# - Role checkboxes are in J6..J12
# - Role titles are in K6..K12 (merged), but we do NOT write there.
# -----------------------------
def _normalize_text(x: str) -> str:
    return _s(x).lower()


def _member_role_bucket(member: Dict[str, Any]) -> str:
    """
    Decide which D1 role row the member belongs to based on function/department text.
    Returns one of:
      production, maintenance, engineering, logistics, leader, quality, other
    """
    fn = _normalize_text(member.get("function", ""))
    dept = _normalize_text(member.get("department", ""))
    blob = f"{fn} {dept}".strip()

    def has_any(*keywords: str) -> bool:
        return any(k in blob for k in keywords)

    if has_any("production", "operator", "line leader", "supervisor", "line ", "shopfloor", "shop floor"):
        return "production"
    if has_any("maintenance", "technician", "mechanic", "electrical", "mechanical"):
        return "maintenance"
    if has_any("engineering", "process", "manufacturing", "industrial", "methods", "method", "me", "pe"):
        return "engineering"
    if has_any("logistics", "supply", "warehouse", "shipping", "receiving", "supplier", "sqa", "sq", "procurement"):
        return "logistics"
    if has_any("team leader", "leader", "project", "pm", "manager", "management"):
        return "leader"
    if has_any("quality", "qc", "qa", "quality control", "quality assurance"):
        return "quality"
    return "other"


def _fill_d1(ws: Worksheet, data: Dict):
    """
    Fill D1 according to template:
      - Write member lines into the correct role row (B:D merged)
      - Tick J checkbox ONLY if that role has at least one member
    """
    ROLE_ROWS = {
        "production": 6,
        "maintenance": 7,
        "engineering": 8,
        "logistics": 9,
        "leader": 10,
        "quality": 11,
        "other": 12,
    }

    # Collect members into buckets
    buckets: Dict[str, List[str]] = {k: [] for k in ROLE_ROWS.keys()}
    for member in data.get("team_members", []) or []:
        line = f"{_s(member.get('name'))}  —  {_s(member.get('function'))} / {_s(member.get('department'))}".strip()
        if not line or line == "— /":
            continue
        bucket = _member_role_bucket(member)
        buckets[bucket].append(line)

    # Write + tick
    for role, row in ROLE_ROWS.items():
        lines = buckets.get(role) or []
        if lines:
            # B{row}:D{row} merged; write only in top-left
            _w(ws, f"B{row}", "\n".join(lines))
            _wb(ws, f"J{row}", True)
        else:
            # keep template empty; make sure checkbox is not ticked
            _w(ws, f"B{row}", "")
            _wb(ws, f"J{row}", False)


# -----------------------------
# FIX 2: 5 Whys should not overwrite "Question:" / "Answer:"
# Template reality:
# - "Question:" label is in C{base} merged vertically (e.g. C61:C63)
# - question text area is D{base}:M{base+2} merged (top-left D{base})
# - "Answer:" label is in C{base+3} merged vertically (e.g. C64:C66)
# - answer text area is D{base+3}:M{base+5} merged (top-left D{base+3})
# -----------------------------
def _fill_5whys(ws: Worksheet, whys: Dict, start: int):
    for i in range(5):
        why = (whys or {}).get(f"why_{i+1}", {}) or {}
        base = start + i * 6

        q = _s(why.get("question"))
        a = _s(why.get("answer"))

        # Keep labels in column C intact, write into the merged text blocks in column D
        _w(ws, f"D{base}", q)         # Question text block (D..M merged)
        _w(ws, f"D{base+3}", a)       # Answer text block   (D..M merged)




def _fill_d2(ws, data: Dict):
    five_w: Dict = data.get("five_w_2h", {}) or {}

    lines = [_s(data.get("problem_description", ""))]

    for label, key in [
        ("WHAT", "what"),
        ("WHERE", "where"),
        ("WHEN", "when"),
        ("WHO", "who"),
        ("WHY", "why"),
        ("HOW", "how"),
        ("HOW MANY", "how_many"),
    ]:
        val = _s(five_w.get(key, ""))
        if val:
            lines.append(f"{label}: {val}")

    for label, key in [
        ("Standard", "standard_applicable"),
        ("Expected", "expected_situation"),
        ("Observed", "observed_situation"),
    ]:
        val = _s(data.get(key, ""))
        if val:
            lines.append(f"{label}: {val}")

    _w(ws, "B14", "\n".join(filter(None, lines)))

    # Factors (Is / Is Not)
    FACTOR_ROWS = {"product": 21, "time": 22, "lot": 23, "pattern": 24}

    for factor in (data.get("is_is_not_factors", []) or []):
        factor_name = _s(factor.get("factor", "")).strip().lower()
        row = FACTOR_ROWS.get(factor_name)
        if not row:
            continue

        # Support both new and legacy field names
        is_problem = (
            factor.get("is_problem")
            if factor.get("is_problem") is not None
            else factor.get("is_value")
        )
        is_not_problem = (
            factor.get("is_not_problem")
            if factor.get("is_not_problem") is not None
            else factor.get("is_not_value")
        )

        _w(ws, f"C{row}", _s(is_problem, ""))
        _w(ws, f"E{row}", _s(is_not_problem, ""))


def _fill_d3(ws, data: Dict):
    defected: Dict = data.get("defected_part_status", {})
    _wb(ws, "C27", defected.get("returned", False))
    _wb(ws, "E27", defected.get("isolated", False))
    if defected.get("isolated"):
        _w(ws, "F27", f"Is it Isolated? Where? — {_s(defected.get('isolated_location'))}")
    _wb(ws, "H27", defected.get("identified", False))
    if defected.get("identified"):
        _w(ws, "I27", f"Identified to avoid mishandling: {_s(defected.get('identified_method'))}")

    LOC_ROWS = {"supplier_site":31,"in_transit":32,"production_floor":33,
                "warehouse":34,"customer_site":35,"others":36}
    for rd in data.get("suspected_parts_status", []):
        r = LOC_ROWS.get(rd.get("location",""))
        if r:
            _w(ws, f"C{r}", _s(rd.get("inventory")))
            _w(ws, f"E{r}", _s(rd.get("actions")))
            _w(ws, f"G{r}", _s(rd.get("leader")))
            _w(ws, f"K{r}", _s(rd.get("results")))

    alert: Dict = data.get("alert_communicated_to", {})
    _wb(ws, "C37", alert.get("production_shift_leaders", False))
    _wb(ws, "E37", alert.get("warehouse", False))
    _wb(ws, "H37", alert.get("customer_contact", False))
    _wb(ws, "C38", alert.get("quality_control", False))
    _wb(ws, "E38", alert.get("maintenance", False))
    _wb(ws, "H38", alert.get("production_planner", False))
    alert_num = _s(data.get("alert_number", ""))
    _w(ws, "B38", f"Alert # (QRQC log or NCR #): {alert_num}" if alert_num else "Alert # (QRQC log or NCR #):")

    restart: Dict = data.get("restart_production", {})
    _w(ws, "D40", _s(restart.get("when")))
    _w(ws, "G40", _s(restart.get("first_certified_lot")))
    _w(ws, "K40", _s(restart.get("identification")))
    _w(ws, "D41", _s(restart.get("approved_by")))
    _w(ws, "D42", _s(restart.get("method")))
    _w(ws, "C44", _s(data.get("containment_responsible")))


def _fill_4m(ws, fourm: Dict, base: int):
    """base = row of 'Material' header (48 or 95)."""
    for i, key in enumerate(["row_1","row_2","row_3"]):
        rd = fourm.get(key, {})
        r = base + 1 + i
        _w(ws, f"B{r}", _s(rd.get("material")))
        _w(ws, f"D{r}", _s(rd.get("method")))
        _w(ws, f"F{r}", _s(rd.get("machine")))
        r2 = base + 6 + i
        _w(ws, f"B{r2}", _s(rd.get("manpower")))
        _w(ws, f"D{r2}", _s(rd.get("environment")))
    _w(ws, f"J{base+3}", _s(fourm.get("selected_problem")))

def _fill_d4(ws, data: Dict):
    _fill_4m(ws, data.get("four_m_occurrence", {}), 48)
    _fill_5whys(ws, data.get("five_whys_occurrence", {}), 61)
    rc = data.get("root_cause_occurrence", {})
    _w(ws, "B91", _s(rc.get("root_cause")))
    _w(ws, "E91", _s(rc.get("validation_method")))

    _fill_4m(ws, data.get("four_m_non_detection", {}), 95)
    _fill_5whys(ws, data.get("five_whys_non_detection", {}), 106)
    rc2 = data.get("root_cause_non_detection", {})
    _w(ws, "B136", _s(rc2.get("root_cause")))
    _w(ws, "E136", _s(rc2.get("validation_method")))


def _fill_d5_d6(ws, d5: Dict, d6: Dict):
    d6_occ = d6.get("corrective_actions_occurrence", [])
    d6_det = d6.get("corrective_actions_detection", [])

    for idx, row in enumerate(d5.get("corrective_actions_occurrence", [])[:2]):
        r = 142 + idx
        d6r = d6_occ[idx] if idx < len(d6_occ) else {}
        _w(ws, f"B{r}", _s(row.get("action")))
        _w(ws, f"F{r}", _s(row.get("responsible")))
        _w(ws, f"G{r}", _s(row.get("due_date")))
        _w(ws, f"I{r}", _s(d6r.get("imp_date","")))
        _w(ws, f"K{r}", _s(d6r.get("evidence","")))

    for idx, row in enumerate(d5.get("corrective_actions_detection", [])[:2]):
        r = 145 + idx
        d6r = d6_det[idx] if idx < len(d6_det) else {}
        _w(ws, f"B{r}", _s(row.get("action")))
        _w(ws, f"F{r}", _s(row.get("responsible")))
        _w(ws, f"G{r}", _s(row.get("due_date")))
        _w(ws, f"I{r}", _s(d6r.get("imp_date","")))
        _w(ws, f"K{r}", _s(d6r.get("evidence","")))

    mon: Dict = d6.get("monitoring", {})
    _w(ws, "C149", _s(mon.get("monitoring_interval")))
    _w(ws, "C150", _s(mon.get("pieces_produced")))
    _w(ws, "C151", _s(mon.get("rejection_rate")))

    for idx, item in enumerate(d6.get("checklist", [])[:13]):
        r = 149 + idx
        _wb(ws, f"K{r}", item.get("shift_1", False))
        _wb(ws, f"L{r}", item.get("shift_2", False))
        _wb(ws, f"M{r}", item.get("shift_3", False))

    _w(ws, "F162", _s(mon.get("audited_by")))
    _w(ws, "F163", _s(mon.get("audit_date")))


def _fill_d7(ws, data: Dict):
    # I. Recurrence (168-170): B, C, E top-left (F=slave of E)
    for idx, risk in enumerate(data.get("recurrence_risks", [])[:3]):
        r = 168 + idx
        _w(ws,  f"B{r}", _s(risk.get("area_line_product")))
        _wb(ws, f"C{r}", bool(risk.get("similar_risk_present", False)))
        _w(ws,  f"E{r}", _s(risk.get("action_taken")))

    # II. Dissemination (174-176): B, D, F, G, J top-left
    # C=slave of B, E=slave of D, H/I=slave of G, K/L/M=slave of J
    for idx, item in enumerate(data.get("lesson_disseminations", [])[:3]):
        r = 174 + idx
        _w(ws, f"B{r}", _s(item.get("audience_team")))
        _w(ws, f"D{r}", _s(item.get("method")))
        _w(ws, f"F{r}", _s(item.get("date")))
        _w(ws, f"G{r}", _s(item.get("owner")))
        _w(ws, f"J{r}", _s(item.get("evidence")))

    # III. Replication (179-181): B, C, F, G top-left
    # D/E=slave of C, H-M=slave of G
    for idx, item in enumerate(data.get("replication_validations", [])[:3]):
        r = 179 + idx
        _w(ws, f"B{r}", _s(item.get("line_site")))
        _w(ws, f"C{r}", _s(item.get("action_replicated")))
        _w(ws, f"F{r}", _s(item.get("confirmation_method")))
        _w(ws, f"G{r}", _s(item.get("confirmed_by")))

    # IV. KB updates (184-186): B, C, F, G top-left
    # D/E=slave of C, H-M=slave of G
    for idx, item in enumerate(data.get("knowledge_base_updates", [])[:3]):
        r = 184 + idx
        _w(ws, f"B{r}", _s(item.get("document_type")))
        _w(ws, f"C{r}", _s(item.get("topic_reference")))
        _w(ws, f"F{r}", _s(item.get("owner")))
        _w(ws, f"G{r}", _s(item.get("location_link")))

    # V. Long-term monitoring — CAREFUL: row 190 D190 is slave of C190
    # row 189 & 191: B, D, F, G, L are all top-left
    # row 190:       B, C, F, G, L are top-left (D=slave of C)
    LT_FREQ_COL = {189: "D", 190: "C", 191: "D"}
    for idx, item in enumerate(data.get("long_term_monitoring", [])[:3]):
        r = 189 + idx
        freq_col = LT_FREQ_COL[r]
        _w(ws, f"B{r}",        _s(item.get("checkpoint_type")))
        _w(ws, f"{freq_col}{r}", _s(item.get("frequency")))
        _w(ws, f"F{r}",        _s(item.get("owner")))
        _w(ws, f"G{r}",        _s(item.get("start_date")))
        _w(ws, f"L{r}",        _s(item.get("notes")))

    # VI. LL conclusion (B193:M195 — B193 is top-left)
    _w(ws, "B193", _s(data.get("ll_conclusion")))


def _fill_d8(ws, data: Dict):
    closure = _s(data.get("closure_statement", ""))
    sigs: Dict = data.get("signatures", {})
    parts = []
    if sigs.get("closed_by"):   parts.append(f"Closed by: {_s(sigs['closed_by'])}")
    if sigs.get("closure_date"):parts.append(f"Date: {_s(sigs['closure_date'])}")
    if sigs.get("approved_by"): parts.append(f"Approved by: {_s(sigs['approved_by'])}")
    sig_line = "  |  ".join(parts)
    full = (closure + "\n\n" + sig_line) if (closure and sig_line) else (closure or sig_line)
    # B199:M201 merged — B199 is top-left
    _w(ws, "B199", full)


class ReportExportService:

    @staticmethod
    def generate_excel(db: Session, report_id: int) -> bytes:
        report = db.query(Report).filter(Report.id == report_id).first()
        if not report:
            raise ValueError(f"Report {report_id} not found")
        complaint: Complaint = report.complaint
        if not complaint:
            raise ValueError("Report has no associated complaint")

        steps: Dict[str, ReportStep] = {s.step_code: s for s in report.steps}

        wb = load_workbook(TEMPLATE_PATH)
        ws = wb["Sheet1"]

        _w(ws, "B2", f"PROBLEM SOLVING - 8D METHOD  |  {_s(report.report_number)}  |  {_s(complaint.complaint_name)}")

        _fill_d1(ws, _step(steps, "D1"))
        _fill_d2(ws, _step(steps, "D2"))
        _fill_d3(ws, _step(steps, "D3"))
        _fill_d4(ws, _step(steps, "D4"))
        _fill_d5_d6(ws, _step(steps, "D5"), _step(steps, "D6"))
        _fill_d7(ws, _step(steps, "D7"))
        _fill_d8(ws, _step(steps, "D8"))

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf.read()

    @staticmethod
    def get_filename(db: Session, report_id: int) -> str:
        report = db.query(Report).filter(Report.id == report_id).first()
        if not report:
            return f"8D_report_{report_id}.xlsx"
        name = _s(getattr(report.complaint, "complaint_name", ""))[:40].replace(" ", "_").replace("/", "-")
        return f"8D_{_s(report.report_number)}_{name}.xlsx"