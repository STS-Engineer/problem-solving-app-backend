# app/api/routes/dashboard.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import distinct, extract, func
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.complaint import Complaint
from app.services.dashboard_service import DashboardService

router = APIRouter()


<<<<<<< Updated upstream
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
=======
MIN_SUPPORTED_YEAR = 2020
MAX_COMPARISON_YEARS = 5
OPEN_COMPLAINT_STATUSES = {"open", "in_progress", "under_review"}


def _current_year() -> int:
    return datetime.now(timezone.utc).year


def _normalize_plant(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _validate_year(year: Optional[int]) -> int:
    current_year = _current_year()
    selected_year = year or current_year

    if selected_year < MIN_SUPPORTED_YEAR or selected_year > current_year + 1:
        raise HTTPException(
            status_code=400,
            detail=(
                f"year must be between {MIN_SUPPORTED_YEAR} " f"and {current_year + 1}"
            ),
        )

    return selected_year


def _validate_month(month: Optional[int]) -> Optional[int]:
    if month is None:
        return None

    if month < 1 or month > 12:
        raise HTTPException(
            status_code=400,
            detail="month must be between 1 and 12",
        )

    return month


def _validate_quarter(quarter: Optional[int]) -> Optional[int]:
    if quarter is None:
        return None

    if quarter < 1 or quarter > 4:
        raise HTTPException(
            status_code=400,
            detail="quarter must be between 1 and 4",
        )

    return quarter


def _validate_period_filters(
    year: Optional[int],
    month: Optional[int],
    quarter: Optional[int],
) -> Dict[str, Optional[int]]:
    selected_year = _validate_year(year)
    selected_month = _validate_month(month)
    selected_quarter = _validate_quarter(quarter)

    if selected_month is not None and selected_quarter is not None:
        raise HTTPException(
            status_code=400,
            detail="month and quarter cannot be used together",
        )

    return {
        "year": selected_year,
        "month": selected_month,
        "quarter": selected_quarter,
    }


def _validate_year_list(years: Optional[List[int]]) -> List[int]:
    current_year = _current_year()

    if not years:
        return [current_year - 2, current_year - 1, current_year]

    cleaned = sorted(set(years))

    if len(cleaned) > MAX_COMPARISON_YEARS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"at most {MAX_COMPARISON_YEARS} years can be compared "
                f"in one request"
            ),
        )

    for year in cleaned:
        if year < MIN_SUPPORTED_YEAR or year > current_year + 1:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"each year must be between {MIN_SUPPORTED_YEAR} "
                    f"and {current_year + 1}"
                ),
            )

    return cleaned


def _build_intake_year_filter(year: int):
    return (
        Complaint.customer_complaint_date.isnot(None),
        extract("year", Complaint.customer_complaint_date) == year,
    )


def _build_intake_period_filter(
    year: int,
    month: Optional[int] = None,
    quarter: Optional[int] = None,
) -> List[Any]:
    filters: List[Any] = list(_build_intake_year_filter(year))

    if month is not None:
        filters.append(extract("month", Complaint.customer_complaint_date) == month)
    elif quarter is not None:
        quarter_months = {
            1: [1, 2, 3],
            2: [4, 5, 6],
            3: [7, 8, 9],
            4: [10, 11, 12],
        }
        filters.append(
            extract("month", Complaint.customer_complaint_date).in_(
                quarter_months[quarter]
            )
        )

    return filters


def _get_open_complaints_count(
    db: Session,
    year: int,
    month: Optional[int] = None,
    quarter: Optional[int] = None,
) -> int:
    period_filters = _build_intake_period_filter(
        year=year,
        month=month,
        quarter=quarter,
    )

    return (
        db.query(func.count(Complaint.id))
        .filter(
            *period_filters,
            Complaint.status.in_(list(OPEN_COMPLAINT_STATUSES)),
        )
        .scalar()
        or 0
    )


