"""
app/services/kpi_email_service.py
──────────────────────────────────
Monthly KPI report emailer.

Sends two kinds of emails:
  1. Per-plant PDF  → plant CQT engineer(s) (one email per plant)
  2. Consolidated PDF → quality group manager (all plants in one PDF)

Scheduled via the APScheduler in app/main.py (first day of each month, 07:00 UTC).

  KPI_MANAGER_EMAIL  quality manager address (default: hayfa.rajhi@avocarbon.com)

Optional per-plant override (JSON string):
  KPI_PLANT_EMAILS   '{"FRANKFURT":"franz.mueller@avocarbon.com","CHENNAI":"..."}'
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
from datetime import date
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List

from sqlalchemy.orm import Session

from app.services.kpi_report.kpi_report_pdf import per_plant_report

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Config helpers
# ─────────────────────────────────────────────────────────────────────────────

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

DEFAULT_MANAGER_EMAIL = os.getenv("KPI_MANAGER_EMAIL", "hayfa.rajhi@avocarbon.com")

# Default plant → recipient map (override via KPI_PLANT_EMAILS env var)
_DEFAULT_PLANT_EMAILS: Dict[str, str] = {
    "FRANKFURT": "hayfa.rajhi@avocarbon.com",
    "SCEET": "hayfa.rajhi@avocarbon.com",
    "ASSYMEX": "hayfa.rajhi@avocarbon.com",
    "CHENNAI": "hayfa.rajhi@avocarbon.com",
    "TIANJIN": "hayfa.rajhi@avocarbon.com",
    "DAEGU": "hayfa.rajhi@avocarbon.com",
    "ANHUI": "hayfa.rajhi@avocarbon.com",
    "Kunshan": "hayfa.rajhi@avocarbon.com",
    "SAME": "hayfa.rajhi@avocarbon.com",
    "POITIERS": "hayfa.rajhi@avocarbon.com",
    "CYCLAM": "hayfa.rajhi@avocarbon.com",
}


def _plant_emails() -> Dict[str, str]:
    raw = os.getenv("KPI_PLANT_EMAILS", "")
    if raw:
        try:
            return {**_DEFAULT_PLANT_EMAILS, **json.loads(raw)}
        except json.JSONDecodeError:
            logger.warning("KPI_PLANT_EMAILS is not valid JSON — using defaults")
    return _DEFAULT_PLANT_EMAILS


def _smtp_cfg() -> Dict:
    return {
        "host": os.getenv("SMTP_HOST", "smtp.office365.com"),
        "port": int(os.getenv("SMTP_PORT", "25")),
        "user": os.getenv("SMTP_USER", "noreply@avocarbon.com"),
        "password": os.getenv("SMTP_PASSWORD", ""),
    }


# ─────────────────────────────────────────────────────────────────────────────
# SMTP helper
# ─────────────────────────────────────────────────────────────────────────────


def _send_email(
    to_addrs: List[str],
    subject: str,
    html_body: str,
    attachments: List[Dict],  # [{"filename": "x.pdf", "data": bytes}, ...]
) -> None:
    cfg = _smtp_cfg()
    msg = MIMEMultipart("mixed")
    msg["From"] = cfg["user"]
    msg["To"] = ", ".join(to_addrs)
    msg["Subject"] = subject

    msg.attach(MIMEText(html_body, "html"))

    for att in attachments:
        part = MIMEBase("application", "pdf")
        part.set_payload(att["data"])
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            "attachment",
            filename=att["filename"],
        )
        msg.attach(part)

    with smtplib.SMTP(cfg["host"], cfg["port"]) as server:
        # server.ehlo()
        # server.starttls()
        # server.login(cfg["user"], cfg["password"])
        server.sendmail(cfg["user"], to_addrs, msg.as_string())

    logger.info("KPI email sent to %s — subject: %s", to_addrs, subject)


# ─────────────────────────────────────────────────────────────────────────────
# HTML email body templates
# ─────────────────────────────────────────────────────────────────────────────

_PLANT_HTML = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #f3f4f6; margin: 0; padding: 0; }}
  .wrapper {{ max-width: 600px; margin: 0 auto; background: #ffffff; }}
  .header {{ background: #2E5E3E; padding: 28px 32px; text-align: center; }}
  .header h1 {{ color: #ffffff; margin: 0; font-size: 20px; font-weight: 700; }}
  .header p  {{ color: #A8C5A0; margin: 6px 0 0; font-size: 13px; }}
  .accent-bar {{ height: 4px; background: #D4A843; }}
  .body {{ padding: 28px 32px; }}
  .kpi-grid {{ display: flex; gap: 12px; margin: 20px 0; }}
  .kpi-box {{ flex: 1; background: #f3f4f6; border-radius: 8px; padding: 14px;
               text-align: center; border-top: 3px solid #4A7C59; }}
  .kpi-val {{ font-size: 28px; font-weight: 700; color: #2E5E3E; }}
  .kpi-lbl {{ font-size: 11px; color: #6B7280; margin-top: 4px; }}
  .note {{ font-size: 12px; color: #6B7280; margin-top: 24px; }}
  .footer {{ background: #f3f4f6; padding: 16px 32px; text-align: center;
              font-size: 11px; color: #6B7280; }}
  h2 {{ color: #2E5E3E; font-size: 15px; margin-top: 24px; margin-bottom: 4px; }}
  p  {{ color: #2D2D2D; font-size: 13px; line-height: 1.6; }}
</style>
</head>
<body>
<div class="wrapper">
  <div class="header">
    <h1>AVOCarbon — {plant} KPI Report</h1>
    <p>{month_name} {year} &nbsp;·&nbsp; Monthly Quality Report</p>
  </div>
  <div class="accent-bar"></div>
  <div class="body">
    <p>Dear team,</p>
    <p>Please find attached the <strong>{month_name} {year}</strong> KPI report
       for <strong>{plant}</strong>. Key highlights are summarised below.</p>

    <div class="kpi-grid">
      <div class="kpi-box">
        <div class="kpi-val">{month_count}</div>
        <div class="kpi-lbl">{month_name} Complaints</div>
      </div>
      <div class="kpi-box">
        <div class="kpi-val">{ytd}</div>
        <div class="kpi-lbl">YTD {year}</div>
      </div>
      <div class="kpi-box">
        <div class="kpi-val">{target}</div>
        <div class="kpi-lbl">Monthly Target</div>
      </div>
      <div class="kpi-box" style="border-top-color:#C0392B;">
        <div class="kpi-val" style="color:#C0392B;">{overdue}</div>
        <div class="kpi-lbl">Overdue Complaints</div>
      </div>
    </div>

    <h2>Action Required</h2>
    <p>Please review the attached PDF for the full breakdown including customer analysis,
       product line distribution, and 8D step completion status.</p>

    <p class="note">This report is auto-generated by the AVOCarbon Quality Management System.
    For questions contact the Quality team.</p>
  </div>
  <div class="footer">
    AVOCarbon &nbsp;·&nbsp; Quality Management System &nbsp;·&nbsp;
    Generated {today}
  </div>
</div>
</body>
</html>
"""

