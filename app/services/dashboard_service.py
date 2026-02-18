# app/services/dashboard_service.py
from datetime import datetime
from typing import Dict, List, Any, Optional
from sqlalchemy import func, case, extract
from sqlalchemy.orm import Session
from app.models.complaint import Complaint
from app.models.report import Report
from app.models.report_step import ReportStep
from app.models.enums import PlantEnum

class DashboardService:
    @staticmethod
    def get_dashboard_stats(db: Session, year: Optional[int] = None) -> Dict[str, Any]:
        """Get all dashboard statistics in one query-optimized call"""
        if year is None:
            year = datetime.now().year
        
        # Filter complaints by year
        year_filter = extract('year', Complaint.created_at) == year
        
        # Total complaints
        total_complaints = db.query(func.count(Complaint.id)).filter(year_filter).scalar() or 0
        
        # Monthly distribution by plant
        monthly_data = DashboardService._get_monthly_by_plant(db, year)
        
        # Total by plant
        total_by_plant = DashboardService._get_total_by_plant(db, year)
        
        # Claims by plant and customer
        claims_by_plant_customer = DashboardService._get_claims_by_plant_customer(db, year)
        
        # Customer vs AvoCarbon sites
        customer_vs_sites = DashboardService._get_customer_vs_sites(db, year)
        
        # Status distribution monthly
        status_monthly = DashboardService._get_status_monthly(db, year)
        
        # Delay time statistics using ReportStep completed_at dates
        delay_time = DashboardService._get_delay_time(db, year)
        
        # Defect types
        defect_types = DashboardService._get_defect_types(db, year)
        
        # Product types
        product_types = DashboardService._get_product_types(db, year)
        
        # Cost distribution (placeholder - can be added later)
        cost_distribution = {
            "costD13": [],
            "costD45": [],
            "costD68": [],
            "costLLC": []
        }
        
        # Top plant
        top_plant = total_by_plant[-1] if total_by_plant else {"plant": "N/A", "count": 0}
        
        # Last update
        last_update = db.query(func.max(Complaint.updated_at)).filter(year_filter).scalar()
        
        # Report completion statistics
        report_stats = DashboardService._get_report_statistics(db, year)
        
        return {
            "total_complaints": total_complaints,
            "top_plant": top_plant,
            "last_update": last_update.isoformat() if last_update else None,
            "monthly_data": monthly_data,
            "total_by_plant": total_by_plant,
            "claims_by_plant_customer": claims_by_plant_customer,
            "customer_vs_sites": customer_vs_sites,
            "status_monthly": status_monthly,
            "delay_time": delay_time,
            "defect_types": defect_types,
            "product_types": product_types,
            "cost_distribution": cost_distribution,
            "report_stats": report_stats
        }
    
    @staticmethod
    def _get_monthly_by_plant(db: Session, year: int) -> List[Dict]:
        """Get monthly complaints grouped by plant"""
        results = db.query(
            extract('month', Complaint.created_at).label('month'),
            Complaint.avocarbon_plant,
            func.count(Complaint.id).label('count')
        ).filter(
            extract('year', Complaint.created_at) == year
        ).group_by(
            extract('month', Complaint.created_at),
            Complaint.avocarbon_plant
        ).all()
        
        # Transform to monthly format
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", 
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        
        plants = [plant.value for plant in PlantEnum]
        
        monthly_data = []
        for month_idx in range(1, 13):
            month_entry = {"month": months[month_idx - 1]}
            total = 0
            
            for plant in plants:
                count = next((r.count for r in results 
                            if r.month == month_idx and r.avocarbon_plant == plant), 0)
                month_entry[plant] = count
                total += count
            
            month_entry["total"] = total
            monthly_data.append(month_entry)
        
        return monthly_data
    
    @staticmethod
    def _get_total_by_plant(db: Session, year: int) -> List[Dict]:
        """Get total complaints by plant, ordered by count"""
        results = db.query(
            Complaint.avocarbon_plant.label('plant'),
            func.count(Complaint.id).label('count')
        ).filter(
            extract('year', Complaint.created_at) == year
        ).group_by(
            Complaint.avocarbon_plant
        ).order_by(
            func.count(Complaint.id).asc()
        ).all()
        
        return [{"plant": r.plant, "count": r.count} for r in results]
    
    @staticmethod
    def _get_claims_by_plant_customer(db: Session, year: int) -> List[Dict]:
        """Get claims by plant with customer breakdown"""
        results = db.query(
            Complaint.avocarbon_plant.label('plant'),
            Complaint.customer,
            func.count(Complaint.id).label('count')
        ).filter(
            extract('year', Complaint.created_at) == year
        ).group_by(
            Complaint.avocarbon_plant,
            Complaint.customer
        ).order_by(
            Complaint.avocarbon_plant,
            func.count(Complaint.id).desc()
        ).all()
        
        # Group by plant and create customer breakdown
        plant_data = {}
        for r in results:
            plant_key = r.plant
            if plant_key not in plant_data:
                plant_data[plant_key] = {
                    "plant": plant_key,
                    "customer1": 0,
                    "customer2": 0,
                    "customer3": 0,
                    "customer4": 0,
                    "customer5": 0
                }
            
            # Distribute counts to customer slots
            for i in range(1, 6):
                customer_key = f"customer{i}"
                if plant_data[plant_key][customer_key] == 0:
                    plant_data[plant_key][customer_key] = r.count
                    break
        
        # Convert to list and sort by total
        result_list = list(plant_data.values())
        result_list.sort(key=lambda x: sum([x[f"customer{i}"] for i in range(1, 6)]))
        
        return result_list
    
    @staticmethod
    def _get_customer_vs_sites(db: Session, year: int) -> List[Dict]:
        """Get customer vs AvoCarbon sites distribution"""
        results = db.query(
            Complaint.customer,
            Complaint.avocarbon_plant,
            func.count(Complaint.id).label('count')
        ).filter(
            extract('year', Complaint.created_at) == year
        ).group_by(
            Complaint.customer,
            Complaint.avocarbon_plant
        ).all()
        
        # Transform to customer-centric view
        customer_data = {}
        plants = [plant.value for plant in PlantEnum]
        
        for r in results:
            customer = r.customer or "OTHERS"
            if customer not in customer_data:
                customer_data[customer] = {p: 0 for p in plants}
                customer_data[customer]["customer"] = customer
            
            if r.avocarbon_plant in plants:
                customer_data[customer][r.avocarbon_plant] = r.count
        
        return list(customer_data.values())
    
    @staticmethod
    def _get_status_monthly(db: Session, year: int) -> List[Dict]:
        """Get complaint status distribution by month"""
        results = db.query(
            extract('month', Complaint.created_at).label('month'),
            Complaint.status,
            func.count(Complaint.id).label('count')
        ).filter(
            extract('year', Complaint.created_at) == year
        ).group_by(
            extract('month', Complaint.created_at),
            Complaint.status
        ).all()
        
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", 
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        
        # Status categories (can map to CSI/CSII equivalents)
        monthly_status = []
        for month_idx in range(1, 13):
            entry = {
                "month": months[month_idx - 1],
                "open": 0,
                "in_progress": 0,
                "under_review": 0,
                "resolved": 0,
                "closed": 0,
                "rejected": 0
            }
            
            for r in results:
                if r.month == month_idx and r.status:
                    status_key = r.status.replace('-', '_')
                    if status_key in entry:
                        entry[status_key] += r.count
            
            monthly_status.append(entry)
        
        return monthly_status
    
    @staticmethod
    def _get_delay_time(db: Session, year: int) -> List[Dict]:
        """Calculate average delay times by plant using ReportStep completion dates"""
        # Get average time from report creation to step completion
        results = db.query(
            Complaint.avocarbon_plant.label('plant'),
            func.avg(
                func.extract('epoch', 
                    func.coalesce(
                        db.query(ReportStep.completed_at)
                        .filter(
                            ReportStep.report_id == Report.id,
                            ReportStep.step_code == 'D3'
                        )
                        .scalar_subquery(),
                        Report.created_at
                    ) - Report.created_at
                )
            ).label('d13_seconds'),
            func.avg(
                func.extract('epoch',
                    func.coalesce(
                        db.query(ReportStep.completed_at)
                        .filter(
                            ReportStep.report_id == Report.id,
                            ReportStep.step_code == 'D5'
                        )
                        .scalar_subquery(),
                        Report.created_at
                    ) - Report.created_at
                )
            ).label('d15_seconds'),
            func.avg(
                func.extract('epoch',
                    func.coalesce(
                        db.query(ReportStep.completed_at)
                        .filter(
                            ReportStep.report_id == Report.id,
                            ReportStep.step_code == 'D8'
                        )
                        .scalar_subquery(),
                        Report.created_at
                    ) - Report.created_at
                )
            ).label('d18_seconds')
        ).join(
            Report, Complaint.id == Report.complaint_id
        ).filter(
            extract('year', Complaint.created_at) == year
        ).group_by(
            Complaint.avocarbon_plant
        ).all()
        
        return [
            {
                "plant": r.plant,
                "d13": int(r.d13_seconds or 0),
                "d15": int(r.d15_seconds or 0),
                "d18": int(r.d18_seconds or 0),
                "llc": 0  # Placeholder
            }
            for r in results
        ]
    
    @staticmethod
    def _get_defect_types(db: Session, year: int) -> List[Dict]:
        """Get defect type distribution from complaints.defects field"""
        results = db.query(
            Complaint.defects,
            func.count(Complaint.id).label('count')
        ).filter(
            extract('year', Complaint.created_at) == year,
            Complaint.defects.isnot(None)
        ).group_by(
            Complaint.defects
        ).order_by(
            func.count(Complaint.id).desc()
        ).all()
        
        return [{"type": r.defects or "NA", "count": r.count} for r in results]
    
    @staticmethod
    def _get_product_types(db: Session, year: int) -> List[Dict]:
        """Get product line distribution"""
        results = db.query(
            Complaint.product_line,
            func.count(Complaint.id).label('count')
        ).filter(
            extract('year', Complaint.created_at) == year,
            Complaint.product_line.isnot(None)
        ).group_by(
            Complaint.product_line
        ).order_by(
            func.count(Complaint.id).desc()
        ).all()
        
        return [
            {
                "type": str(r.product_line.value if hasattr(r.product_line, 'value') else r.product_line), 
                "count": r.count
            } 
            for r in results
        ]
    
    @staticmethod
    def _get_report_statistics(db: Session, year: int) -> Dict[str, Any]:
        """Get 8D report completion statistics"""
        year_filter = extract('year', Complaint.created_at) == year
        
        # Total reports
        total_reports = db.query(func.count(Report.id)).join(
            Complaint, Report.complaint_id == Complaint.id
        ).filter(year_filter).scalar() or 0
        
        # Reports by status
        report_status = db.query(
            Report.status,
            func.count(Report.id).label('count')
        ).join(
            Complaint, Report.complaint_id == Complaint.id
        ).filter(year_filter).group_by(Report.status).all()
        
        # Step completion statistics
        step_completion = db.query(
            ReportStep.step_code,
            func.count(case((ReportStep.status == 'validated', 1))).label('completed'),
            func.count(ReportStep.id).label('total')
        ).join(
            Report, ReportStep.report_id == Report.id
        ).join(
            Complaint, Report.complaint_id == Complaint.id
        ).filter(year_filter).group_by(ReportStep.step_code).all()
        
        return {
            "total_reports": total_reports,
            "by_status": {r.status: r.count for r in report_status},
            "step_completion": [
                {
                    "step": s.step_code,
                    "completed": s.completed,
                    "total": s.total,
                    "completion_rate": round((s.completed / s.total * 100) if s.total > 0 else 0, 1)
                }
                for s in step_completion
            ]
        }