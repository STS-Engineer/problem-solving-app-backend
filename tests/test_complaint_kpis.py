from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace as Row

import pytest

from app.services.complaint_kpis import classify_complaint, summarize

NOW = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)


def complaint(**overrides):
    return Row(**{"avocarbon_plant": "SAME", "source": None, "status": "D3",
                  "closed_at": NOW, **overrides})


def steps(complete=False):
    return [Row(step_code=f"D{i}", status="fulfilled" if complete or i < 3 else "draft",
                due_date=NOW + timedelta(days=1), completed_at=NOW if complete or i < 3 else None)
            for i in range(1, 9)]


def test_current_step_deadline_controls_open_delay_not_future_steps():
    ss = steps()
    ss[7].due_date = NOW - timedelta(days=10)
    assert classify_complaint(complaint(), ss, NOW)["timing_status"] == "on_time"
    ss[2].due_date = NOW - timedelta(seconds=1)
    assert classify_complaint(complaint(), ss, NOW)["timing_status"] == "delayed"


def test_deadline_is_inclusive_and_handles_naive_timestamps():
    ss = steps()
    ss[2].due_date = NOW.replace(tzinfo=None)
    assert classify_complaint(complaint(), ss, NOW)["timing_status"] == "on_time"


def test_closed_label_does_not_override_incomplete_steps():
    assert classify_complaint(complaint(status="closed"), steps(), NOW)["kpi_status"] == "open"


def test_all_eight_steps_override_stale_open_label():
    assert classify_complaint(complaint(status="D8"), steps(True), NOW)["kpi_status"] == "closed"


@pytest.mark.parametrize("offset,expected", [(0, "on_time"), (-1, "delayed"), (1, "on_time")])
def test_closed_timing_compares_actual_completion_to_d8_forecast(offset, expected):
    ss = steps(True)
    ss[7].due_date = NOW + timedelta(seconds=offset)
    # An earlier step being late is not the overall closure SLA.
    ss[0].due_date = NOW - timedelta(days=5)
    assert classify_complaint(complaint(), ss, NOW)["timing_status"] == expected


def test_missing_and_duplicate_step_codes_cannot_complete_an_8d():
    ss = steps(True)[:7]
    ss.append(ss[0])
    result = classify_complaint(complaint(status="closed"), ss, NOW)
    assert result["kpi_status"] == "open"
    assert result["timing_status"] == "unknown"


def test_no_report_or_deadline_is_not_assumed_on_time():
    assert classify_complaint(complaint(), [], NOW)["timing_status"] == "unknown"
    ss = steps(True)
    ss[7].due_date = None
    assert classify_complaint(complaint(), ss, NOW)["timing_status"] == "unknown"


def test_cancelled_takes_precedence_even_if_all_steps_complete():
    result = classify_complaint(complaint(status="cancelled"), steps(True), NOW)
    assert result["kpi_status"] == "cancelled"
    assert result["timing_status"] is None


def test_history_and_frankfurt_are_excluded_without_changing_stored_status():
    for c in [complaint(source="monday_import", status="closed"), complaint(avocarbon_plant="FRANKFURT")]:
        before = vars(c).copy()
        assert not classify_complaint(c, [], NOW)["kpi_eligible"]
        assert vars(c) == before


def test_received_and_timing_subtotals_reconcile_including_missing_timing():
    rows = []
    for c, ss in [(complaint(), steps()), (complaint(), steps(True)),
                  (complaint(), []), (complaint(status="cancelled"), []),
                  (complaint(avocarbon_plant="FRANKFURT"), [])]:
        rows.append((c, classify_complaint(c, ss, NOW)))
    result = summarize(rows)
    assert result["received"] == 4
    assert result["received"] == result["open"] + result["closed"] + result["cancelled"]
    for state in ("open", "closed"):
        assert result[state] == sum(result[f"{state}_{suffix}"] for suffix in ("on_time", "with_delay", "timing_unknown"))


def test_dashboard_queries_exclude_history_cancelled_and_frankfurt():
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
    from app.services.complaint_kpis import classification_rows

    @compiles(JSONB, "sqlite")
    def sqlite_jsonb(element, compiler, **kw):
        return "JSON"

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Complaint.__table__, Report.__table__, ReportStep.__table__])
    with Session(engine) as db:
        for i, overrides in enumerate([{}, {"source": "monday_import"},
                                       {"avocarbon_plant": "FRANKFURT"}, {"status": "cancelled"}], 1):
            db.add(Complaint(id=i, reference_number=f"TEST-{i}", complaint_name="Fixture", product_line="BRUSH",
                             customer_complaint_date=date(2026, 1, 1), complaint_opening_date=date(2026, 1, 1),
                             **{"avocarbon_plant": "SAME", "status": "D1", **overrides}))
        db.commit()
        performance_filter = DashboardService._build_filter(2026)
        received_filter = DashboardService._build_filter(2026, include_cancelled=True)
        assert [c.id for c, _ in classification_rows(db, performance_filter)] == [1]
        summary = summarize(classification_rows(db, received_filter))
        assert summary["received"] == 2 and summary["cancelled"] == 1
        plants = DashboardService._get_total_by_plant(db, performance_filter)
        rankings = DashboardService._get_complaints_per_customer_avocarbon(db, performance_filter)
        assert sum(row["count"] for row in rankings) == 1
        assert len(plants) == 1 and plants[0]["plant"] == "SAME"
        assert plants[0]["open_timing_unknown"] == 1
        stats = DashboardService.get_dashboard_stats(db, year=2026)
        assert stats["kpi_summary"] == summary
        assert stats["total_complaints"] == 2
        assert sum(row["count"] for row in stats["total_by_plant"]) == 1
        assert classification_rows(db, DashboardService._build_filter(2025)) == []
        assert classification_rows(db, DashboardService._build_filter(2026, plant="FRANKFURT")) == []
