from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.complaint import Complaint
from app.models.report import Report
from app.models.report_step import ReportStep
from app.models.step_conversation import StepConversation
from app.services.dashboard_service import DashboardService


@compiles(JSONB, "sqlite")
def sqlite_jsonb(element, compiler, **kw):
    return "JSON"


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Complaint.__table__, Report.__table__, ReportStep.__table__])
    with Session(engine) as session:
        yield session
    engine.dispose()


def add_complaint(db, ident, **overrides):
    values = dict(id=ident, reference_number=f"TEST-{ident}", complaint_name="Fixture",
                  product_line="BRUSH", avocarbon_plant="SAME", status="open",
                  customer_complaint_date=date(2026, 1, 31), complaint_opening_date=date(2026, 2, 2))
    values.update(overrides)
    complaint = Complaint(**values)
    db.add(complaint)
    db.flush()
    return complaint


def add_report(db, complaint, complete=False):
    db.add(Report(id=complaint.id, complaint_id=complaint.id,
                  report_number=f"8D-{complaint.id}", title="Fixture", plant=complaint.avocarbon_plant))
    db.flush()
    for i in range(1, 9):
        db.add(ReportStep(report_id=complaint.id, step_code=f"D{i}", step_name=f"Step {i}",
                          status="fulfilled" if complete else "draft",
                          completed_at=complaint.closed_at if complete else None,
                          due_date=datetime(2026, 3, 4)))
    db.flush()


def test_archived_history_restores_target_without_entering_current_actuals(db):
    add_complaint(db, 1)
    add_complaint(db, 2, source="monday_import", status="closed",
                  customer_complaint_date=date(2025, 1, 31), complaint_opening_date=date(2025, 2, 2))
    add_complaint(db, 3, source="monday_import", status="closed")
    add_complaint(db, 4, source="monday_import", avocarbon_plant="FRANKFURT",
                  customer_complaint_date=date(2025, 1, 31), complaint_opening_date=date(2025, 2, 2))
    add_complaint(db, 5, source="monday_import", status="cancelled",
                  customer_complaint_date=date(2025, 1, 31), complaint_opening_date=date(2025, 2, 2))
    scope = DashboardService._build_filter(2026)
    rows = DashboardService._get_monthly_vs_target(db, scope, 2026)
    jan = next(r for r in rows if r["plant"] == "SAME" and r["month"] == "Jan")
    feb = next(r for r in rows if r["plant"] == "SAME" and r["month"] == "Feb")
    assert jan["actual"] == 1 and jan["prev_year_actual"] == 1
    assert jan["target"] == .85 and jan["no_baseline"] is False
    assert feb["actual"] == 0 and feb["prev_year_actual"] == 0
    assert feb["target"] == 0 and feb["no_baseline"] is False
    assert all(r["plant"] != "FRANKFURT" for r in rows)
    assert db.query(Complaint).filter(DashboardService._build_filter(2025)).count() == 0
    assert db.query(Complaint).filter(scope).count() == 1


def test_absent_baseline_is_unknown_not_zero_or_a_target_breach(db):
    add_complaint(db, 1, avocarbon_plant="MONTERREY")
    rows = DashboardService._get_monthly_vs_target(db, DashboardService._build_filter(2026), 2026)
    row = next(r for r in rows if r["plant"] == "MONTERREY" and r["month"] == "Jan")
    assert row["actual"] == 1
    assert row["target"] == 0 and row["delta"] is None and row["on_target"] is None
    assert row["no_baseline"] is True and row["zero_target_breach"] is False


def test_monterrey_29_days_is_opening_response_not_closure(db):
    add_complaint(db, 1, avocarbon_plant="MONTERREY", quality_issue_warranty="CS1",
                  customer_complaint_date=date(2026, 10, 1), complaint_opening_date=date(2026, 10, 30))
    c = add_complaint(db, 2, avocarbon_plant="POITIERS", quality_issue_warranty="CS1",
                      customer_complaint_date=date(2026, 5, 22), complaint_opening_date=date(2026, 5, 22))
    add_report(db, c)
    scope = DashboardService._build_filter(2026)
    ack = DashboardService._get_acknowledgement_delay(db, scope)
    values = {r["plant"]: r["avg_days"] for r in ack["by_plant"]}
    assert values["MONTERREY"] == 29 and values["POITIERS"] == 0
    assert DashboardService._get_resolution_cycle_time(db, scope)["avg_days_overall"] is None
    assert DashboardService._get_resolution_cycle_time(db, scope)["by_plant"] == []
    assert DashboardService._get_overdue_complaints(db, scope)["total"] == 1


def test_only_completed_noncancelled_8ds_enter_closure_average(db):
    c = add_complaint(db, 1, status="closed", closed_at=datetime(2026, 2, 12))
    add_report(db, c, complete=True)
    open_c = add_complaint(db, 2, avocarbon_plant="MONTERREY", closed_at=datetime(2026, 6, 1))
    add_report(db, open_c)
    cancelled = add_complaint(db, 3, status="cancelled", closed_at=datetime(2026, 6, 1))
    add_report(db, cancelled, complete=True)
    result = DashboardService._get_resolution_cycle_time(db, DashboardService._build_filter(2026))
    assert result["avg_days_overall"] == 10
    assert result["by_plant"] == [{"plant": "SAME", "avg_days": 10.0, "count": 1}]
