# app/services/dashboard_service.py
from datetime import datetime, date
from typing import Dict, List, Any, Optional
from sqlalchemy import func, case, extract, and_, or_
from sqlalchemy.orm import Session
from app.models.complaint import Complaint
from app.models.report import Report
from app.models.report_step import ReportStep
from app.models.enums import PlantEnum


MONTHLY_TARGETS_2026: Dict[str, int] = {
    "FRANKFURT": 4,
    "SCEET": 2,
    "ASSYMEX": 2,
    "CHENNAI": 1,
    "TIANJIN": 1,
    "DAEGU": 1,
    "ANHUI": 1,
    "Kunshan": 1,
    "SAME": 0,
    "POITIERS": 0,
    "CYCLAM": 0,
}


class DashboardService:

    # ─────────────────────────────────────────────────────────────────────────
    # Main entry point
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def get_dashboard_stats(
        db: Session,
        year: Optional[int] = None,
        month: Optional[int] = None,
        quarter: Optional[int] = None,
    ) -> Dict[str, Any]:
        if year is None:
            year = datetime.now().year

        base_filter = DashboardService._build_filter(year, month, quarter)

        total_complaints = (
            db.query(func.count(Complaint.id)).filter(base_filter).scalar() or 0
        )

        total_by_plant = DashboardService._get_total_by_plant(db, base_filter)
        top_plant = (
            max(total_by_plant, key=lambda x: x["count"])
            if total_by_plant
            else {"plant": "N/A", "count": 0}
        )

        last_update = (
            db.query(func.max(Complaint.updated_at)).filter(base_filter).scalar()
        )

        return {
            # meta
            "total_complaints": total_complaints,
            "top_plant": top_plant,
            "last_update": last_update.isoformat() if last_update else None,
            "selected_year": year,
            "selected_month": month,
            "selected_quarter": quarter,
            "is_current_year": year == datetime.now().year,
            "monthly_targets": MONTHLY_TARGETS_2026,

            # existing charts
            "monthly_data": DashboardService._get_monthly_by_plant(db, year),
            "total_by_plant": total_by_plant,
            "claims_by_plant_customer": DashboardService._get_claims_by_plant_customer(db, base_filter),
            "customer_vs_sites": DashboardService._get_customer_vs_sites(db, base_filter),
            "status_monthly": DashboardService._get_status_monthly(db, year),
            "delay_time": DashboardService._get_delay_time(db, year),
            "defect_types": DashboardService._get_defect_types(db, base_filter),
            "product_types": DashboardService._get_product_types(db, base_filter),
            "cost_distribution": DashboardService._get_cost_distribution(db, base_filter),

            # ── new KPIs ──────────────────────────────────────────────────────
            # 1. Complaints per customer + per plant (heatmap / stacked bar)
            "complaints_by_customer_plant": DashboardService._get_complaints_by_customer_plant(db, base_filter),

            # 2. Complaints per product line + plant
            "complaints_by_product_line_plant": DashboardService._get_complaints_by_product_line_plant(db, base_filter),

            # 3. Valeo complaints per month
            "valeo_monthly": DashboardService._get_valeo_monthly(db, year),

            # 4. Complaints per product line (simple bar)
            "complaints_per_product_line": DashboardService._get_complaints_per_product_line(db, base_filter),

            # 5. Complaints per customer per customer plant
            "complaints_per_customer_plant": DashboardService._get_complaints_per_customer_plant(db, base_filter),

            # 6. Complaints per customer per AvoCarbon plant
            "complaints_per_customer_avocarbon": DashboardService._get_complaints_per_customer_avocarbon(db, base_filter),

            # 7. CS1/CS2 per plant per month
            "cs_type_per_plant_monthly": DashboardService._get_cs_type_per_plant_monthly(db, year),

            # 8. Closed vs Open per plant per month
            "open_closed_per_plant_monthly": DashboardService._get_open_closed_per_plant_monthly(db, year),

            # 9. Quarterly breakdown by plant
            "quarterly_by_plant": DashboardService._get_quarterly_by_plant(db, year),

            # 10. Repetitive complaints distribution (total + per plant)
            "repetitive_distribution": DashboardService._get_repetitive_distribution(db, base_filter),
            "repetitive_by_plant": DashboardService._get_repetitive_by_plant(db, base_filter),

            # 11. Overdue complaints (at complaint level + step level)
            "overdue_complaints": DashboardService._get_overdue_complaints(db, base_filter),
            "overdue_steps": DashboardService._get_overdue_steps(db, year),

            # 15. CQT lateness
            "cqt_lateness": DashboardService._get_cqt_lateness(db, base_filter),

            # 12. Avg complaints per month vs target
            "monthly_vs_target": DashboardService._get_monthly_vs_target(db, year),

            # 13. D1-D8 cost by plant
            "cost_by_step_plant": DashboardService._get_cost_by_step_plant(db, year),

            # 14. Report statistics
            "report_stats": DashboardService._get_report_statistics(db, year),
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Filter helpers
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _build_filter(
        year: int,
        month: Optional[int] = None,
        quarter: Optional[int] = None,
    ):
        filters = [extract("year", Complaint.created_at) == year]

        if month:
            filters.append(extract("month", Complaint.created_at) == month)
        elif quarter:
            q_months = {1: [1,2,3], 2: [4,5,6], 3: [7,8,9], 4: [10,11,12]}
            months = q_months.get(quarter, [])
            if months:
                filters.append(extract("month", Complaint.created_at).in_(months))

        return and_(*filters)

    # ─────────────────────────────────────────────────────────────────────────
    # Existing helpers (kept / updated to accept filter)
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _get_monthly_by_plant(db: Session, year: int) -> List[Dict]:
        results = db.query(
            extract("month", Complaint.created_at).label("month"),
            Complaint.avocarbon_plant,
            func.count(Complaint.id).label("count"),
        ).filter(extract("year", Complaint.created_at) == year).group_by(
            extract("month", Complaint.created_at), Complaint.avocarbon_plant
        ).all()

        months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
        plants = [p.value for p in PlantEnum]

        data = []
        for m in range(1, 13):
            entry = {"month": months[m - 1]}
            total = 0
            for p in plants:
                cnt = next((r.count for r in results if r.month == m and r.avocarbon_plant == p), 0)
                entry[p] = cnt
                total += cnt
            entry["total"] = total
            data.append(entry)
        return data

    @staticmethod
    def _get_total_by_plant(db: Session, base_filter) -> List[Dict]:
        results = db.query(
            Complaint.avocarbon_plant.label("plant"),
            func.count(Complaint.id).label("count"),
        ).filter(base_filter).group_by(Complaint.avocarbon_plant).order_by(
            func.count(Complaint.id).asc()
        ).all()
        return [{"plant": r.plant, "count": r.count} for r in results]

    @staticmethod
    def _get_claims_by_plant_customer(db: Session, base_filter) -> List[Dict]:
        results = db.query(
            Complaint.avocarbon_plant.label("plant"),
            Complaint.customer,
            func.count(Complaint.id).label("count"),
        ).filter(base_filter).group_by(
            Complaint.avocarbon_plant, Complaint.customer
        ).order_by(Complaint.avocarbon_plant, func.count(Complaint.id).desc()).all()

        plant_data: Dict[str, Any] = {}
        for r in results:
            pk = r.plant
            if pk not in plant_data:
                plant_data[pk] = {"plant": pk, **{f"customer{i}": 0 for i in range(1, 6)}}
            for i in range(1, 6):
                key = f"customer{i}"
                if plant_data[pk][key] == 0:
                    plant_data[pk][key] = r.count
                    break

        result_list = list(plant_data.values())
        result_list.sort(key=lambda x: sum(x[f"customer{i}"] for i in range(1, 6)))
        return result_list

    @staticmethod
    def _get_customer_vs_sites(db: Session, base_filter) -> List[Dict]:
        results = db.query(
            Complaint.customer,
            Complaint.avocarbon_plant,
            func.count(Complaint.id).label("count"),
        ).filter(base_filter).group_by(Complaint.customer, Complaint.avocarbon_plant).all()

        plants = [p.value for p in PlantEnum]
        customer_data: Dict[str, Any] = {}
        for r in results:
            c = r.customer or "OTHERS"
            if c not in customer_data:
                customer_data[c] = {p: 0 for p in plants}
                customer_data[c]["customer"] = c
            if r.avocarbon_plant in plants:
                customer_data[c][r.avocarbon_plant] = r.count
        return list(customer_data.values())

    @staticmethod
    def _get_status_monthly(db: Session, year: int) -> List[Dict]:
        results = db.query(
            extract("month", Complaint.created_at).label("month"),
            Complaint.status,
            func.count(Complaint.id).label("count"),
        ).filter(extract("year", Complaint.created_at) == year).group_by(
            extract("month", Complaint.created_at), Complaint.status
        ).all()

        months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
        data = []
        for m in range(1, 13):
            entry = {"month": months[m - 1], "open": 0, "in_progress": 0,
                     "under_review": 0, "resolved": 0, "closed": 0, "rejected": 0}
            for r in results:
                if r.month == m and r.status:
                    k = r.status.replace("-", "_")
                    if k in entry:
                        entry[k] += r.count
            data.append(entry)
        return data

    @staticmethod
    def _get_delay_time(db: Session, year: int) -> List[Dict]:
        """Legacy — kept for backwards compat but not shown in new UI."""
        return []

    @staticmethod
    def _get_defect_types(db: Session, base_filter) -> List[Dict]:
        results = db.query(
            Complaint.defects,
            func.count(Complaint.id).label("count"),
        ).filter(base_filter, Complaint.defects.isnot(None)).group_by(
            Complaint.defects
        ).order_by(func.count(Complaint.id).desc()).all()
        return [{"type": r.defects or "N/A", "count": r.count} for r in results]

    @staticmethod
    def _get_product_types(db: Session, base_filter) -> List[Dict]:
        results = db.query(
            Complaint.avocarbon_product_type,
            func.count(Complaint.id).label("count"),
        ).filter(base_filter, Complaint.avocarbon_product_type.isnot(None)).group_by(
            Complaint.avocarbon_product_type
        ).order_by(func.count(Complaint.id).desc()).all()
        return [{"type": r.avocarbon_product_type or "N/A", "count": r.count} for r in results]

    @staticmethod
    def _get_cost_distribution(db: Session, base_filter) -> Dict:
        """D1-D8 costs aggregated — placeholder until cost data is populated."""
        return {"costD13": [], "costD45": [], "costD68": [], "costLLC": []}

    # ─────────────────────────────────────────────────────────────────────────
    # NEW KPI helpers
    # ─────────────────────────────────────────────────────────────────────────

    # 1. Total complaints per customer + per AvoCarbon plant (matrix)
    @staticmethod
    def _get_complaints_by_customer_plant(db: Session, base_filter) -> List[Dict]:
        results = db.query(
            Complaint.customer,
            Complaint.avocarbon_plant,
            func.count(Complaint.id).label("count"),
        ).filter(base_filter).group_by(
            Complaint.customer, Complaint.avocarbon_plant
        ).order_by(Complaint.customer, func.count(Complaint.id).desc()).all()

        plants = [p.value for p in PlantEnum]
        agg: Dict[str, Any] = {}
        for r in results:
            c = r.customer or "OTHERS"
            if c not in agg:
                agg[c] = {"customer": c, "total": 0, **{p: 0 for p in plants}}
            if r.avocarbon_plant in plants:
                agg[c][r.avocarbon_plant] = r.count
                agg[c]["total"] += r.count

        return sorted(agg.values(), key=lambda x: x["total"], reverse=True)

    # 2. Complaints per product line + plant
    @staticmethod
    def _get_complaints_by_product_line_plant(db: Session, base_filter) -> List[Dict]:
        results = db.query(
            Complaint.product_line,
            Complaint.avocarbon_plant,
            func.count(Complaint.id).label("count"),
        ).filter(base_filter).group_by(
            Complaint.product_line, Complaint.avocarbon_plant
        ).all()

        plants = [p.value for p in PlantEnum]
        agg: Dict[str, Any] = {}
        for r in results:
            pl = str(r.product_line.value if hasattr(r.product_line, "value") else r.product_line or "N/A")
            if pl not in agg:
                agg[pl] = {"product_line": pl, "total": 0, **{p: 0 for p in plants}}
            if r.avocarbon_plant in plants:
                agg[pl][r.avocarbon_plant] = r.count
                agg[pl]["total"] += r.count
        return sorted(agg.values(), key=lambda x: x["total"], reverse=True)

    # 3. Valeo complaints per month
    @staticmethod
    def _get_valeo_monthly(db: Session, year: int) -> List[Dict]:
        months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
        results = db.query(
            extract("month", Complaint.created_at).label("month"),
            func.count(Complaint.id).label("count"),
        ).filter(
            extract("year", Complaint.created_at) == year,
            func.upper(Complaint.customer).like("%VALEO%"),
        ).group_by(extract("month", Complaint.created_at)).all()

        month_map = {int(r.month): r.count for r in results}
        return [{"month": months[m - 1], "count": month_map.get(m, 0)} for m in range(1, 13)]

    # 4. Complaints per product line (simple)
    @staticmethod
    def _get_complaints_per_product_line(db: Session, base_filter) -> List[Dict]:
        results = db.query(
            Complaint.product_line,
            func.count(Complaint.id).label("count"),
        ).filter(base_filter).group_by(Complaint.product_line).order_by(
            func.count(Complaint.id).desc()
        ).all()
        return [
            {
                "type": str(r.product_line.value if hasattr(r.product_line, "value") else r.product_line or "N/A"),
                "count": r.count,
            }
            for r in results
        ]

    # 5. Complaints per customer per customer plant
    @staticmethod
    def _get_complaints_per_customer_plant(db: Session, base_filter) -> List[Dict]:
        results = db.query(
            Complaint.customer,
            Complaint.customer_plant_name,
            func.count(Complaint.id).label("count"),
        ).filter(base_filter).group_by(
            Complaint.customer, Complaint.customer_plant_name
        ).order_by(func.count(Complaint.id).desc()).limit(40).all()
        return [
            {"customer": r.customer or "N/A", "customer_plant": r.customer_plant_name or "N/A", "count": r.count}
            for r in results
        ]

    # 6. Complaints per customer per AvoCarbon plant
    @staticmethod
    def _get_complaints_per_customer_avocarbon(db: Session, base_filter) -> List[Dict]:
        results = db.query(
            Complaint.customer,
            Complaint.avocarbon_plant,
            func.count(Complaint.id).label("count"),
        ).filter(base_filter).group_by(
            Complaint.customer, Complaint.avocarbon_plant
        ).order_by(Complaint.customer, func.count(Complaint.id).desc()).all()
        return [
            {"customer": r.customer or "N/A", "avocarbon_plant": r.avocarbon_plant or "N/A", "count": r.count}
            for r in results
        ]

    # 7. CS1/CS2 per plant per month
    @staticmethod
    def _get_cs_type_per_plant_monthly(db: Session, year: int) -> List[Dict]:
        """
        quality_issue_warranty field stores CS1 / CS2 classification.
        We aggregate count of CS1 vs CS2 per plant per month.
        """
        months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
        plants = [p.value for p in PlantEnum]

        results = db.query(
            extract("month", Complaint.created_at).label("month"),
            Complaint.avocarbon_plant,
            Complaint.quality_issue_warranty,
            func.count(Complaint.id).label("count"),
        ).filter(extract("year", Complaint.created_at) == year).group_by(
            extract("month", Complaint.created_at),
            Complaint.avocarbon_plant,
            Complaint.quality_issue_warranty,
        ).all()

        # Build {month -> {plant -> {CS1: n, CS2: n}}}
        data: Dict[int, Dict[str, Dict[str, int]]] = {
            m: {p: {"CS1": 0, "CS2": 0} for p in plants} for m in range(1, 13)
        }
        for r in results:
            m = int(r.month)
            plant = r.avocarbon_plant
            cs = (r.quality_issue_warranty or "").upper()
            if plant in plants:
                if "CS1" in cs:
                    data[m][plant]["CS1"] += r.count
                elif "CS2" in cs:
                    data[m][plant]["CS2"] += r.count

        # Flatten: one row per month-plant
        rows = []
        for m in range(1, 13):
            for plant in plants:
                rows.append({
                    "month": months[m - 1],
                    "plant": plant,
                    "CS1": data[m][plant]["CS1"],
                    "CS2": data[m][plant]["CS2"],
                })
        return rows

    # 8. Closed vs Open per plant per month
    @staticmethod
    def _get_open_closed_per_plant_monthly(db: Session, year: int) -> List[Dict]:
        months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
        plants = [p.value for p in PlantEnum]

        results = db.query(
            extract("month", Complaint.created_at).label("month"),
            Complaint.avocarbon_plant,
            Complaint.status,
            func.count(Complaint.id).label("count"),
        ).filter(extract("year", Complaint.created_at) == year).group_by(
            extract("month", Complaint.created_at),
            Complaint.avocarbon_plant,
            Complaint.status,
        ).all()

        OPEN_STATUSES = {"open", "in_progress", "under_review"}
        CLOSED_STATUSES = {"resolved", "closed", "rejected"}

        rows = []
        for m in range(1, 13):
            for plant in plants:
                open_cnt = sum(
                    r.count for r in results
                    if int(r.month) == m and r.avocarbon_plant == plant and r.status in OPEN_STATUSES
                )
                closed_cnt = sum(
                    r.count for r in results
                    if int(r.month) == m and r.avocarbon_plant == plant and r.status in CLOSED_STATUSES
                )
                if open_cnt or closed_cnt:
                    rows.append({
                        "month": months[m - 1],
                        "plant": plant,
                        "open": open_cnt,
                        "closed": closed_cnt,
                    })
        return rows

    # 9. Quarterly breakdown by plant
    @staticmethod
    def _get_quarterly_by_plant(db: Session, year: int) -> List[Dict]:
        plants = [p.value for p in PlantEnum]
        Q_MONTHS = {"Q1": [1,2,3], "Q2": [4,5,6], "Q3": [7,8,9], "Q4": [10,11,12]}

        results = db.query(
            extract("month", Complaint.created_at).label("month"),
            Complaint.avocarbon_plant,
            func.count(Complaint.id).label("count"),
        ).filter(extract("year", Complaint.created_at) == year).group_by(
            extract("month", Complaint.created_at), Complaint.avocarbon_plant
        ).all()

        rows = []
        for q_label, q_months in Q_MONTHS.items():
            entry: Dict[str, Any] = {"quarter": q_label, "total": 0}
            for plant in plants:
                cnt = sum(
                    r.count for r in results
                    if int(r.month) in q_months and r.avocarbon_plant == plant
                )
                entry[plant] = cnt
                entry["total"] += cnt
            rows.append(entry)
        return rows

    # 10. Repetitive complaints distribution
    @staticmethod
    def _get_repetitive_distribution(db: Session, base_filter) -> List[Dict]:
        """
        repetitive_complete_with_number stores a numeric repetition index.
        We bucket: 0 = non-repetitive, 1 = first repetition, 2+ = recurrent.
        """
        results = db.query(
            Complaint.repetitive_complete_with_number,
            func.count(Complaint.id).label("count"),
        ).filter(base_filter).group_by(
            Complaint.repetitive_complete_with_number
        ).all()

        buckets: Dict[str, int] = {"0 (First occurrence)": 0, "1": 0, "2": 0, "3+": 0}
        for r in results:
            raw = r.repetitive_complete_with_number
            try:
                val = int(float(str(raw).strip())) if raw else 0
            except (ValueError, TypeError):
                val = 0
            if val == 0:
                buckets["0 (First occurrence)"] += r.count
            elif val == 1:
                buckets["1"] += r.count
            elif val == 2:
                buckets["2"] += r.count
            else:
                buckets["3+"] += r.count

        return [{"label": k, "count": v} for k, v in buckets.items()]

    # 11a. Overdue complaints
    @staticmethod
    def _get_overdue_complaints(db: Session, base_filter) -> Dict[str, Any]:
        now = datetime.utcnow()
        overdue_q = db.query(
            Complaint.avocarbon_plant,
            func.count(Complaint.id).label("count"),
        ).filter(
            base_filter,
            Complaint.due_date < now,
            Complaint.status.notin_(["closed", "resolved", "rejected"]),
        ).group_by(Complaint.avocarbon_plant).all()

        total_overdue = sum(r.count for r in overdue_q)
        by_plant = [{"plant": r.avocarbon_plant, "count": r.count} for r in overdue_q]

        return {"total": total_overdue, "by_plant": by_plant}

    # 11b. Overdue report steps
    @staticmethod
    def _get_overdue_steps(db: Session, year: int) -> List[Dict]:
        now = datetime.utcnow()
        results = db.query(
            ReportStep.step_code,
            Complaint.avocarbon_plant,
            func.count(ReportStep.id).label("count"),
        ).join(Report, ReportStep.report_id == Report.id).join(
            Complaint, Report.complaint_id == Complaint.id
        ).filter(
            extract("year", Complaint.created_at) == year,
            ReportStep.is_overdue == True,
            ReportStep.status != "fulfilled",
        ).group_by(ReportStep.step_code, Complaint.avocarbon_plant).all()

        return [
            {"step": r.step_code, "plant": r.avocarbon_plant, "count": r.count}
            for r in results
        ]

    # 12. Monthly complaints vs target per plant
    @staticmethod
    def _get_monthly_vs_target(db: Session, year: int) -> List[Dict]:
        plants = [p.value for p in PlantEnum]
        months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

        results = db.query(
            extract("month", Complaint.created_at).label("month"),
            Complaint.avocarbon_plant,
            func.count(Complaint.id).label("count"),
        ).filter(extract("year", Complaint.created_at) == year).group_by(
            extract("month", Complaint.created_at), Complaint.avocarbon_plant
        ).all()

        rows = []
        for m in range(1, 13):
            for plant in plants:
                actual = next(
                    (r.count for r in results if int(r.month) == m and r.avocarbon_plant == plant), 0
                )
                target = MONTHLY_TARGETS_2026.get(plant, 0)
                rows.append({
                    "month": months[m - 1],
                    "plant": plant,
                    "actual": actual,
                    "target": target,
                    "delta": actual - target,
                    "on_target": actual <= target,
                })
        return rows

    # 13. D1-D8 cost by plant
    @staticmethod
    def _get_cost_by_step_plant(db: Session, year: int) -> List[Dict]:
        results = db.query(
            Complaint.avocarbon_plant,
            ReportStep.step_code,
            func.sum(ReportStep.cost).label("total_cost"),
            func.count(ReportStep.id).label("step_count"),
        ).join(Report, ReportStep.report_id == Report.id).join(
            Complaint, Report.complaint_id == Complaint.id
        ).filter(
            extract("year", Complaint.created_at) == year,
            ReportStep.cost.isnot(None),
        ).group_by(Complaint.avocarbon_plant, ReportStep.step_code).all()

        # Pivot: {plant -> {D1: cost, D2: cost, ...}}
        plants = [p.value for p in PlantEnum]
        steps = [f"D{i}" for i in range(1, 9)]
        agg: Dict[str, Any] = {
            p: {"plant": p, **{s: 0 for s in steps}, "total": 0} for p in plants
        }
        for r in results:
            plant = r.avocarbon_plant
            step = r.step_code
            cost = int(r.total_cost or 0)
            if plant in agg and step in steps:
                agg[plant][step] = cost
                agg[plant]["total"] += cost

        return [v for v in agg.values() if v["total"] > 0]

    # ─────────────────────────────────────────────────────────────────────────
    # Report statistics (unchanged)
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _get_report_statistics(db: Session, year: int) -> Dict[str, Any]:
        year_filter = extract("year", Complaint.created_at) == year

        total_reports = (
            db.query(func.count(Report.id))
            .join(Complaint, Report.complaint_id == Complaint.id)
            .filter(year_filter)
            .scalar() or 0
        )

        report_status = (
            db.query(Report.status, func.count(Report.id).label("count"))
            .join(Complaint, Report.complaint_id == Complaint.id)
            .filter(year_filter)
            .group_by(Report.status)
            .all()
        )

        step_completion = (
            db.query(
                ReportStep.step_code,
                func.count(case((ReportStep.status == "validated", 1))).label("completed"),
                func.count(ReportStep.id).label("total"),
            )
            .join(Report, ReportStep.report_id == Report.id)
            .join(Complaint, Report.complaint_id == Complaint.id)
            .filter(year_filter)
            .group_by(ReportStep.step_code)
            .all()
        )

        return {
            "total_reports": total_reports,
            "by_status": {r.status: r.count for r in report_status},
            "step_completion": [
                {
                    "step": s.step_code,
                    "completed": s.completed,
                    "total": s.total,
                    "completion_rate": round((s.completed / s.total * 100) if s.total > 0 else 0, 1),
                }
                for s in step_completion
            ],
        }

    # ── NEW: Repetitive by plant ───────────────────────────────────────────────
    @staticmethod
    def _get_repetitive_by_plant(db: Session, base_filter) -> List[Dict]:
        """
        Returns [{ plant, repetition_number (bucketed label), count }]
        so the frontend can draw a stacked bar: X = repetition bucket, stacks = plants.
        """
        results = db.query(
            Complaint.avocarbon_plant,
            Complaint.repetitive_complete_with_number,
            func.count(Complaint.id).label("count"),
        ).filter(base_filter).group_by(
            Complaint.avocarbon_plant,
            Complaint.repetitive_complete_with_number,
        ).all()

        BUCKETS = ["0 – First", "1", "2", "3+"]
        out: List[Dict] = []
        for r in results:
            raw = r.repetitive_complete_with_number
            try:
                val = int(float(str(raw).strip())) if raw else 0
            except (ValueError, TypeError):
                val = 0
            if val == 0:
                bucket = "0 – First"
            elif val == 1:
                bucket = "1"
            elif val == 2:
                bucket = "2"
            else:
                bucket = "3+"
            out.append({
                "plant": r.avocarbon_plant or "UNKNOWN",
                "repetition_number": bucket,
                "count": r.count,
            })
        return out

    # ── NEW: CQT lateness ─────────────────────────────────────────────────────
    @staticmethod
    def _get_cqt_lateness(db: Session, base_filter) -> Dict[str, Any]:
        """
        Measures lateness attributed to CQT engineers:
        - A complaint is "late" if any of its report steps is overdue
          (is_overdue=True or due_date < now with completed_at IS NULL).
        - Groups by cqt_email and by plant.
        Returns:
          {
            total_late: int,                  # total complaints with at least one overdue step
            by_cqt: [{ cqt_email, late_complaints, total_steps_overdue }],
            by_plant: [{ plant, late_complaints }],
            step_overdue_summary: [{ step_code, overdue_count }],
          }
        """
        now = datetime.utcnow()
        overdue_condition = or_(
            ReportStep.is_overdue == True,
            and_(ReportStep.due_date < now, ReportStep.completed_at.is_(None)),
        )

        # Complaints that have at least one overdue step
        late_complaints_sq = (
            db.query(Complaint.id)
            .join(Report, Report.complaint_id == Complaint.id)
            .join(ReportStep, ReportStep.report_id == Report.id)
            .filter(base_filter, overdue_condition)
            .distinct()
            .subquery()
        )

        # Total late complaints
        total_late = db.query(func.count()).select_from(late_complaints_sq).scalar() or 0

        # By CQT email
        by_cqt_q = (
            db.query(
                Complaint.cqt_email,
                func.count(func.distinct(Complaint.id)).label("late_complaints"),
                func.count(ReportStep.id).label("total_steps_overdue"),
            )
            .join(Report, Report.complaint_id == Complaint.id)
            .join(ReportStep, ReportStep.report_id == Report.id)
            .filter(base_filter, overdue_condition)
            .group_by(Complaint.cqt_email)
            .order_by(func.count(func.distinct(Complaint.id)).desc())
            .all()
        )

        # By plant
        by_plant_q = (
            db.query(
                Complaint.avocarbon_plant,
                func.count(func.distinct(Complaint.id)).label("late_complaints"),
            )
            .join(Report, Report.complaint_id == Complaint.id)
            .join(ReportStep, ReportStep.report_id == Report.id)
            .filter(base_filter, overdue_condition)
            .group_by(Complaint.avocarbon_plant)
            .order_by(func.count(func.distinct(Complaint.id)).desc())
            .all()
        )

        # Step overdue summary
        step_q = (
            db.query(
                ReportStep.step_code,
                func.count(ReportStep.id).label("overdue_count"),
            )
            .join(Report, ReportStep.report_id == Report.id)
            .join(Complaint, Report.complaint_id == Complaint.id)
            .filter(base_filter, overdue_condition)
            .group_by(ReportStep.step_code)
            .order_by(ReportStep.step_code)
            .all()
        )

        return {
            "total_late": total_late,
            "by_cqt": [
                {
                    "cqt_email": r.cqt_email or "Unassigned",
                    "late_complaints": r.late_complaints,
                    "total_steps_overdue": r.total_steps_overdue,
                }
                for r in by_cqt_q
            ],
            "by_plant": [
                {"plant": r.avocarbon_plant or "UNKNOWN", "late_complaints": r.late_complaints}
                for r in by_plant_q
            ],
            "step_overdue_summary": [
                {"step_code": r.step_code, "overdue_count": r.overdue_count}
                for r in step_q
            ],
        }