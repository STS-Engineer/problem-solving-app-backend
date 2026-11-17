"""Read-only complaint classification shared by dashboard and list summaries."""

from collections import defaultdict
from datetime import datetime, timezone


STEP_CODES = tuple(f"D{i}" for i in range(1, 9))


def completed_8d_condition():
    """SQL counterpart of the classifier, for existing aggregate KPI queries."""
    from sqlalchemy import func, select
    from app.models.complaint import Complaint
    from app.models.report import Report
    from app.models.report_step import ReportStep

    fulfilled = (
        select(func.count(func.distinct(ReportStep.step_code)))
        .select_from(Report)
        .join(ReportStep, ReportStep.report_id == Report.id)
        .where(Report.complaint_id == Complaint.id,
               ReportStep.step_code.in_(STEP_CODES), ReportStep.status == "fulfilled")
        .correlate(Complaint)
        .scalar_subquery()
    )
    return fulfilled == len(STEP_CODES)


def open_8d_condition():
    from sqlalchemy import and_
    from app.models.complaint import Complaint
    return and_(Complaint.status != "cancelled", ~completed_8d_condition())


def operational_scope(include_cancelled=False, include_history=False):
    from sqlalchemy import or_
    from app.models.complaint import Complaint
    from app.models.enums import PlantEnum

    filters = [
        or_(Complaint.source.is_(None), Complaint.source == "monday_import")
        if include_history else Complaint.source.is_(None),
        or_(Complaint.avocarbon_plant.is_(None),
            Complaint.avocarbon_plant != PlantEnum.FRANKFURT),
    ]
    if not include_cancelled:
        filters.append(Complaint.status != "cancelled")
    return filters


def _utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def classify_complaint(complaint, steps, now=None):
    """Missing steps are incomplete; missing deadlines are never 'on time'.

    The stored state is not changed. D8's stored deadline is the overall
    forecast. A late earlier step does not imply a late overall closure.
    """
    plant = getattr(complaint.avocarbon_plant, "value", complaint.avocarbon_plant)
    eligible = complaint.source is None and plant != "FRANKFURT"
    result = {"kpi_eligible": eligible, "kpi_status": "open", "timing_status": "unknown"}
    if complaint.status == "cancelled":
        return {**result, "kpi_status": "cancelled", "timing_status": None}
    by_code = {step.step_code: step for step in steps if step.step_code in STEP_CODES}
    complete = all(code in by_code and by_code[code].status == "fulfilled" for code in STEP_CODES)
    if complete:
        result["kpi_status"] = "closed"
        # Prefer actual completion evidence; closed_at is a fallback for older data.
        completions = [_utc(by_code[code].completed_at) for code in STEP_CODES]
        finished = max(completions) if all(completions) else _utc(complaint.closed_at)
        deadline = _utc(by_code["D8"].due_date)
        if finished is not None and deadline is not None:
            result["timing_status"] = "delayed" if finished > deadline else "on_time"
    else:
        current_code = next(code for code in STEP_CODES
                            if code not in by_code or by_code[code].status != "fulfilled")
        current = by_code.get(current_code)
        deadline = _utc(current.due_date) if current is not None else None
        if deadline is not None:
            result["timing_status"] = "delayed" if _utc(now or datetime.now(timezone.utc)) > deadline else "on_time"
    return result


def classification_rows(db, complaint_filter):
    """Two bounded queries, with no lazy loads and no writes."""
    from app.models.complaint import Complaint
    from app.models.report import Report
    from app.models.report_step import ReportStep

    complaints = db.query(Complaint).filter(complaint_filter).all()
    if not complaints:
        return []
    steps = defaultdict(list)
    for cid, step in (
        db.query(Report.complaint_id, ReportStep)
        .join(ReportStep, ReportStep.report_id == Report.id)
        .filter(Report.complaint_id.in_([c.id for c in complaints]))
        .all()
    ):
        steps[cid].append(step)
    now = datetime.now(timezone.utc)
    return [(c, classify_complaint(c, steps[c.id], now)) for c in complaints]


def summarize(rows):
    totals = dict.fromkeys(("received", "open", "open_on_time", "open_with_delay",
                          "open_timing_unknown", "closed", "closed_on_time",
                          "closed_with_delay", "closed_timing_unknown", "cancelled"), 0)
    for _, classification in rows:
        if not classification["kpi_eligible"]:
            continue
        state = classification["kpi_status"]
        totals["received"] += 1
        totals[state] += 1
        if state != "cancelled":
            timing = {"on_time": "on_time", "delayed": "with_delay", "unknown": "timing_unknown"}[classification["timing_status"]]
            totals[f"{state}_{timing}"] += 1
    return totals