_MANAGER_HTML = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #f3f4f6; margin: 0; padding: 0; }}
  .wrapper {{ max-width: 620px; margin: 0 auto; background: #ffffff; }}
  .header {{ background: #2E5E3E; padding: 28px 32px; text-align: center; }}
  .header h1 {{ color: #ffffff; margin: 0; font-size: 20px; font-weight: 700; }}
  .header p  {{ color: #A8C5A0; margin: 6px 0 0; font-size: 13px; }}
  .accent-bar {{ height: 4px; background: #D4A843; }}
  .body {{ padding: 28px 32px; }}
  .kpi-grid {{ display: flex; flex-wrap: wrap; gap: 12px; margin: 20px 0; }}
  .kpi-box {{ flex: 1; min-width: 110px; background: #f3f4f6; border-radius: 8px;
               padding: 14px; text-align: center; border-top: 3px solid #4A7C59; }}
  .kpi-val {{ font-size: 26px; font-weight: 700; color: #2E5E3E; }}
  .kpi-lbl {{ font-size: 11px; color: #6B7280; margin-top: 4px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 16px; }}
  th {{ background: #4A7C59; color: #fff; padding: 8px; text-align: left; }}
  td {{ padding: 7px 8px; border-bottom: 1px solid #e5e7eb; color: #2D2D2D; }}
  tr:nth-child(even) td {{ background: #f9fafb; }}
  .footer {{ background: #f3f4f6; padding: 16px 32px; text-align: center;
              font-size: 11px; color: #6B7280; }}
  h2 {{ color: #2E5E3E; font-size: 15px; margin-top: 24px; margin-bottom: 4px; }}
  p  {{ color: #2D2D2D; font-size: 13px; line-height: 1.6; }}
</style>
</head>
<body>
<div class="wrapper">
  <div class="header">
    <h1>AVOCarbon — Quality Group KPI Report</h1>
    <p>{month_name} {year} &nbsp;·&nbsp; All Plants Consolidated</p>
  </div>
  <div class="accent-bar"></div>
  <div class="body">
    <p>Dear Quality Manager,</p>
    <p>Please find attached the <strong>{month_name} {year}</strong> consolidated KPI report
       covering all AVOCarbon plants.</p>

    <div class="kpi-grid">
      <div class="kpi-box">
        <div class="kpi-val">{total}</div>
        <div class="kpi-lbl">Total Complaints YTD</div>
      </div>
      <div class="kpi-box">
        <div class="kpi-val">{top_plant}</div>
        <div class="kpi-lbl">Top Plant</div>
      </div>
      <div class="kpi-box" style="border-top-color:#C0392B;">
        <div class="kpi-val" style="color:#C0392B;">{overdue}</div>
        <div class="kpi-lbl">Group Overdue</div>
      </div>
      <div class="kpi-box">
        <div class="kpi-val">{reports}</div>
        <div class="kpi-lbl">8D Reports</div>
      </div>
    </div>

    <h2>Plant Summary</h2>
    <table>
      <tr>
        <th>Plant</th>
        <th>{month_name} Complaints</th>
        <th>YTD</th>
        <th>Target</th>
        <th>Overdue</th>
      </tr>
      {plant_rows}
    </table>

    <p style="margin-top:20px;">The full consolidated report with all charts and per-plant 
    drill-downs is attached as a PDF.</p>
  </div>
  <div class="footer">
    AVOCarbon &nbsp;·&nbsp; Quality Management System &nbsp;·&nbsp;
    Generated {today}
  </div>
</div>
</body>
</html>
"""


# ─────────────────────────────────────────────────────────────────────────────
# Main job functions (called by the scheduler)
# ─────────────────────────────────────────────────────────────────────────────


def send_monthly_kpi_reports(db: Session) -> None:
    """
    Entry-point called by the APScheduler on the first day of each month.
    1. Calls DashboardService to build the data dict.
    2. Generates per-plant PDFs → emails each plant's CQT address.
    3. Generates consolidated PDF → emails the quality manager.
    """
    # Late import to avoid circular deps
    from app.services.dashboard_service import DashboardService  # noqa

    today = date.today()
    # Report covers the PREVIOUS month
    report_month = today.month - 1 if today.month > 1 else 12
    report_year = today.year if today.month > 1 else today.year - 1

    logger.info(
        "send_monthly_kpi_reports: generating report for %s %d",
        MONTH_NAMES[report_month - 1],
        report_year,
    )

    try:
        data = DashboardService.get_dashboard_stats(
            db, year=report_year, month=report_month
        )
    except Exception:
        logger.exception("Failed to fetch dashboard stats for KPI report")
        return

    month_name = MONTH_NAMES[report_month - 1]
    plant_emails = _plant_emails()
    manager_email = DEFAULT_MANAGER_EMAIL

    # ── Per-plant emails ──────────────────────────────────────────────────────
    plants = sorted(
        {
            r["plant"]
            for r in data.get("total_by_plant", [])
            if r.get("plant") is not None
        }
    )

    for plant in plants:
        recipient = plant_emails.get(plant, manager_email)
        try:
            pdf_bytes = per_plant_report(data, plant, report_month, report_year)
        except Exception:
            logger.exception("PDF generation failed for plant %s", plant)
            continue

        monthly_data = data.get("monthly_data", [])
        month_count = next(
            (r.get(plant, 0) for r in monthly_data if r["month"] == month_name[:3]),
            0,
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
            year=report_year,
            month_count=month_count,
            ytd=ytd,
            target=target,
            overdue=overdue,
            today=today.strftime("%d %B %Y"),
        )
        filename = f"AVOCarbon_{plant}_KPI_{month_name}_{report_year}.pdf"
        try:
            _send_email(
                to_addrs=[recipient],
                subject=f"[AVOCarbon] {plant} KPI Report — {month_name} {report_year}",
                html_body=html,
                attachments=[{"filename": filename, "data": pdf_bytes}],
            )
        except Exception:
            logger.exception(
                "Failed to send KPI email for plant %s to %s", plant, recipient
            )

    # ── Consolidated quality-manager email ────────────────────────────────────
    # from .kpi_report_pdf import consolidated_report  # noqa
    # try:
    #     consolidated_pdf = consolidated_report(data, report_month, report_year)
    # except Exception:
    #     logger.exception("Consolidated PDF generation failed")
    #     return

    # monthly_data = data.get("monthly_data", [])
    # plant_rows_html = ""
    # for plant in plants:
    #     m_count = next(
    #         (r.get(plant, 0) for r in monthly_data if r["month"] == month_name[:3]), 0
    #     )
    #     ytd_p   = sum(r.get(plant, 0) for r in monthly_data)
    #     target_p = data.get("monthly_targets", {}).get(plant, 0)
    #     od_p    = next(
    #         (r["count"] for r in data.get("overdue_complaints", {}).get("by_plant", [])
    #          if r["plant"] == plant), 0
    #     )
    #     plant_rows_html += (
    #         f"<tr><td><strong>{plant}</strong></td>"
    #         f"<td>{m_count}</td><td>{ytd_p}</td>"
    #         f"<td>{target_p}</td>"
    #         f'<td style="color:{"#C0392B" if od_p else "#4A7C59"};">'
    #         f"<strong>{od_p}</strong></td></tr>"
    #     )

    # manager_html = _MANAGER_HTML.format(
    #     month_name=month_name,
    #     year=report_year,
    #     total=data.get("total_complaints", 0),
    #     top_plant=data.get("top_plant", {}).get("plant", "—"),
    #     overdue=data.get("overdue_complaints", {}).get("total", 0),
    #     reports=data.get("report_stats", {}).get("total_reports", 0),
    #     plant_rows=plant_rows_html,
    #     today=today.strftime("%d %B %Y"),
    # )

    # consolidated_filename = (
    #     f"AVOCarbon_AllPlants_KPI_{month_name}_{report_year}.pdf"
    # )
    # try:
    #     _send_email(
    #         to_addrs=[manager_email],
    #         subject=(
    #             f"[AVOCarbon] Quality KPI Report (All Plants) — "
    #             f"{month_name} {report_year}"
    #         ),
    #         html_body=manager_html,
    #         attachments=[
    #             {"filename": consolidated_filename, "data": consolidated_pdf}
    #         ],
    #     )
    #     # logger.info(
    #     #     "Consolidated KPI report sent to %s (%d plants)",
    #     #     manager_email, len(plants),
    #     # )
    # except Exception:
    #     logger.exception(
    #         "Failed to send consolidated KPI email to %s", manager_email
    #     )
