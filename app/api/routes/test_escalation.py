"""
HOW TO TEST D1 ESCALATION IN 30 MINUTES
========================================

STEP 1 — Add env variables to your .env file
─────────────────────────────────────────────

    TEST_ESCALATION=true
    SMTP_HOST=smtp.gmail.com          # or your SMTP
    SMTP_PORT=587
    SMTP_USER=your@email.com
    SMTP_PASSWORD=your-app-password

With TEST_ESCALATION=true:
  • Scheduler runs every 5 min instead of 30
  • SLA thresholds compressed 1/48:  L1=30min, L2=60min, L3=90min, L4=120min
  • D1 due_date is set to NOW() when you create the complaint
    (see Step 2 below)


STEP 2 — Create a test complaint via the API
─────────────────────────────────────────────
Use curl, Postman, or your frontend.

    POST /complaints
    {
      "complaint_name":        "TEST — D1 30min escalation",
      "customer":              "Test Customer",
      "customer_plant_name":   "Test Plant",
      "product_line":          "CARBON",
      "avocarbon_plant":       "PLANT_A",
      "priority":              "urgent",
      "cqt_email":             "your@email.com",
      "quality_manager_email": "your@email.com",
      "plant_manager_email":   "your@email.com"
    }

  Note: set all three emails to YOUR email so you receive all levels.


STEP 3 — Set D1 due_date to NOW (so it's already overdue in 30 min)
─────────────────────────────────────────────────────────────────────
Option A — via API (add a test endpoint):

    POST /dev/test-escalation/{complaint_id}

    This sets D1 due_date = NOW() so the scheduler picks it up
    on next run.  See the test route in test_escalation_route.py.


Option B — directly in the database:

    UPDATE report_steps
    SET due_date = NOW()         -- sets due_date to right now
    WHERE complaint_id = <id>    -- replace with your complaint ID
      AND step_code = 'D1';


STEP 4 — Watch the scheduler fire
───────────────────────────────────
In your server logs you'll see (every 5 min):

    INFO  Escalation scan: 1 active steps checked
    INFO  ✓ Escalation L1 sent | TEST-2024-0001 / D1 | overdue 0.1h | to: ['your@email.com']

After 30 min → L1 email arrives.
After 60 min → L2 email arrives (CQT added).
After 90 min → L3 email arrives (Plant Mgr added).
After 120 min → L4 final notice.


STEP 5 — Test route (add to your FastAPI app for dev only)
───────────────────────────────────────────────────────────
See test_escalation_route.py — mounts at /dev/test-escalation
"""

# ─── test_escalation_route.py ──────────────────────────────────────────────────
"""
Paste this into your routers directory and include ONLY in dev/test environments.

    # main.py (conditional include)
    import os
    if os.getenv("TEST_ESCALATION") == "true":
        from app.routers.test_escalation_route import router as dev_router
        app.include_router(dev_router)
"""

from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.db.session import get_db
from app.models.complaint import Complaint
from app.models.report_step import ReportStep
from app.services.escalation_service import check_and_escalate_all

router = APIRouter(prefix="/dev", tags=["dev-test"])


@router.post("/test-escalation/{complaint_id}")
async def set_d1_overdue_now(
    complaint_id: int,
    offset_minutes: int = 0,      # extra minutes to subtract (make it MORE overdue)
    db: AsyncSession = Depends(get_db),
):
    """
    Dev-only endpoint.
    Sets D1 due_date = NOW() - offset_minutes so it is already overdue
    and the next scheduler run will fire the escalation.

    Usage:
        POST /dev/test-escalation/1             → due_date = now  (fires on next tick)
        POST /dev/test-escalation/1?offset_minutes=35  → overdue by 35min (fires L1 immediately)
    """
    # Verify complaint exists
    c_res = await db.execute(select(Complaint).where(Complaint.id == complaint_id))
    complaint = c_res.scalar_one_or_none()
    if not complaint:
        raise HTTPException(404, "Complaint not found")

    # Get D1 step
    s_res = await db.execute(
        select(ReportStep).where(
            ReportStep.complaint_id == complaint_id,
            ReportStep.step_code == "D1",
        )
    )
    step = s_res.scalar_one_or_none()
    if not step:
        raise HTTPException(404, "D1 step not found — make sure a Report was created for this complaint")

    # Set due_date to now minus offset so it's already overdue
    new_due = datetime.now(timezone.utc) - timedelta(minutes=offset_minutes)
    step.due_date       = new_due
    step.escalation_count = 0          # reset so escalation fires fresh
    step.escalation_sent_at = None
    step.is_overdue     = False        # will be set true by escalation_service
    step.status         = "not_started"

    await db.commit()

    return {
        "message": f"D1 due_date set to {new_due.isoformat()}",
        "overdue_by_minutes": offset_minutes,
        "complaint_reference": complaint.reference_number,
        "next_step": (
            "Run POST /dev/trigger-escalation-now to fire immediately, "
            "or wait for scheduler (every 5 min in test mode)"
        ),
    }


@router.post("/trigger-escalation-now")
async def trigger_escalation_immediately(db: AsyncSession = Depends(get_db)):
    """
    Manually trigger the escalation check RIGHT NOW without waiting
    for the scheduler interval.  Useful for instant testing.
    """
    await check_and_escalate_all(db)
    return {"message": "Escalation check completed — check your email and server logs"}


@router.get("/escalation-status/{complaint_id}")
async def get_escalation_status(
    complaint_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Check current escalation state for all steps of a complaint."""
    s_res = await db.execute(
        select(ReportStep)
        .where(ReportStep.complaint_id == complaint_id)
        .order_by(ReportStep.step_code)
    )
    steps = s_res.scalars().all()

    now = datetime.now(timezone.utc)
    result = []
    for s in steps:
        due = s.due_date
        if due and not due.tzinfo:
            due = due.replace(tzinfo=timezone.utc)
        overdue_min = round((now - due).total_seconds() / 60, 1) if due and not s.completed_at and now > due else None
        result.append({
            "step_code":       s.step_code,
            "status":          s.status,
            "due_date":        s.due_date.isoformat() if s.due_date else None,
            "overdue_minutes": overdue_min,
            "escalation_count":s.escalation_count,
            "escalation_sent_at": s.escalation_sent_at.isoformat() if s.escalation_sent_at else None,
            "completed_at":    s.completed_at.isoformat() if s.completed_at else None,
        })

    return {"complaint_id": complaint_id, "steps": result}