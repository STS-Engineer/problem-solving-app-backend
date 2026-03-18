# app/api/routes/dashboard.py
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import datetime

from app.api.deps import get_db
from app.services.dashboard_service import DashboardService

router = APIRouter()


@router.get("/available-years")
def get_available_years(db: Session = Depends(get_db)):
    """Years that have at least one complaint."""
    from sqlalchemy import func, extract, distinct
    from app.models.complaint import Complaint

    years = (
        db.query(distinct(extract("year", Complaint.created_at)).label("year"))
        .order_by(extract("year", Complaint.created_at).desc())
        .all()
    )
    year_list = [int(y.year) for y in years if y.year]
    return {
        "years": year_list,
        "current_year": datetime.now().year,
        "default_year": year_list[0] if year_list else datetime.now().year,
    }


@router.get("/stats")
def get_dashboard_stats(
    year: Optional[int] = Query(None, description="Year filter (default: current year)"),
    month: Optional[int] = Query(None, ge=1, le=12, description="Month filter 1-12"),
    quarter: Optional[int] = Query(None, ge=1, le=4, description="Quarter filter 1-4"),
    db: Session = Depends(get_db),
):
    """
    Full dashboard statistics.

    Filters:
    - year  — defaults to current year
    - month — if set, overrides quarter
    - quarter — Q1-Q4 (ignored when month is set)
    """
    if year is None:
        year = datetime.now().year

    current_year = datetime.now().year
    if year < 2020 or year > current_year + 1:
        raise HTTPException(
            status_code=400,
            detail=f"Year must be between 2020 and {current_year + 1}",
        )

    if month and quarter:
        # month takes priority
        quarter = None

    return DashboardService.get_dashboard_stats(db, year=year, month=month, quarter=quarter)


@router.get("/stats/realtime")
def get_realtime_stats(
    year: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """Lightweight polling endpoint — total count + last update only."""
    if year is None:
        year = datetime.now().year

    from sqlalchemy import func, extract
    from app.models.complaint import Complaint

    yf = extract("year", Complaint.created_at) == year

    total = db.query(func.count(Complaint.id)).filter(yf).scalar() or 0
    open_count = (
        db.query(func.count(Complaint.id))
        .filter(yf, Complaint.status.in_(["open", "in_progress", "under_review"]))
        .scalar() or 0
    )
    last_update = db.query(func.max(Complaint.updated_at)).filter(yf).scalar()
    recent = (
        db.query(Complaint).filter(yf).order_by(Complaint.created_at.desc()).limit(5).all()
    )

    return {
        "total_complaints": total,
        "open_complaints": open_count,
        "last_update": last_update.isoformat() if last_update else None,
        "recent_complaints": [
            {
                "id": c.id,
                "reference_number": c.reference_number,
                "complaint_name": c.complaint_name,
                "status": c.status,
                "customer": c.customer,
                "avocarbon_plant": c.avocarbon_plant,
                "created_at": c.created_at.isoformat(),
            }
            for c in recent
        ],
        "year": year,
    }


@router.get("/stats/comparison")
def get_year_comparison(
    years: List[int] = Query(None),
    db: Session = Depends(get_db),
):
    """Year-over-year comparison."""
    if not years:
        cy = datetime.now().year
        years = [cy - 2, cy - 1, cy]

    comparison = []
    for y in years:
        stats = DashboardService.get_dashboard_stats(db, y)
        comparison.append(
            {
                "year": y,
                "total_complaints": stats["total_complaints"],
                "top_plant": stats["top_plant"],
                "overdue": stats["overdue_complaints"]["total"],
                "defect_types_count": len(stats["defect_types"]),
                "product_types_count": len(stats["product_types"]),
            }
        )
    return {"comparison": comparison, "years_compared": years}