@router.get("/available-years")
def get_available_years(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Return the available reporting years based on complaint intake date
    (`customer_complaint_date`).
    """
    years = (
        db.query(
            distinct(extract("year", Complaint.customer_complaint_date)).label("year")
        )
        .filter(Complaint.customer_complaint_date.isnot(None))
        .order_by(extract("year", Complaint.customer_complaint_date).desc())
        .all()
    )

    year_list = [int(row.year) for row in years if row.year is not None]
    current_year = _current_year()

    return {
        "years": year_list,
        "current_year": current_year,
        "default_year": year_list[0] if year_list else current_year,
>>>>>>> Stashed changes
    }


@router.get("/stats")
def get_dashboard_stats(
<<<<<<< Updated upstream
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

=======
    year: Optional[int] = Query(
        default=None,
        description="Reporting year. Defaults to current year.",
    ),
    month: Optional[int] = Query(
        default=None,
        description="Optional month filter (1-12).",
    ),
    quarter: Optional[int] = Query(
        default=None,
        description="Optional quarter filter (1-4). Cannot be used with month.",
    ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Return the full dashboard payload.

    Supports yearly, monthly, and quarterly filtering.
    """
    filters = _validate_period_filters(
        year=year,
        month=month,
        quarter=quarter,
    )

    stats = DashboardService.get_dashboard_stats(
        db=db,
        year=filters["year"],
        month=filters["month"],
        quarter=filters["quarter"],
    )

    return stats
>>>>>>> Stashed changes


@router.get("/stats/realtime")
def get_realtime_stats(
<<<<<<< Updated upstream
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
=======
    year: Optional[int] = Query(
        default=None,
        description="Reporting year. Defaults to current year.",
    ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Return a lightweight snapshot for fast polling.

    Scope:
    - complaint totals for the selected intake year
    - open complaints for the selected intake year
    - most recent complaint updates within the selected intake year
    """
    selected_year = _validate_year(year)
    year_filters = _build_intake_period_filter(year=selected_year)

    total_complaints = (
        db.query(func.count(Complaint.id)).filter(*year_filters).scalar() or 0
    )

    open_complaints = _get_open_complaints_count(
        db=db,
        year=selected_year,
    )

    last_update = (
        db.query(func.max(Complaint.updated_at)).filter(*year_filters).scalar()
    )

    recent_complaints = (
        db.query(Complaint)
        .filter(*year_filters)
        .order_by(Complaint.updated_at.desc(), Complaint.id.desc())
        .limit(5)
        .all()
>>>>>>> Stashed changes
    )

    return {
        "total_complaints": total_complaints,
        "open_complaints": open_complaints,
        "last_update": last_update.isoformat() if last_update else None,
        "recent_complaints": [
            {
<<<<<<< Updated upstream
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
=======
                "id": complaint.id,
                "reference_number": complaint.reference_number,
                "complaint_name": complaint.complaint_name,
                "status": complaint.status,
                "customer": complaint.customer,
                "avocarbon_plant": _normalize_plant(complaint.avocarbon_plant),
                "created_at": (
                    complaint.created_at.isoformat() if complaint.created_at else None
                ),
                "updated_at": (
                    complaint.updated_at.isoformat() if complaint.updated_at else None
                ),
                "customer_complaint_date": (
                    complaint.customer_complaint_date.isoformat()
                    if complaint.customer_complaint_date
                    else None
                ),
            }
            for complaint in recent_complaints
        ],
        "year": selected_year,
>>>>>>> Stashed changes
    }


@router.get("/stats/comparison")
def get_year_comparison(
<<<<<<< Updated upstream
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
=======
    years: Optional[List[int]] = Query(
        default=None,
        description="Years to compare, e.g. ?years=2024&years=2025",
    ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Compare selected years using a compact KPI set.

    The comparison intentionally uses lightweight metrics instead of returning
    the full dashboard payload for each year.
    """
    selected_years = _validate_year_list(years)
    comparison: List[Dict[str, Any]] = []

    for selected_year in selected_years:
        stats = DashboardService.get_dashboard_stats(
            db=db,
            year=selected_year,
        )

        comparison.append(
            {
                "year": selected_year,
                "total_complaints": stats["total_complaints"],
                "top_plant": stats["top_plant"],
                "open_complaints": _get_open_complaints_count(
                    db=db,
                    year=selected_year,
                ),
                "defect_types_count": len(stats.get("defect_types", [])),
                "product_types_count": len(stats.get("product_types", [])),
            }
        )

    return {
        "comparison": comparison,
        "years_compared": selected_years,
    }


@router.get("/stats/reports")
def get_report_stats(
    year: Optional[int] = Query(
        default=None,
        description="Reporting year. Defaults to current year.",
    ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Return report-focused statistics for the selected year.
    """
    selected_year = _validate_year(year)
    stats = DashboardService.get_dashboard_stats(
        db=db,
        year=selected_year,
    )

    return {
        "year": selected_year,
        "report_statistics": stats.get("report_stats", {}),
        "delay_time": stats.get("delay_time", []),
    }
>>>>>>> Stashed changes
