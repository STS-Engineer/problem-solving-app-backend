# app/api/routes/dashboard.py
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import datetime

from app.api.deps import get_db
from app.services.dashboard_service import DashboardService

router = APIRouter()

@router.get("/available-years")
def get_available_years(
    db: Session = Depends(get_db)
):
    """
    Get list of years that have complaint data
    
    Returns:
    - List of years with at least one complaint
    - Useful for year selector dropdown
    """
    from sqlalchemy import func, extract, distinct
    from app.models.complaint import Complaint
    
    years = db.query(
        distinct(extract('year', Complaint.created_at)).label('year')
    ).order_by(
        extract('year', Complaint.created_at).desc()
    ).all()
    
    year_list = [int(y.year) for y in years if y.year]
    
    return {
        "years": year_list,
        "current_year": datetime.now().year,
        "default_year": year_list[0] if year_list else datetime.now().year
    }

@router.get("/stats")
def get_dashboard_stats(
    year: Optional[int] = Query(None, description="Year to filter (default: current year)"),
    db: Session = Depends(get_db)
):
    """
    Get all dashboard statistics
    
    Returns comprehensive dashboard data including:
    - Total complaints
    - Monthly distribution by plant
    - Plant statistics
    - Customer breakdowns
    - Status distribution
    - Delay times (using ReportStep completion)
    - Defect types
    - Product types
    - Report completion statistics
    """
    if year is None:
        year = datetime.now().year
    
    # Validate year range
    current_year = datetime.now().year
    if year < 2020 or year > current_year + 1:
        raise HTTPException(
            status_code=400, 
            detail=f"Year must be between 2020 and {current_year + 1}"
        )
    
    stats = DashboardService.get_dashboard_stats(db, year)
    
    # Add year info to response
    stats["selected_year"] = year
    stats["is_current_year"] = year == current_year
    
    return stats

@router.get("/stats/realtime")
def get_realtime_stats(
    year: Optional[int] = Query(None, description="Year to filter (default: current year)"),
    db: Session = Depends(get_db)
):
    """
    Get lightweight real-time stats for polling
    
    Returns only frequently changing metrics:
    - Total complaints count
    - Last update timestamp
    - Recent complaints (last 5)
    - Open complaints count
    """
    if year is None:
        year = datetime.now().year
    
    from sqlalchemy import func, extract
    from app.models.complaint import Complaint
    
    year_filter = extract('year', Complaint.created_at) == year
    
    total = db.query(func.count(Complaint.id)).filter(year_filter).scalar() or 0
    
    open_count = db.query(func.count(Complaint.id)).filter(
        year_filter,
        Complaint.status.in_(['open', 'in_progress', 'under_review'])
    ).scalar() or 0
    
    last_update = db.query(func.max(Complaint.updated_at)).filter(year_filter).scalar()
    
    recent_complaints = db.query(Complaint).filter(
        year_filter
    ).order_by(Complaint.created_at.desc()).limit(5).all()
    
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
                "created_at": c.created_at.isoformat()
            }
            for c in recent_complaints
        ],
        "year": year
    }

@router.get("/stats/comparison")
def get_year_comparison(
    years: List[int] = Query(None, description="Years to compare (e.g., ?years=2024&years=2025)"),
    db: Session = Depends(get_db)
):
    """
    Compare statistics across multiple years
    
    Useful for year-over-year analysis
    """
    if not years:
        # Default: compare last 3 years
        current_year = datetime.now().year
        years = [current_year - 2, current_year - 1, current_year]
    
    comparison = []
    for year in years:
        stats = DashboardService.get_dashboard_stats(db, year)
        comparison.append({
            "year": year,
            "total_complaints": stats["total_complaints"],
            "top_plant": stats["top_plant"],
            "open_complaints": sum([
                v for k, v in stats.get("report_stats", {}).get("by_status", {}).items()
                if k in ['draft', 'in_progress', 'submitted', 'under_review']
            ]),
            "defect_types_count": len(stats["defect_types"]),
            "product_types_count": len(stats["product_types"])
        })
    
    return {
        "comparison": comparison,
        "years_compared": years
    }

@router.get("/stats/reports")
def get_report_stats(
    year: Optional[int] = Query(None, description="Year to filter (default: current year)"),
    db: Session = Depends(get_db)
):
    """
    Get detailed 8D report statistics
    
    Returns:
    - Report completion rates
    - Step-by-step progress
    - Average completion times
    """
    if year is None:
        year = datetime.now().year
    
    stats = DashboardService.get_dashboard_stats(db, year)
    
    return {
        "year": year,
        "report_statistics": stats.get("report_stats", {}),
        "delay_time": stats.get("delay_time", [])
    }