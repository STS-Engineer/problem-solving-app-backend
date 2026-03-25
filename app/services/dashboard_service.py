# app/services/dashboard_service.py
from datetime import datetime, date
from typing import Dict, List, Any, Optional
from sqlalchemy import func, case, extract, and_, or_
from sqlalchemy.orm import Session
from app.models.complaint import Complaint
from app.models.report import Report
from app.models.report_step import ReportStep
from app.models.enums import PlantEnum


# ─── SLA definitions ──────────────────────────────────────────────────────────
# Each D-step SLA = days from complaint creation (complaint_opening_date)
D_STEP_SLA_DAYS: Dict[str, int] = {
    "D1": 1,
    "D2": 2,
    "D3": 3,
    "D4": 5,
    "D5": 10,
    "D6": 30,
    "D7": 30,
    "D8": 30,
}

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

OPEN_STATUSES = {"open", "in_progress", "under_review"}
CLOSED_STATUSES = {"resolved", "closed", "rejected"}


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
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        if year is None:
            year = datetime.now().year

        base_filter = DashboardService._build_filter(
            year, month, quarter, start_date, end_date
        )

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
            # NOTE: monthly charts use complaint_opening_date (operational date),
            # not created_at (system insert timestamp)
            "monthly_data": DashboardService._get_monthly_by_plant(
                db, year, start_date, end_date
            ),
            "total_by_plant": total_by_plant,
            "claims_by_plant_customer": DashboardService._get_claims_by_plant_customer(
                db, base_filter
            ),
            "customer_vs_sites": DashboardService._get_customer_vs_sites(
                db, base_filter
            ),
            "status_monthly": DashboardService._get_status_monthly(
                db, year, start_date, end_date
            ),
            "delay_time": [],  # deprecated stub
            "defect_types": DashboardService._get_defect_types(db, base_filter),
            "product_types": DashboardService._get_product_types(db, base_filter),
            "cost_distribution": DashboardService._get_cost_distribution(
                db, base_filter
            ),
            # volume KPIs
            "complaints_by_customer_plant": DashboardService._get_complaints_by_customer_plant(
                db, base_filter
            ),
            "complaints_by_product_line_plant": DashboardService._get_complaints_by_product_line_plant(
                db, base_filter
            ),
            "valeo_monthly": DashboardService._get_valeo_monthly(
                db, year, start_date, end_date
            ),
            "complaints_per_product_line": DashboardService._get_complaints_per_product_line(
                db, base_filter
            ),
            "complaints_per_customer_plant": DashboardService._get_complaints_per_customer_plant(
                db, base_filter
            ),
            "complaints_per_customer_avocarbon": DashboardService._get_complaints_per_customer_avocarbon(
                db, base_filter
            ),
            "cs_type_per_plant_monthly": DashboardService._get_cs_type_per_plant_monthly(
                db, year, start_date, end_date
            ),
            "open_closed_per_plant_monthly": DashboardService._get_open_closed_per_plant_monthly(
                db, year, start_date, end_date
            ),
            "quarterly_by_plant": DashboardService._get_quarterly_by_plant(db, year),
            "repetitive_distribution": DashboardService._get_repetitive_distribution(
                db, base_filter
            ),
            "repetitive_by_plant": DashboardService._get_repetitive_by_plant(
                db, base_filter
            ),
            "overdue_complaints": DashboardService._get_overdue_complaints(
                db, base_filter
            ),
            "overdue_steps": DashboardService._get_overdue_steps(
                db, year, start_date, end_date
            ),
            "cqt_lateness": DashboardService._get_cqt_lateness(db, base_filter),
            "monthly_vs_target": DashboardService._get_monthly_vs_target(
                db, year, start_date, end_date
            ),
            "cost_by_step_plant": DashboardService._get_cost_by_step_plant(
                db, year, start_date, end_date
            ),
            "report_stats": DashboardService._get_report_statistics(
                db, year, start_date, end_date
            ),
            # ── NEW KPIs ──────────────────────────────────────────────────────
            # 1. Acknowledgement delay (customer_complaint_date → complaint_opening_date)
            "acknowledgement_delay": DashboardService._get_acknowledgement_delay(
                db, base_filter
            ),
            # 2. Resolution cycle time (complaint_opening_date → closed_at)
            "resolution_cycle_time": DashboardService._get_resolution_cycle_time(
                db, base_filter
            ),
            # 3. CS2 SLA compliance (dedicated for CS2 warranty complaints)
            "cs2_sla_compliance": DashboardService._get_cs2_sla_compliance(
                db, base_filter
            ),
            # 4. Complaint ageing buckets (open complaints by age)
            "complaint_ageing": DashboardService._get_complaint_ageing(db, base_filter),
            # 5. 8D step SLA compliance per step per plant
            "step_sla_compliance": DashboardService._get_step_sla_compliance(
                db, base_filter
            ),
            # 6. Recurrence rate % per plant per month
            "recurrence_rate": DashboardService._get_recurrence_rate(
                db, year, start_date, end_date
            ),
            # 7. Process / application Pareto per plant
            "process_pareto": DashboardService._get_process_pareto(db, base_filter),
            "application_pareto": DashboardService._get_application_pareto(
                db, base_filter
            ),
            # 8. Escalation rate (L3/L4 plant manager involvement)
            "escalation_rate": DashboardService._get_escalation_rate(db, base_filter),
            # 9. Complaints with no due date assigned (risk indicator)
            "no_due_date_count": DashboardService._get_no_due_date_count(
                db, base_filter
            ),
            # 10. Rejection rate per customer / plant
            "rejection_rate": DashboardService._get_rejection_rate(db, base_filter),
            # 11. Resolved-to-closed lag (awaiting customer sign-off)
            "resolved_to_closed_lag": DashboardService._get_resolved_to_closed_lag(
                db, base_filter
            ),
            # 12. Priority distribution (the priority field was never surfaced)
            "priority_distribution": DashboardService._get_priority_distribution(
                db, base_filter
            ),
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Filter helpers
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _build_filter(
        year: int,
        month: Optional[int] = None,
        quarter: Optional[int] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ):
        """
        Filter on complaint_opening_date (operational treatment date), NOT created_at.
        If start_date/end_date are provided they take precedence over year/month/quarter.
        """
        if start_date and end_date:
            return and_(
                Complaint.complaint_opening_date >= start_date,
                Complaint.complaint_opening_date <= end_date,
            )

        filters = [extract("year", Complaint.complaint_opening_date) == year]

        if month:
            filters.append(extract("month", Complaint.complaint_opening_date) == month)
        elif quarter:
            q_months = {1: [1, 2, 3], 2: [4, 5, 6], 3: [7, 8, 9], 4: [10, 11, 12]}
            months = q_months.get(quarter, [])
            if months:
                filters.append(
                    extract("month", Complaint.complaint_opening_date).in_(months)
                )

        return and_(*filters)

    @staticmethod
    def _year_filter(year: int, start_date: Optional[date], end_date: Optional[date]):
        """Helper: full-year filter respecting date range override."""
        if start_date and end_date:
            return and_(
                Complaint.complaint_opening_date >= start_date,
                Complaint.complaint_opening_date <= end_date,
            )
        return extract("year", Complaint.complaint_opening_date) == year

    # ─────────────────────────────────────────────────────────────────────────
    # Existing helpers — updated to use complaint_opening_date
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _get_monthly_by_plant(
        db: Session,
        year: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[Dict]:
        yf = DashboardService._year_filter(year, start_date, end_date)
        results = (
            db.query(
                extract("month", Complaint.complaint_opening_date).label("month"),
                Complaint.avocarbon_plant,
                func.count(Complaint.id).label("count"),
            )
            .filter(yf)
            .group_by(
                extract("month", Complaint.complaint_opening_date),
                Complaint.avocarbon_plant,
            )
            .all()
        )

        months = [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ]
        plants = [p.value for p in PlantEnum]

        data = []
        for m in range(1, 13):
            entry = {"month": months[m - 1]}
            total = 0
            for p in plants:
                cnt = next(
                    (
                        r.count
                        for r in results
                        if r.month == m and r.avocarbon_plant == p
                    ),
                    0,
                )
                entry[p] = cnt
                total += cnt
            entry["total"] = total
            data.append(entry)
        return data

    @staticmethod
    def _get_total_by_plant(db: Session, base_filter) -> List[Dict]:
        results = (
            db.query(
                Complaint.avocarbon_plant.label("plant"),
                func.count(Complaint.id).label("count"),
            )
            .filter(base_filter)
            .group_by(Complaint.avocarbon_plant)
            .order_by(func.count(Complaint.id).asc())
            .all()
        )
        return [
            {"plant": r.plant.value if r.plant is not None else None, "count": r.count}
            for r in results
        ]

    @staticmethod
    def _get_claims_by_plant_customer(db: Session, base_filter) -> List[Dict]:
        results = (
            db.query(
                Complaint.avocarbon_plant.label("plant"),
                Complaint.customer,
                func.count(Complaint.id).label("count"),
            )
            .filter(base_filter)
            .group_by(Complaint.avocarbon_plant, Complaint.customer)
            .order_by(Complaint.avocarbon_plant, func.count(Complaint.id).desc())
            .all()
        )

        plant_data: Dict[str, Any] = {}
        for r in results:
            pk = r.plant
            if pk not in plant_data:
                plant_data[pk] = {
                    "plant": pk,
                    **{f"customer{i}": 0 for i in range(1, 6)},
                }
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
        results = (
            db.query(
                Complaint.customer,
                Complaint.avocarbon_plant,
                func.count(Complaint.id).label("count"),
            )
            .filter(base_filter)
            .group_by(Complaint.customer, Complaint.avocarbon_plant)
            .all()
        )

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
    def _get_status_monthly(
        db: Session,
        year: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[Dict]:
        yf = DashboardService._year_filter(year, start_date, end_date)
        results = (
            db.query(
                extract("month", Complaint.complaint_opening_date).label("month"),
                Complaint.status,
                func.count(Complaint.id).label("count"),
            )
            .filter(yf)
            .group_by(
                extract("month", Complaint.complaint_opening_date), Complaint.status
            )
            .all()
        )

        months = [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ]
        data = []
        for m in range(1, 13):
            entry = {
                "month": months[m - 1],
                "open": 0,
                "in_progress": 0,
                "under_review": 0,
                "resolved": 0,
                "closed": 0,
                "rejected": 0,
            }
            for r in results:
                if r.month == m and r.status:
                    k = r.status.replace("-", "_")
                    if k in entry:
                        entry[k] += r.count
            data.append(entry)
        return data

    @staticmethod
    def _get_defect_types(db: Session, base_filter) -> List[Dict]:
        results = (
            db.query(
                Complaint.defects,
                func.count(Complaint.id).label("count"),
            )
            .filter(base_filter, Complaint.defects.isnot(None))
            .group_by(Complaint.defects)
            .order_by(func.count(Complaint.id).desc())
            .all()
        )
        return [{"type": r.defects or "N/A", "count": r.count} for r in results]

    @staticmethod
    def _get_product_types(db: Session, base_filter) -> List[Dict]:
        results = (
            db.query(
                Complaint.avocarbon_product_type,
                func.count(Complaint.id).label("count"),
            )
            .filter(base_filter, Complaint.avocarbon_product_type.isnot(None))
            .group_by(Complaint.avocarbon_product_type)
            .order_by(func.count(Complaint.id).desc())
            .all()
        )
        return [
            {"type": r.avocarbon_product_type or "N/A", "count": r.count}
            for r in results
        ]

    @staticmethod
    def _get_cost_distribution(db: Session, base_filter) -> Dict:
        return {"costD13": [], "costD45": [], "costD68": [], "costLLC": []}

    # ─────────────────────────────────────────────────────────────────────────
    # Existing volume KPIs — complaint_opening_date corrected
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _get_complaints_by_customer_plant(db: Session, base_filter) -> List[Dict]:
        results = (
            db.query(
                Complaint.customer,
                Complaint.avocarbon_plant,
                func.count(Complaint.id).label("count"),
            )
            .filter(base_filter)
            .group_by(Complaint.customer, Complaint.avocarbon_plant)
            .order_by(Complaint.customer, func.count(Complaint.id).desc())
            .all()
        )

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

    @staticmethod
    def _get_complaints_by_product_line_plant(db: Session, base_filter) -> List[Dict]:
        results = (
            db.query(
                Complaint.product_line,
                Complaint.avocarbon_plant,
                func.count(Complaint.id).label("count"),
            )
            .filter(base_filter)
            .group_by(Complaint.product_line, Complaint.avocarbon_plant)
            .all()
        )

        plants = [p.value for p in PlantEnum]
        agg: Dict[str, Any] = {}
        for r in results:
            pl = str(
                r.product_line.value
                if hasattr(r.product_line, "value")
                else r.product_line or "N/A"
            )
            if pl not in agg:
                agg[pl] = {"product_line": pl, "total": 0, **{p: 0 for p in plants}}
            if r.avocarbon_plant in plants:
                agg[pl][r.avocarbon_plant] = r.count
                agg[pl]["total"] += r.count
        return sorted(agg.values(), key=lambda x: x["total"], reverse=True)

    @staticmethod
    def _get_valeo_monthly(
        db: Session,
        year: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[Dict]:
        months = [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ]
        yf = DashboardService._year_filter(year, start_date, end_date)
        results = (
            db.query(
                extract("month", Complaint.complaint_opening_date).label("month"),
                func.count(Complaint.id).label("count"),
            )
            .filter(
                yf,
                func.upper(Complaint.customer).like("%VALEO%"),
            )
            .group_by(extract("month", Complaint.complaint_opening_date))
            .all()
        )

        month_map = {int(r.month): r.count for r in results}
        return [
            {"month": months[m - 1], "count": month_map.get(m, 0)} for m in range(1, 13)
        ]

    @staticmethod
    def _get_complaints_per_product_line(db: Session, base_filter) -> List[Dict]:
        results = (
            db.query(
                Complaint.product_line,
                func.count(Complaint.id).label("count"),
            )
            .filter(base_filter)
            .group_by(Complaint.product_line)
            .order_by(func.count(Complaint.id).desc())
            .all()
        )
        return [
            {
                "type": str(
                    r.product_line.value
                    if hasattr(r.product_line, "value")
                    else r.product_line or "N/A"
                ),
                "count": r.count,
            }
            for r in results
        ]

    @staticmethod
    def _get_complaints_per_customer_plant(db: Session, base_filter) -> List[Dict]:
        results = (
            db.query(
                Complaint.customer,
                Complaint.customer_plant_name,
                func.count(Complaint.id).label("count"),
            )
            .filter(base_filter)
            .group_by(Complaint.customer, Complaint.customer_plant_name)
            .order_by(func.count(Complaint.id).desc())
            .limit(40)
            .all()
        )
        return [
            {
                "customer": r.customer or "N/A",
                "customer_plant": r.customer_plant_name or "N/A",
                "count": r.count,
            }
            for r in results
        ]

    @staticmethod
    def _get_complaints_per_customer_avocarbon(db: Session, base_filter) -> List[Dict]:
        results = (
            db.query(
                Complaint.customer,
                Complaint.avocarbon_plant,
                func.count(Complaint.id).label("count"),
            )
            .filter(base_filter)
            .group_by(Complaint.customer, Complaint.avocarbon_plant)
            .order_by(Complaint.customer, func.count(Complaint.id).desc())
            .all()
        )
        return [
            {
                "customer": r.customer or "N/A",
                "avocarbon_plant": r.avocarbon_plant or "N/A",
                "count": r.count,
            }
            for r in results
        ]

    @staticmethod
    def _get_cs_type_per_plant_monthly(
        db: Session,
        year: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[Dict]:
        months = [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ]
        plants = [p.value for p in PlantEnum]
        yf = DashboardService._year_filter(year, start_date, end_date)

        results = (
            db.query(
                extract("month", Complaint.complaint_opening_date).label("month"),
                Complaint.avocarbon_plant,
                Complaint.quality_issue_warranty,
                func.count(Complaint.id).label("count"),
            )
            .filter(yf)
            .group_by(
                extract("month", Complaint.complaint_opening_date),
                Complaint.avocarbon_plant,
                Complaint.quality_issue_warranty,
            )
            .all()
        )

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

        rows = []
        for m in range(1, 13):
            for plant in plants:
                rows.append(
                    {
                        "month": months[m - 1],
                        "plant": plant,
                        "CS1": data[m][plant]["CS1"],
                        "CS2": data[m][plant]["CS2"],
                    }
                )
        return rows

    @staticmethod
    def _get_open_closed_per_plant_monthly(
        db: Session,
        year: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[Dict]:
        months = [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ]
        plants = [p.value for p in PlantEnum]
        yf = DashboardService._year_filter(year, start_date, end_date)

        results = (
            db.query(
                extract("month", Complaint.complaint_opening_date).label("month"),
                Complaint.avocarbon_plant,
                Complaint.status,
                func.count(Complaint.id).label("count"),
            )
            .filter(yf)
            .group_by(
                extract("month", Complaint.complaint_opening_date),
                Complaint.avocarbon_plant,
                Complaint.status,
            )
            .all()
        )

        rows = []
        for m in range(1, 13):
            for plant in plants:
                open_cnt = sum(
                    r.count
                    for r in results
                    if int(r.month) == m
                    and r.avocarbon_plant == plant
                    and r.status in OPEN_STATUSES
                )
                closed_cnt = sum(
                    r.count
                    for r in results
                    if int(r.month) == m
                    and r.avocarbon_plant == plant
                    and r.status in CLOSED_STATUSES
                )
                if open_cnt or closed_cnt:
                    rows.append(
                        {
                            "month": months[m - 1],
                            "plant": plant,
                            "open": open_cnt,
                            "closed": closed_cnt,
                        }
                    )
        return rows

    @staticmethod
    def _get_quarterly_by_plant(db: Session, year: int) -> List[Dict]:
        plants = [p.value for p in PlantEnum]
        Q_MONTHS = {
            "Q1": [1, 2, 3],
            "Q2": [4, 5, 6],
            "Q3": [7, 8, 9],
            "Q4": [10, 11, 12],
        }

        results = (
            db.query(
                extract("month", Complaint.complaint_opening_date).label("month"),
                Complaint.avocarbon_plant,
                func.count(Complaint.id).label("count"),
            )
            .filter(extract("year", Complaint.complaint_opening_date) == year)
            .group_by(
                extract("month", Complaint.complaint_opening_date),
                Complaint.avocarbon_plant,
            )
            .all()
        )

        rows = []
        for q_label, q_months in Q_MONTHS.items():
            entry: Dict[str, Any] = {"quarter": q_label, "total": 0}
            for plant in plants:
                cnt = sum(
                    r.count
                    for r in results
                    if int(r.month) in q_months and r.avocarbon_plant == plant
                )
                entry[plant] = cnt
                entry["total"] += cnt
            rows.append(entry)
        return rows

    @staticmethod
    def _get_repetitive_distribution(db: Session, base_filter) -> List[Dict]:
        results = (
            db.query(
                Complaint.repetitive_complete_with_number,
                func.count(Complaint.id).label("count"),
            )
            .filter(base_filter)
            .group_by(Complaint.repetitive_complete_with_number)
            .all()
        )

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

    @staticmethod
    def _get_repetitive_by_plant(db: Session, base_filter) -> List[Dict]:
        results = (
            db.query(
                Complaint.avocarbon_plant,
                Complaint.repetitive_complete_with_number,
                func.count(Complaint.id).label("count"),
            )
            .filter(base_filter)
            .group_by(
                Complaint.avocarbon_plant,
                Complaint.repetitive_complete_with_number,
            )
            .all()
        )

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
            out.append(
                {
                    "plant": r.avocarbon_plant or "UNKNOWN",
                    "repetition_number": bucket,
                    "count": r.count,
                }
            )
        return out

    @staticmethod
    def _get_overdue_complaints(db: Session, base_filter) -> Dict[str, Any]:
        now = datetime.utcnow()
        overdue_q = (
            db.query(
                Complaint.avocarbon_plant,
                func.count(Complaint.id).label("count"),
            )
            .filter(
                base_filter,
                Complaint.due_date < now,
                Complaint.status.notin_(["closed", "resolved", "rejected"]),
                Complaint.due_date.isnot(None),
            )
            .group_by(Complaint.avocarbon_plant)
            .all()
        )

        total_overdue = sum(r.count for r in overdue_q)
        by_plant = [
            {
                "plant": (
                    r.avocarbon_plant.value
                    if r.avocarbon_plant is not None
                    else "UNKNOWN"
                ),
                "count": r.count,
            }
            for r in overdue_q
        ]

        # Also count complaints with no due date (risk indicator)
        no_due = (
            db.query(func.count(Complaint.id))
            .filter(
                base_filter,
                Complaint.due_date.is_(None),
                Complaint.status.notin_(["closed", "resolved", "rejected"]),
            )
            .scalar()
            or 0
        )

        return {"total": total_overdue, "by_plant": by_plant, "no_due_date": no_due}

    @staticmethod
    def _get_overdue_steps(
        db: Session,
        year: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[Dict]:
        now = datetime.utcnow()
        yf = DashboardService._year_filter(year, start_date, end_date)
        results = (
            db.query(
                ReportStep.step_code,
                Complaint.avocarbon_plant,
                func.count(ReportStep.id).label("count"),
            )
            .join(Report, ReportStep.report_id == Report.id)
            .join(Complaint, Report.complaint_id == Complaint.id)
            .filter(
                yf,
                ReportStep.is_overdue == True,
                ReportStep.status != "fulfilled",
            )
            .group_by(ReportStep.step_code, Complaint.avocarbon_plant)
            .all()
        )

        return [
            {
                "step": r.step_code,
                "plant": (
                    r.avocarbon_plant.value
                    if r.avocarbon_plant is not None
                    else "UNKNOWN"
                ),
                "count": r.count,
            }
            for r in results
        ]

    @staticmethod
    def _get_monthly_vs_target(
        db: Session,
        year: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[Dict]:
        plants = [p.value for p in PlantEnum]
        months = [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ]
        yf = DashboardService._year_filter(year, start_date, end_date)

        results = (
            db.query(
                extract("month", Complaint.complaint_opening_date).label("month"),
                Complaint.avocarbon_plant,
                func.count(Complaint.id).label("count"),
            )
            .filter(yf)
            .group_by(
                extract("month", Complaint.complaint_opening_date),
                Complaint.avocarbon_plant,
            )
            .all()
        )

        rows = []
        for m in range(1, 13):
            for plant in plants:
                actual = next(
                    (
                        r.count
                        for r in results
                        if int(r.month) == m and r.avocarbon_plant == plant
                    ),
                    0,
                )
                target = MONTHLY_TARGETS_2026.get(plant, 0)
                rows.append(
                    {
                        "month": months[m - 1],
                        "plant": plant,
                        "actual": actual,
                        "target": target,
                        "delta": actual - target,
                        # on_target: plants with target=0 should flag ANY complaint
                        "on_target": actual <= target if target > 0 else actual == 0,
                        "zero_target_breach": target == 0 and actual > 0,
                    }
                )
        return rows

    @staticmethod
    def _get_cost_by_step_plant(
        db: Session,
        year: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[Dict]:
        yf = DashboardService._year_filter(year, start_date, end_date)
        results = (
            db.query(
                Complaint.avocarbon_plant,
                ReportStep.step_code,
                func.sum(ReportStep.cost).label("total_cost"),
                func.count(ReportStep.id).label("step_count"),
            )
            .join(Report, ReportStep.report_id == Report.id)
            .join(Complaint, Report.complaint_id == Complaint.id)
            .filter(yf, ReportStep.cost.isnot(None))
            .group_by(Complaint.avocarbon_plant, ReportStep.step_code)
            .all()
        )

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

    @staticmethod
    def _get_report_statistics(
        db: Session,
        year: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        yf = DashboardService._year_filter(year, start_date, end_date)

        total_reports = (
            db.query(func.count(Report.id))
            .join(Complaint, Report.complaint_id == Complaint.id)
            .filter(yf)
            .scalar()
            or 0
        )

        report_status = (
            db.query(Report.status, func.count(Report.id).label("count"))
            .join(Complaint, Report.complaint_id == Complaint.id)
            .filter(yf)
            .group_by(Report.status)
            .all()
        )

        step_completion = (
            db.query(
                ReportStep.step_code,
                func.count(case((ReportStep.status == "validated", 1))).label(
                    "completed"
                ),
                func.count(ReportStep.id).label("total"),
            )
            .join(Report, ReportStep.report_id == Report.id)
            .join(Complaint, Report.complaint_id == Complaint.id)
            .filter(yf)
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
                    "completion_rate": round(
                        (s.completed / s.total * 100) if s.total > 0 else 0, 1
                    ),
                }
                for s in step_completion
            ],
        }

    @staticmethod
    def _get_cqt_lateness(db: Session, base_filter) -> Dict[str, Any]:
        now = datetime.utcnow()
        overdue_condition = or_(
            ReportStep.is_overdue == True,
            and_(ReportStep.due_date < now, ReportStep.completed_at.is_(None)),
        )

        late_complaints_sq = (
            db.query(Complaint.id)
            .join(Report, Report.complaint_id == Complaint.id)
            .join(ReportStep, ReportStep.report_id == Report.id)
            .filter(base_filter, overdue_condition)
            .distinct()
            .subquery()
        )

        total_late = (
            db.query(func.count()).select_from(late_complaints_sq).scalar() or 0
        )

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
                {
                    "plant": (
                        r.avocarbon_plant.value
                        if r.avocarbon_plant is not None
                        else "UNKNOWN"
                    ),
                    "late_complaints": r.late_complaints,
                }
                for r in by_plant_q
            ],
            "step_overdue_summary": [
                {"step_code": r.step_code, "overdue_count": r.overdue_count}
                for r in step_q
            ],
        }

    # ─────────────────────────────────────────────────────────────────────────
    # NEW KPI — 1. Acknowledgement delay
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _get_acknowledgement_delay(db: Session, base_filter) -> Dict[str, Any]:
        """
        Measures the gap between customer_complaint_date and complaint_opening_date.
        This is the primary SLA metric: how fast does AVOCarbon acknowledge a claim?
        Target: ≤1 day for CS2, ≤2 days for CS1.

        Returns:
          {
            avg_days_overall: float,
            avg_days_cs1: float,
            avg_days_cs2: float,
            pct_within_1_day: float,    # % acknowledged within 1 day
            pct_within_2_days: float,   # % acknowledged within 2 days
            by_plant: [{ plant, avg_days, count, pct_on_time }],
            by_month: [{ month, avg_days_cs1, avg_days_cs2 }],
            distribution: [{ bucket_label, count }],  # 0-1d, 1-2d, 2-5d, 5-10d, 10d+
          }
        """
        results = (
            db.query(
                Complaint.avocarbon_plant,
                Complaint.quality_issue_warranty,
                Complaint.customer_complaint_date,
                Complaint.complaint_opening_date,
                extract("month", Complaint.complaint_opening_date).label("month"),
            )
            .filter(
                base_filter,
                Complaint.customer_complaint_date.isnot(None),
                Complaint.complaint_opening_date.isnot(None),
            )
            .all()
        )

        if not results:
            return {
                "avg_days_overall": None,
                "avg_days_cs1": None,
                "avg_days_cs2": None,
                "pct_within_1_day": None,
                "pct_within_2_days": None,
                "by_plant": [],
                "by_month": [],
                "distribution": [],
            }

        def calc_delay(r) -> float:
            delta = (r.complaint_opening_date - r.customer_complaint_date).days
            return max(delta, 0)  # negative = data entry error, treat as 0

        delays = [(r, calc_delay(r)) for r in results]
        total = len(delays)

        avg_overall = round(sum(d for _, d in delays) / total, 2)

        cs1_delays = [
            d for r, d in delays if "CS1" in (r.quality_issue_warranty or "").upper()
        ]
        cs2_delays = [
            d for r, d in delays if "CS2" in (r.quality_issue_warranty or "").upper()
        ]

        avg_cs1 = round(sum(cs1_delays) / len(cs1_delays), 2) if cs1_delays else None
        avg_cs2 = round(sum(cs2_delays) / len(cs2_delays), 2) if cs2_delays else None

        pct_1d = round(sum(1 for _, d in delays if d <= 1) / total * 100, 1)
        pct_2d = round(sum(1 for _, d in delays if d <= 2) / total * 100, 1)

        # By plant
        plant_agg: Dict[str, List[float]] = {}
        for r, d in delays:
            p = r.avocarbon_plant or "UNKNOWN"
            plant_agg.setdefault(p, []).append(d)

        by_plant = [
            {
                "plant": p,
                "avg_days": round(sum(vals) / len(vals), 2),
                "count": len(vals),
                "pct_on_time": round(
                    sum(1 for v in vals if v <= 2) / len(vals) * 100, 1
                ),
            }
            for p, vals in plant_agg.items()
        ]
        by_plant.sort(key=lambda x: x["avg_days"], reverse=True)

        # By month (CS1 vs CS2 avg delay)
        months = [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ]
        month_agg: Dict[int, Dict[str, List[float]]] = {
            m: {"CS1": [], "CS2": []} for m in range(1, 13)
        }
        for r, d in delays:
            m = int(r.month)
            cs = (r.quality_issue_warranty or "").upper()
            if "CS1" in cs:
                month_agg[m]["CS1"].append(d)
            elif "CS2" in cs:
                month_agg[m]["CS2"].append(d)

        by_month = [
            {
                "month": months[m - 1],
                "avg_days_cs1": (
                    round(sum(month_agg[m]["CS1"]) / len(month_agg[m]["CS1"]), 2)
                    if month_agg[m]["CS1"]
                    else None
                ),
                "avg_days_cs2": (
                    round(sum(month_agg[m]["CS2"]) / len(month_agg[m]["CS2"]), 2)
                    if month_agg[m]["CS2"]
                    else None
                ),
            }
            for m in range(1, 13)
        ]

        # Distribution buckets
        BUCKETS = [
            ("Same day", lambda d: d == 0),
            ("1 day", lambda d: d == 1),
            ("2–3 days", lambda d: 2 <= d <= 3),
            ("4–7 days", lambda d: 4 <= d <= 7),
            ("8–14 days", lambda d: 8 <= d <= 14),
            ("15+ days", lambda d: d >= 15),
        ]
        distribution = [
            {"label": label, "count": sum(1 for _, d in delays if fn(d))}
            for label, fn in BUCKETS
        ]

        return {
            "avg_days_overall": avg_overall,
            "avg_days_cs1": avg_cs1,
            "avg_days_cs2": avg_cs2,
            "pct_within_1_day": pct_1d,
            "pct_within_2_days": pct_2d,
            "by_plant": by_plant,
            "by_month": by_month,
            "distribution": distribution,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # NEW KPI — 2. Resolution cycle time
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _get_resolution_cycle_time(db: Session, base_filter) -> Dict[str, Any]:
        """
        Measures complaint_opening_date → closed_at duration in days.
        Only counts complaints that are already closed.
        Segmented by CS1/CS2 and by plant.

        Returns:
          {
            avg_days_overall: float | None,
            avg_days_cs1: float | None,
            avg_days_cs2: float | None,
            median_days: float | None,
            p90_days: float | None,
            by_plant: [{ plant, avg_days, count }],
            by_month: [{ month, avg_days }],
          }
        """
        results = (
            db.query(
                Complaint.avocarbon_plant,
                Complaint.quality_issue_warranty,
                Complaint.complaint_opening_date,
                Complaint.closed_at,
                extract("month", Complaint.complaint_opening_date).label("month"),
            )
            .filter(
                base_filter,
                Complaint.closed_at.isnot(None),
                Complaint.complaint_opening_date.isnot(None),
                Complaint.status.in_(["closed", "resolved"]),
            )
            .all()
        )

        if not results:
            return {
                "avg_days_overall": None,
                "avg_days_cs1": None,
                "avg_days_cs2": None,
                "median_days": None,
                "p90_days": None,
                "by_plant": [],
                "by_month": [],
            }

        def calc_days(r) -> float:
            closed = r.closed_at.date() if hasattr(r.closed_at, "date") else r.closed_at
            return max((closed - r.complaint_opening_date).days, 0)

        all_days = [(r, calc_days(r)) for r in results]
        sorted_days = sorted(d for _, d in all_days)
        total = len(sorted_days)

        avg_overall = round(sum(sorted_days) / total, 1)
        median = round(sorted_days[total // 2], 1)
        p90 = round(sorted_days[int(total * 0.9)], 1)

        cs1 = [
            d for r, d in all_days if "CS1" in (r.quality_issue_warranty or "").upper()
        ]
        cs2 = [
            d for r, d in all_days if "CS2" in (r.quality_issue_warranty or "").upper()
        ]
        avg_cs1 = round(sum(cs1) / len(cs1), 1) if cs1 else None
        avg_cs2 = round(sum(cs2) / len(cs2), 1) if cs2 else None

        plant_agg: Dict[str, List[float]] = {}
        for r, d in all_days:
            p = r.avocarbon_plant or "UNKNOWN"
            plant_agg.setdefault(p, []).append(d)

        by_plant = [
            {"plant": p, "avg_days": round(sum(v) / len(v), 1), "count": len(v)}
            for p, v in plant_agg.items()
        ]
        by_plant.sort(key=lambda x: x["avg_days"], reverse=True)

        months = [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ]
        month_agg: Dict[int, List[float]] = {m: [] for m in range(1, 13)}
        for r, d in all_days:
            month_agg[int(r.month)].append(d)

        by_month = [
            {
                "month": months[m - 1],
                "avg_days": (
                    round(sum(month_agg[m]) / len(month_agg[m]), 1)
                    if month_agg[m]
                    else None
                ),
            }
            for m in range(1, 13)
        ]

        return {
            "avg_days_overall": avg_overall,
            "avg_days_cs1": avg_cs1,
            "avg_days_cs2": avg_cs2,
            "median_days": median,
            "p90_days": p90,
            "by_plant": by_plant,
            "by_month": by_month,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # NEW KPI — 3. CS2 SLA compliance
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _get_cs2_sla_compliance(db: Session, base_filter) -> Dict[str, Any]:
        """
        CS2 (warranty) complaints have tighter requirements.
        SLA targets: acknowledged ≤1 day, closed ≤30 days.

        Returns:
          {
            total_cs2: int,
            pct_acknowledged_on_time: float,   # ≤1 day to open
            pct_closed_on_time: float,          # ≤30 days to close
            open_past_sla: int,                 # CS2 still open > 30 days
            by_plant: [{ plant, total, on_time_ack, on_time_close }],
          }
        """
        now = datetime.utcnow().date()

        results = (
            db.query(
                Complaint.avocarbon_plant,
                Complaint.customer_complaint_date,
                Complaint.complaint_opening_date,
                Complaint.closed_at,
                Complaint.status,
            )
            .filter(
                base_filter,
                func.upper(Complaint.quality_issue_warranty).like("%CS2%"),
            )
            .all()
        )

        if not results:
            return {
                "total_cs2": 0,
                "pct_acknowledged_on_time": None,
                "pct_closed_on_time": None,
                "open_past_sla": 0,
                "by_plant": [],
            }

        total = len(results)
        ack_on_time = 0
        closed_on_time = 0
        open_past_30 = 0

        plant_agg: Dict[str, Dict[str, int]] = {}

        for r in results:
            p = r.avocarbon_plant or "UNKNOWN"
            plant_agg.setdefault(p, {"total": 0, "ack_ok": 0, "close_ok": 0})
            plant_agg[p]["total"] += 1

            # Acknowledgement: ≤1 day
            if r.customer_complaint_date and r.complaint_opening_date:
                delay = (r.complaint_opening_date - r.customer_complaint_date).days
                if delay <= 1:
                    ack_on_time += 1
                    plant_agg[p]["ack_ok"] += 1

            # Closure: ≤30 days
            if r.closed_at and r.complaint_opening_date:
                closed_d = (
                    r.closed_at.date() if hasattr(r.closed_at, "date") else r.closed_at
                )
                cycle = (closed_d - r.complaint_opening_date).days
                if cycle <= 30:
                    closed_on_time += 1
                    plant_agg[p]["close_ok"] += 1
            elif (
                r.status not in ("closed", "resolved", "rejected")
                and r.complaint_opening_date
            ):
                age = (now - r.complaint_opening_date).days
                if age > 30:
                    open_past_30 += 1

        by_plant = [
            {
                "plant": p,
                "total": v["total"],
                "pct_ack_on_time": round(v["ack_ok"] / v["total"] * 100, 1),
                "pct_close_on_time": round(v["close_ok"] / v["total"] * 100, 1),
            }
            for p, v in plant_agg.items()
        ]
        by_plant.sort(key=lambda x: x["total"], reverse=True)

        return {
            "total_cs2": total,
            "pct_acknowledged_on_time": round(ack_on_time / total * 100, 1),
            "pct_closed_on_time": round(closed_on_time / total * 100, 1),
            "open_past_sla": open_past_30,
            "by_plant": by_plant,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # NEW KPI — 4. Complaint ageing buckets
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _get_complaint_ageing(db: Session, base_filter) -> Dict[str, Any]:
        """
        Age buckets for currently open complaints.
        Age = today - complaint_opening_date.

        Returns:
          {
            buckets: [{ label, count }],   # 0-7d, 7-30d, 30-60d, 60d+
            by_plant: [{ plant, "0-7d": n, "7-30d": n, "30-60d": n, "60d+": n }],
            oldest_open: { complaint_ref, plant, days_open, priority } | None,
          }
        """
        now = datetime.utcnow().date()

        results = (
            db.query(
                Complaint.reference_number,
                Complaint.avocarbon_plant,
                Complaint.complaint_opening_date,
                Complaint.priority,
                Complaint.quality_issue_warranty,
            )
            .filter(
                base_filter,
                Complaint.status.in_(list(OPEN_STATUSES)),
                Complaint.complaint_opening_date.isnot(None),
            )
            .all()
        )

        if not results:
            return {"buckets": [], "by_plant": [], "oldest_open": None}

        BUCKET_DEFS = [
            ("0–7 days", lambda d: d <= 7),
            ("8–30 days", lambda d: 8 <= d <= 30),
            ("31–60 days", lambda d: 31 <= d <= 60),
            ("61+ days", lambda d: d > 60),
        ]

        buckets = {label: 0 for label, _ in BUCKET_DEFS}
        plant_agg: Dict[str, Dict[str, int]] = {}
        max_age = -1
        oldest = None

        for r in results:
            age = (now - r.complaint_opening_date).days
            p = r.avocarbon_plant or "UNKNOWN"
            plant_agg.setdefault(p, {label: 0 for label, _ in BUCKET_DEFS})

            for label, fn in BUCKET_DEFS:
                if fn(age):
                    buckets[label] += 1
                    plant_agg[p][label] += 1
                    break

            if age > max_age:
                max_age = age
                oldest = {
                    "reference_number": r.reference_number,
                    "plant": p,
                    "days_open": age,
                    "priority": r.priority,
                    "cs_type": (r.quality_issue_warranty or "").upper(),
                }

        return {
            "buckets": [{"label": k, "count": v} for k, v in buckets.items()],
            "by_plant": [{"plant": p, **counts} for p, counts in plant_agg.items()],
            "oldest_open": oldest,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # NEW KPI — 5. 8D step SLA compliance per step per plant
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _get_step_sla_compliance(db: Session, base_filter) -> List[Dict]:
        """
        For each D-step, across all complaints in the filter window:
          - total instances of this step
          - completed on time (completed_at ≤ due_date)
          - overdue (is_overdue or completed late)
          - still open

        Returns: [{ step, sla_days, total, on_time, overdue, open, compliance_pct }]
        """
        results = (
            db.query(
                ReportStep.step_code,
                Complaint.avocarbon_plant,
                ReportStep.status,
                ReportStep.is_overdue,
                ReportStep.due_date,
                ReportStep.completed_at,
            )
            .join(Report, ReportStep.report_id == Report.id)
            .join(Complaint, Report.complaint_id == Complaint.id)
            .filter(base_filter)
            .all()
        )

        # Aggregate per step
        step_agg: Dict[str, Dict[str, int]] = {}
        for step_code in D_STEP_SLA_DAYS:
            step_agg[step_code] = {"total": 0, "on_time": 0, "overdue": 0, "open": 0}

        for r in results:
            sc = r.step_code
            if sc not in step_agg:
                continue
            step_agg[sc]["total"] += 1

            if r.status in ("fulfilled", "validated"):
                # Completed — was it on time?
                if r.completed_at and r.due_date:
                    if r.completed_at <= r.due_date:
                        step_agg[sc]["on_time"] += 1
                    else:
                        step_agg[sc]["overdue"] += 1
                else:
                    step_agg[sc]["on_time"] += 1  # no due date set, count as ok
            elif r.is_overdue:
                step_agg[sc]["overdue"] += 1
            else:
                step_agg[sc]["open"] += 1

        return [
            {
                "step": sc,
                "sla_days": D_STEP_SLA_DAYS[sc],
                "total": v["total"],
                "on_time": v["on_time"],
                "overdue": v["overdue"],
                "open": v["open"],
                "compliance_pct": (
                    round(v["on_time"] / v["total"] * 100, 1)
                    if v["total"] > 0
                    else None
                ),
            }
            for sc, v in step_agg.items()
        ]

    # ─────────────────────────────────────────────────────────────────────────
    # NEW KPI — 6. Recurrence rate % per plant per month
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _get_recurrence_rate(
        db: Session,
        year: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[Dict]:
        """
        Per plant per month: total complaints vs repetitive complaints
        (repetition_number ≥ 1).
        Returns [{ month, plant, total, repetitive, recurrence_pct }]
        A rising recurrence_pct means corrective actions are failing.
        """
        months = [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ]
        plants = [p.value for p in PlantEnum]
        yf = DashboardService._year_filter(year, start_date, end_date)

        results = (
            db.query(
                extract("month", Complaint.complaint_opening_date).label("month"),
                Complaint.avocarbon_plant,
                Complaint.repetitive_complete_with_number,
                func.count(Complaint.id).label("count"),
            )
            .filter(yf)
            .group_by(
                extract("month", Complaint.complaint_opening_date),
                Complaint.avocarbon_plant,
                Complaint.repetitive_complete_with_number,
            )
            .all()
        )

        rows = []
        for m in range(1, 13):
            for plant in plants:
                total = 0
                repetitive = 0
                for r in results:
                    if int(r.month) != m or r.avocarbon_plant != plant:
                        continue
                    total += r.count
                    try:
                        val = (
                            int(float(str(r.repetitive_complete_with_number).strip()))
                            if r.repetitive_complete_with_number
                            else 0
                        )
                    except (ValueError, TypeError):
                        val = 0
                    if val >= 1:
                        repetitive += r.count

                if total > 0:
                    rows.append(
                        {
                            "month": months[m - 1],
                            "plant": plant,
                            "total": total,
                            "repetitive": repetitive,
                            "recurrence_pct": round(repetitive / total * 100, 1),
                        }
                    )
        return rows

    # ─────────────────────────────────────────────────────────────────────────
    # NEW KPI — 7. Process & application Pareto per plant
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _get_process_pareto(db: Session, base_filter) -> List[Dict]:
        """
        Top processes (process_linked_to_problem) generating complaints.
        Grouped by plant for the stacked Pareto.
        Returns [{ process, plant, count }] sorted by count desc.
        """
        results = (
            db.query(
                Complaint.potential_avocarbon_process_linked_to_problem.label(
                    "process"
                ),
                Complaint.avocarbon_plant,
                func.count(Complaint.id).label("count"),
            )
            .filter(
                base_filter,
                Complaint.potential_avocarbon_process_linked_to_problem.isnot(None),
                Complaint.potential_avocarbon_process_linked_to_problem != "",
            )
            .group_by(
                Complaint.potential_avocarbon_process_linked_to_problem,
                Complaint.avocarbon_plant,
            )
            .order_by(func.count(Complaint.id).desc())
            .limit(60)
            .all()
        )

        # Pivot: [{process, PLANT_A: n, PLANT_B: n, ..., total: n}]
        plants = [p.value for p in PlantEnum]
        agg: Dict[str, Any] = {}
        for r in results:
            proc = r.process or "Unknown"
            if proc not in agg:
                agg[proc] = {"process": proc, "total": 0, **{p: 0 for p in plants}}
            if r.avocarbon_plant in plants:
                agg[proc][r.avocarbon_plant] += r.count
                agg[proc]["total"] += r.count

        return sorted(agg.values(), key=lambda x: x["total"], reverse=True)[:20]

    @staticmethod
    def _get_application_pareto(db: Session, base_filter) -> List[Dict]:
        """
        Top concerned_application values generating complaints.
        """
        results = (
            db.query(
                Complaint.concerned_application.label("application"),
                Complaint.avocarbon_plant,
                func.count(Complaint.id).label("count"),
            )
            .filter(
                base_filter,
                Complaint.concerned_application.isnot(None),
                Complaint.concerned_application != "",
            )
            .group_by(
                Complaint.concerned_application,
                Complaint.avocarbon_plant,
            )
            .order_by(func.count(Complaint.id).desc())
            .limit(60)
            .all()
        )

        plants = [p.value for p in PlantEnum]
        agg: Dict[str, Any] = {}
        for r in results:
            app = r.application or "Unknown"
            if app not in agg:
                agg[app] = {"application": app, "total": 0, **{p: 0 for p in plants}}
            if r.avocarbon_plant in plants:
                agg[app][r.avocarbon_plant] += r.count
                agg[app]["total"] += r.count

        return sorted(agg.values(), key=lambda x: x["total"], reverse=True)[:20]

    # ─────────────────────────────────────────────────────────────────────────
    # NEW KPI — 8. Escalation rate
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _get_escalation_rate(db: Session, base_filter) -> Dict[str, Any]:
        """
        plant_manager_email is populated only for escalated (L3/L4) complaints.
        Tracks escalation rate per plant and per month.

        Returns:
          {
            total_escalated: int,
            escalation_rate_pct: float,
            by_plant: [{ plant, total, escalated, escalation_pct }],
            by_month: [{ month, total, escalated }],
          }
        """
        total_q = db.query(func.count(Complaint.id)).filter(base_filter).scalar() or 0
        escalated_q = (
            db.query(func.count(Complaint.id))
            .filter(base_filter, Complaint.plant_manager_email.isnot(None))
            .scalar()
            or 0
        )

        # By plant
        total_by_plant = (
            db.query(
                Complaint.avocarbon_plant,
                func.count(Complaint.id).label("total"),
            )
            .filter(base_filter)
            .group_by(Complaint.avocarbon_plant)
            .all()
        )

        escalated_by_plant = (
            db.query(
                Complaint.avocarbon_plant,
                func.count(Complaint.id).label("count"),
            )
            .filter(base_filter, Complaint.plant_manager_email.isnot(None))
            .group_by(Complaint.avocarbon_plant)
            .all()
        )

        esc_map = {r.avocarbon_plant: r.count for r in escalated_by_plant}
        by_plant = [
            {
                "plant": r.avocarbon_plant or "UNKNOWN",
                "total": r.total,
                "escalated": esc_map.get(r.avocarbon_plant, 0),
                "escalation_pct": (
                    round(esc_map.get(r.avocarbon_plant, 0) / r.total * 100, 1)
                    if r.total > 0
                    else 0
                ),
            }
            for r in total_by_plant
        ]
        by_plant.sort(key=lambda x: x["escalation_pct"], reverse=True)

        return {
            "total_escalated": escalated_q,
            "total_complaints": total_q,
            "escalation_rate_pct": (
                round(escalated_q / total_q * 100, 1) if total_q > 0 else 0
            ),
            "by_plant": by_plant,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # NEW KPI — 9. Complaints with no due date
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _get_no_due_date_count(db: Session, base_filter) -> Dict[str, Any]:
        total = (
            db.query(func.count(Complaint.id))
            .filter(
                base_filter,
                Complaint.due_date.is_(None),
                Complaint.status.notin_(["closed", "resolved", "rejected"]),
            )
            .scalar()
            or 0
        )

        by_plant = (
            db.query(
                Complaint.avocarbon_plant,
                func.count(Complaint.id).label("count"),
            )
            .filter(
                base_filter,
                Complaint.due_date.is_(None),
                Complaint.status.notin_(["closed", "resolved", "rejected"]),
            )
            .group_by(Complaint.avocarbon_plant)
            .all()
        )

        return {
            "total": total,
            "by_plant": [
                {"plant": r.avocarbon_plant or "UNKNOWN", "count": r.count}
                for r in by_plant
            ],
        }

    # ─────────────────────────────────────────────────────────────────────────
    # NEW KPI — 10. Rejection rate per customer / plant
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _get_rejection_rate(db: Session, base_filter) -> Dict[str, Any]:
        """
        % of complaints rejected per customer and per plant.
        High rejection rate = quality perception mismatch or commercial tension.
        """
        total_by_customer = (
            db.query(
                Complaint.customer,
                func.count(Complaint.id).label("total"),
            )
            .filter(base_filter)
            .group_by(Complaint.customer)
            .all()
        )

        rejected_by_customer = (
            db.query(
                Complaint.customer,
                func.count(Complaint.id).label("count"),
            )
            .filter(base_filter, Complaint.status == "rejected")
            .group_by(Complaint.customer)
            .all()
        )

        rej_map = {r.customer: r.count for r in rejected_by_customer}
        by_customer = [
            {
                "customer": r.customer or "N/A",
                "total": r.total,
                "rejected": rej_map.get(r.customer, 0),
                "rejection_pct": (
                    round(rej_map.get(r.customer, 0) / r.total * 100, 1)
                    if r.total > 0
                    else 0
                ),
            }
            for r in total_by_customer
            if rej_map.get(r.customer, 0) > 0
        ]
        by_customer.sort(key=lambda x: x["rejection_pct"], reverse=True)

        total_rejected = sum(r.count for r in rejected_by_customer)
        total_all = db.query(func.count(Complaint.id)).filter(base_filter).scalar() or 0

        return {
            "total_rejected": total_rejected,
            "overall_rejection_pct": (
                round(total_rejected / total_all * 100, 1) if total_all > 0 else 0
            ),
            "by_customer": by_customer[:15],
        }

    # ─────────────────────────────────────────────────────────────────────────
    # NEW KPI — 11. Resolved-to-closed lag (awaiting customer sign-off)
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _get_resolved_to_closed_lag(db: Session, base_filter) -> Dict[str, Any]:
        """
        Complaints stuck in 'resolved' status awaiting customer approval.
        updated_at approximates when status became resolved.
        Anything > 7 days in resolved without closing is a flag.
        """
        now = datetime.utcnow()

        stuck = (
            db.query(
                Complaint.avocarbon_plant,
                Complaint.reference_number,
                Complaint.customer,
                Complaint.updated_at,
            )
            .filter(
                base_filter,
                Complaint.status == "resolved",
                Complaint.closed_at.is_(None),
            )
            .all()
        )

        if not stuck:
            return {"total_stuck": 0, "avg_days_stuck": None, "by_plant": []}

        items = []
        for r in stuck:
            days = (now - r.updated_at).days if r.updated_at else 0
            items.append(
                {
                    "plant": r.avocarbon_plant or "UNKNOWN",
                    "reference_number": r.reference_number,
                    "customer": r.customer or "N/A",
                    "days_stuck": days,
                }
            )

        items.sort(key=lambda x: x["days_stuck"], reverse=True)

        plant_agg: Dict[str, List[int]] = {}
        for it in items:
            plant_agg.setdefault(it["plant"], []).append(it["days_stuck"])

        return {
            "total_stuck": len(items),
            "avg_days_stuck": round(
                sum(i["days_stuck"] for i in items) / len(items), 1
            ),
            "flagged": [i for i in items if i["days_stuck"] > 7],
            "by_plant": [
                {"plant": p, "count": len(v), "avg_days": round(sum(v) / len(v), 1)}
                for p, v in plant_agg.items()
            ],
        }

    # ─────────────────────────────────────────────────────────────────────────
    # NEW KPI — 12. Priority distribution
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _get_priority_distribution(db: Session, base_filter) -> List[Dict]:
        """
        The priority field (low/normal/high/urgent) was never surfaced.
        Returns [{ priority, count, open, closed }].
        """
        results = (
            db.query(
                Complaint.priority,
                Complaint.status,
                func.count(Complaint.id).label("count"),
            )
            .filter(base_filter)
            .group_by(Complaint.priority, Complaint.status)
            .all()
        )

        PRIORITIES = ["urgent", "high", "normal", "low"]
        agg: Dict[str, Dict[str, int]] = {
            p: {"open": 0, "closed": 0, "total": 0} for p in PRIORITIES
        }
        for r in results:
            pri = r.priority or "normal"
            if pri not in agg:
                agg[pri] = {"open": 0, "closed": 0, "total": 0}
            if r.status in OPEN_STATUSES:
                agg[pri]["open"] += r.count
            else:
                agg[pri]["closed"] += r.count
            agg[pri]["total"] += r.count

        return [{"priority": p, **v} for p, v in agg.items() if v["total"] > 0]
