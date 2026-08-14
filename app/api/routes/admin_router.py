"""
app/api/admin_router.py
────────────────────────
Admin / ops endpoints.

  POST /api/v1/admin/trigger-kpi-report
      Immediately runs the monthly KPI report job for the given month/year
      (defaults to the previous calendar month).
      Useful for testing on staging without waiting for the 1st of the month.

  GET  /api/v1/admin/scheduler-status
      Returns current APScheduler job list and next run times.

  POST /api/v1/admin/graph/subscribe
      One-time bootstrap: registers the Microsoft Graph change-notification
      subscription on the shared claims mailbox (internal intake agent).
      Re-run after changing GRAPH_MAILBOX_UPN/GRAPH_NOTIFICATION_URL, or if
      the subscription was deleted/expired without the renewal job catching it.

  GET  /api/v1/admin/graph/subscription-status
      Shows the currently tracked subscription(s) and expiry.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.graph_subscription import GraphSubscription
from app.services.graph_client import is_configured as graph_is_configured
from app.services.graph_subscription_service import create_subscription
from app.services.kpi_report.kpi_email_service import _PLANT_HTML
from app.services.kpi_report.kpi_report_pdf import per_plant_report

logger = logging.getLogger(__name__)


MONTH_NAMES = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
router = APIRouter()


@router.post("/trigger-kpi-report", summary="Manually trigger monthly KPI email report")
async def trigger_kpi_report(
    background_tasks: BackgroundTasks,
    month: Optional[int] = Query(
        default=None,
        ge=1,
        le=12,
        description="Month to report on (1-12). Defaults to last month.",
    ),
    year: Optional[int] = Query(
        default=None,
        description="Year to report on. Defaults to current year.",
    ),
):
    """
    Trigger the KPI PDF report pipeline immediately.

    - Generates per-plant PDFs, emails them to the configured CQT recipients.
    - Generates a consolidated PDF and emails the quality manager.
    - Runs in the background so the HTTP response returns immediately.
    """
    today = date.today()
    if month is None:
        month = today.month - 1 if today.month > 1 else 12
    if year is None:
        year = today.year if today.month > 1 else today.year - 1

    logger.info("Manual KPI report trigger: %s %d", MONTH_NAMES[month - 1], year)

    def _run(month: int, year: int) -> None:
        from app.db.session import SessionLocal  # noqa

        _db = SessionLocal()
        try:
            # Override the "previous month" logic inside the service by
            # temporarily patching date.today — simplest approach is to call
            # a dedicated helper that accepts explicit month/year.
            _send_for_period(_db, month, year)
            _db.commit()
        except Exception:
            logger.exception("Manual KPI report failed")
            _db.rollback()
        finally:
            _db.close()

    def _send_for_period(db: Session, month: int, year: int) -> None:
        from app.services.dashboard_service import DashboardService  # noqa
        from app.services.kpi_report.kpi_report_pdf import (  # noqa
            consolidated_report,
        )
        from app.services.kpi_report.kpi_email_service import (  # noqa
            DEFAULT_MANAGER_EMAIL,
            _MANAGER_HTML,
            _plant_emails,
            _send_email,
        )

        data = DashboardService.get_dashboard_stats(db, year=year, month=month)
        month_name = MONTH_NAMES[month - 1]
        month_name_3 = month_name[:3]
        plant_emails = _plant_emails()
        manager_email = DEFAULT_MANAGER_EMAIL
        plants = sorted(
            {
                r["plant"]
                for r in data.get("total_by_plant", [])
                if r["plant"] is not None
            }
        )

        for plant in plants:
            recipient = plant_emails.get(plant, manager_email)
            pdf_bytes = per_plant_report(data, plant, month, year)
            monthly_data = data.get("monthly_data", [])
            m_count = next(
                (r.get(plant, 0) for r in monthly_data if r["month"] == month_name_3), 0
            )
            ytd = sum(r.get(plant, 0) for r in monthly_data)
            target = data.get("monthly_targets", {}).get(plant, 0)
            overdue = next(
                (
                    r["count"]
                    for r in data.get("overdue_complaints", {}).get("by_plant", [])
                    if r["plant"] == plant
                ),
                0,
            )
            html = _PLANT_HTML.format(
                plant=plant,
                month_name=month_name,
                year=year,
                month_count=m_count,
                ytd=ytd,
                target=target,
                overdue=overdue,
                today=date.today().strftime("%d %B %Y"),
            )
            _send_email(
                to_addrs=[recipient],
                subject=f"[AVOCarbon] {plant} KPI Report — {month_name} {year}",
                html_body=html,
                attachments=[
                    {
                        "filename": f"AVOCarbon_{plant}_KPI_{month_name}_{year}.pdf",
                        "data": pdf_bytes,
                    }
                ],
            )

        # Consolidated
        # consolidated_pdf = consolidated_report(data, month, year)
        # plant_rows_html = ""
        # monthly_data = data.get("monthly_data", [])
        # for plant in plants:
        #     m_c = next((r.get(plant,0) for r in
        # monthly_data if r["month"]==month_name_3),0)
        #     ytd_p = sum(r.get(plant,0) for r in monthly_data)
        #     tgt_p = data.get("monthly_targets",{}).get(plant,0)
        #     od_p  = next((r["count"] for r in data.get("overdue_complaints",{})
        #                  .get("by_plant",[]) if r["plant"]==plant),0)
        #     plant_rows_html += (
        #         f"<tr><td><strong>{plant}</strong></td>"
        #         f"<td>{m_c}</td><td>{ytd_p}</td><td>{tgt_p}</td>"
        #         f'<td style="color:{"#C0392B" if od_p else "#4A7C59"};">'
        #         f"<strong>{od_p}</strong></td></tr>"
        #     )
        # manager_html = _MANAGER_HTML.format(
        #     month_name=month_name, year=year,
        #     total=data.get("total_complaints",0),
        #     top_plant=data.get("top_plant",{}).get("plant","—"),
        #     overdue=data.get("overdue_complaints",{}).get("total",0),
        #     reports=data.get("report_stats",{}).get("total_reports",0),
        #     plant_rows=plant_rows_html,
        #     today=date.today().strftime("%d %B %Y"),
        # )
        # _send_email(
        #     to_addrs=[manager_email],
        #     subject=f"[AVOCarbon] Quality KPI Report (All Plants)
        #  — {month_name} {year}",
        #     html_body=manager_html,
        #     attachments=[{
        #         "filename": f"AVOCarbon_AllPlants_KPI_{month_name}_{year}.pdf",
        #         "data": consolidated_pdf,
        #     }],
        # )

    background_tasks.add_task(_run, month, year)
    return {
        "status": "queued",
        "message": f"KPI report for {MONTH_NAMES[month-1]} {year} "
        "is being generated and emailed.",
        "month": month,
        "year": year,
    }


@router.post(
    "/graph/subscribe",
    summary="One-time bootstrap: create the Graph mailbox-watch subscription",
)
def subscribe_graph_mailbox(db: Session = Depends(get_db)):
    """
    Registers a Microsoft Graph change-notification subscription so new mail
    in GRAPH_MAILBOX_UPN's Inbox is pushed to GRAPH_NOTIFICATION_URL. Call
    this once after deploying with the AZURE_*/GRAPH_* env vars set — the
    scheduled renewal job keeps the subscription alive after that.
    """
    if not graph_is_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "Graph is not configured — set AZURE_TENANT_ID, AZURE_CLIENT_ID, "
                "AZURE_CLIENT_SECRET and GRAPH_MAILBOX_UPN first."
            ),
        )
    try:
        sub = create_subscription(db)
    except Exception as exc:
        logger.exception("Failed to create Graph subscription")
        raise HTTPException(status_code=502, detail=f"Graph rejected the subscription: {exc}")

    return {
        "status": "created",
        "subscription_id": sub.subscription_id,
        "resource": sub.resource,
        "expires_at": sub.expires_at.isoformat(),
    }


@router.get(
    "/graph/subscription-status",
    summary="Show tracked Graph mailbox subscription(s)",
)
def graph_subscription_status(db: Session = Depends(get_db)):
    rows = (
        db.query(GraphSubscription)
        .order_by(GraphSubscription.created_at.desc())
        .limit(10)
        .all()
    )
    return {
        "graph_configured": graph_is_configured(),
        "subscriptions": [
            {
                "subscription_id": r.subscription_id,
                "resource": r.resource,
                "status": r.status,
                "expires_at": r.expires_at.isoformat() if r.expires_at else None,
                "last_renewed_at": r.last_renewed_at.isoformat()
                if r.last_renewed_at
                else None,
                "last_error": r.last_error,
                "last_notification_at": r.last_notification_at.isoformat()
                if r.last_notification_at
                else None,
                "last_notification_message_id": r.last_notification_message_id,
                "notification_count": r.notification_count,
            }
            for r in rows
        ],
    }
