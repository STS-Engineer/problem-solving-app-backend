"""
services/email_templates.py

Professional HTML email templates for 8D step escalation alerts.
Each level has its own visual weight — L4 is the most urgent.

Usage:
    from app.services.email_templates import build_escalation_email
    subject, html = build_escalation_email(level=2, context={...})
    await send_email(subject=subject, recipients=[...], body_html=html)
"""

from datetime import datetime, timezone

# ── Brand palette (matches the AVOCarbon UI) ─────────────────────────────────
_NAVY = "#1C2B3A"
_ORANGE = "#E8650A"
_BLUE = "#1A5F9E"
_GREEN = "#0E6E42"
_RED = "#B83228"
_PURPLE = "#5B21B6"
_AMBER = "#9E5E00"

_LEVEL_CFG = {
    1: {
        "color": _AMBER,
        "bg": "#FEF3E2",
        "label": "LEVEL 1 — ATTENTION REQUIRED",
        "badge": "#F59E0B",
        "icon": "⚠️",
    },
    2: {
        "color": _ORANGE,
        "bg": "#FEF0E6",
        "label": "LEVEL 2 — ACTION OVERDUE",
        "badge": _ORANGE,
        "icon": "🔔",
    },
    3: {
        "color": _RED,
        "bg": "#FDECEB",
        "label": "LEVEL 3 — URGENT ESCALATION",
        "badge": _RED,
        "icon": "🚨",
    },
    4: {
        "color": _RED,
        "bg": "#FDECEB",
        "label": "LEVEL 4 — CRITICAL — FINAL NOTICE",
        "badge": _RED,
        "icon": "🆘",
    },
}

_STEP_LABELS = {
    "D1": "Team Formation",
    "D2": "Problem Description",
    "D3": "Containment Actions",
    "D4": "Root Cause Analysis",
    "D5": "Corrective Actions",
    "D6": "Implementation",
    "D7": "Prevention",
    "D8": "Closure & Congratulation",
}

_LEVEL_MESSAGES = {
    1: "This step has exceeded its SLA deadline. Please take immediate action to complete it.",
    2: (
        "This step is now significantly overdue. The Customer Quality Technician (CQT) "
        "has been included in this notification. Immediate completion is required."
    ),
    3: (
        "This step has reached a critical delay threshold. Plant management has been notified. "
        "Escalation to customer-facing contacts may follow if no action is taken within 24 hours."
    ),
    4: (
        "This is a final escalation notice. All responsible parties have been notified. "
        "Failure to act immediately may result in a customer escalation and formal non-conformity report."
    ),
}


def _fmt_hours(h: float) -> str:
    if h < 1:
        return "< 1 hour"
    if h < 24:
        return f"{int(h)} hour{'s' if int(h) != 1 else ''}"
    days = int(h // 24)
    hrs = int(h % 24)
    parts = [f"{days} day{'s' if days != 1 else ''}"]
    if hrs:
        parts.append(f"{hrs} hour{'s' if hrs != 1 else ''}")
    return " ".join(parts)


def _progress_bar(step_code: str) -> str:
    """Render a compact 8-step progress indicator as inline HTML."""
    steps = ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8"]
    cells = ""
    for s in steps:
        active = s == step_code
        cells += (
            f'<td align="center" style="padding:0 2px">'
            f'  <div style="width:28px;height:28px;border-radius:6px;'
            f'    background:{"#1C2B3A" if active else "#E0E4E9"};'
            f'    color:{"white" if active else "#7A8FA0"};'
            f"    font-family:monospace;font-size:10px;font-weight:700;"
            f'    line-height:28px;text-align:center;">{s}</div>'
            f"</td>"
        )
    return (
        '<table cellpadding="0" cellspacing="0" border="0" style="margin:0 auto">'
        f"<tr>{cells}</tr>"
        "</table>"
    )


def build_escalation_email(
    level: int,
    *,
    complaint_reference: str,
    complaint_name: str,
    customer: str,
    step_code: str,
    step_name: str | None = None,
    hours_overdue: float,
    due_date: str,  # ISO string
    cqt_email: str | None,
    quality_manager_emails: list[str] | None,
    plant_manager_email: str | None,
    app_url: str = "https://avocarbon-customer-complaint.azurewebsites.net",
) -> tuple[str, str]:
    """
    Build the subject line and HTML body for a given escalation level.

    Returns:
        (subject: str, body_html: str)
    """
    cfg = _LEVEL_CFG.get(level, _LEVEL_CFG[1])
    s_label = step_name or _STEP_LABELS.get(step_code, step_code)
    msg = _LEVEL_MESSAGES.get(level, _LEVEL_MESSAGES[1])
    overdue = _fmt_hours(hours_overdue)

    # Parse due_date for display
    try:
        due_dt = datetime.fromisoformat(due_date.replace("Z", "+00:00"))
        due_display = due_dt.strftime("%d %b %Y at %H:%M UTC")
    except Exception:
        due_display = due_date

    # Subject
    subject = (
        f"{cfg['icon']} [{complaint_reference}] 8D {step_code} Overdue "
        f"by {overdue} — {cfg['label'].split('—')[0].strip()} (L{level})"
    )

    # Recipients table rows. A plant can have several Quality Managers, so
    # render one row per QM email.
    contacts_rows = ""
    qm_label = "Quality Manager"
    role_map = [
        (qm_label, qm) for qm in (quality_manager_emails or [])
    ] + [
        ("CQT (Customer Quality Technician)", cqt_email),
        ("Plant Manager", plant_manager_email),
    ]
    for role, email in role_map:
        if email:
            contacts_rows += (
                f"<tr>"
                f'  <td style="padding:4px 0;font-size:12px;color:#7A8FA0;white-space:nowrap">{role}</td>'
                f'  <td style="padding:4px 0 4px 16px;font-size:12px;color:{_NAVY};font-weight:600">{email}</td>'
                f"</tr>"
            )

    # Progress bar
    progress = _progress_bar(step_code)

    # Direct link to this complaint's overdue step (real frontend route)
    base = app_url.rstrip("/")
    cta_url = f"{base}/8d/{complaint_reference}/{step_code}"
    # Link to the logger, deep-linked to record the escalation action for this step
    logger_url = f"{base}/logger?ref={complaint_reference}&action={step_code}"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>8D Escalation — {complaint_reference}</title>
</head>
<body style="margin:0;padding:0;background-color:#F2F4F7;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased;">

  <!-- Preheader (hidden) -->
  <div style="display:none;max-height:0;overflow:hidden;color:#F2F4F7;">
    [{complaint_reference}] Step {step_code} is overdue by {overdue} · Level {level} escalation
  </div>

  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#F2F4F7;padding:32px 0">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;width:100%">

        <!-- Header bar -->
        <tr>
          <td style="background:{_NAVY};border-radius:10px 10px 0 0;padding:20px 32px 0;border-bottom:3px solid {_ORANGE}">
            <table width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td>
                  <div style="font-family:monospace;font-size:11px;font-weight:700;color:{_ORANGE};letter-spacing:0.1em;margin-bottom:4px">
                    AVOCARBON · 8D QUALITY MANAGEMENT
                  </div>
                  <div style="font-size:22px;font-weight:800;color:white;letter-spacing:-0.02em;padding-bottom:18px">
                    Step Escalation Alert
                  </div>
                </td>
                <td align="right" valign="top" style="padding-bottom:18px">
                  <div style="display:inline-block;padding:6px 14px;border-radius:20px;background:{cfg['badge']};color:white;font-size:11px;font-weight:800;letter-spacing:0.06em;white-space:nowrap">
                    LEVEL {level}
                  </div>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Level banner -->
        <tr>
          <td style="background:{cfg['bg']};border-left:4px solid {cfg['color']};padding:14px 32px">
            <div style="font-size:13px;font-weight:800;color:{cfg['color']};letter-spacing:0.05em">
              {cfg['icon']}&nbsp;&nbsp;{cfg['label']}
            </div>
            <div style="font-size:13px;color:#3D5066;margin-top:4px;line-height:1.5">
              {msg}
            </div>
          </td>
        </tr>

        <!-- Main card -->
        <tr>
          <td style="background:white;padding:28px 32px">

            <!-- Complaint info -->
            <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:24px">
              <tr>
                <td>
                  <div style="font-family:monospace;font-size:11px;font-weight:700;color:#7A8FA0;letter-spacing:0.07em;margin-bottom:6px">COMPLAINT REFERENCE</div>
                  <a href="{cta_url}" target="_blank" style="font-family:monospace;font-size:19px;font-weight:800;color:{_BLUE};letter-spacing:-0.01em;text-decoration:none">{complaint_reference}</a>
                  <div style="font-size:14px;font-weight:600;color:#3D5066;margin-top:4px">{complaint_name}</div>
                  <div style="font-size:12px;color:#7A8FA0;margin-top:2px">Customer: <b style="color:{_NAVY}">{customer}</b></div>
                </td>
              </tr>
            </table>

            <!-- Step progress -->
            <div style="margin-bottom:20px">
              <div style="font-family:monospace;font-size:10px;font-weight:700;color:#7A8FA0;letter-spacing:0.08em;margin-bottom:10px">8D PROGRESS</div>
              {progress}
            </div>

            <!-- Step detail box -->
            <table width="100%" cellpadding="0" cellspacing="0" border="0"
              style="background:#F2F4F7;border-radius:8px;border:1px solid #E0E4E9;margin-bottom:24px">
              <tr>
                <td style="padding:16px 20px">
                  <table width="100%" cellpadding="0" cellspacing="0" border="0">
                    <tr>
                      <td width="50%">
                        <div style="font-size:10px;font-weight:700;color:#7A8FA0;letter-spacing:0.06em;margin-bottom:4px">OVERDUE STEP</div>
                        <div style="font-family:monospace;font-size:15px;font-weight:800;color:{cfg['color']}">{step_code}</div>
                        <div style="font-size:12px;color:#3D5066;margin-top:2px">{s_label}</div>
                      </td>
                      <td width="50%" align="right">
                        <div style="font-size:10px;font-weight:700;color:#7A8FA0;letter-spacing:0.06em;margin-bottom:4px">OVERDUE BY</div>
                        <div style="font-family:monospace;font-size:15px;font-weight:800;color:{cfg['color']}">{overdue}</div>
                        <div style="font-size:11px;color:#7A8FA0;margin-top:2px">Due: {due_display}</div>
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>
            </table>

            <!-- Notified contacts -->
            <div style="margin-bottom:24px">
              <div style="font-size:10px;font-weight:700;color:#7A8FA0;letter-spacing:0.07em;margin-bottom:10px">
                Related Parties to This Complaint
              </div>
              <table cellpadding="0" cellspacing="0" border="0" style="width:100%">
                {contacts_rows}
              </table>
            </div>

            <!-- Divider -->
            <div style="border-top:1px solid #E0E4E9;margin-bottom:24px"></div>

            <!-- CTA Buttons -->
            <table cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td style="padding-right:10px">
                  <a href="{cta_url}" target="_blank"
                    style="display:inline-block;background:{_ORANGE};color:#ffffff;text-decoration:none;padding:13px 24px;border-radius:8px;font-size:14px;font-weight:700;letter-spacing:0.02em;font-family:'Helvetica Neue',Arial,sans-serif">
                    Open Complaint &rarr;
                  </a>
                </td>
                <td>
                  <a href="{logger_url}" target="_blank"
                    style="display:inline-block;background:#ffffff;color:{_NAVY};text-decoration:none;padding:12px 24px;border:1.5px solid {_NAVY};border-radius:8px;font-size:14px;font-weight:700;letter-spacing:0.02em;font-family:'Helvetica Neue',Arial,sans-serif">
                    Record Actions Taken
                  </a>
                </td>
              </tr>
            </table>
            <div style="font-size:11px;color:#7A8FA0;margin-top:10px;line-height:1.5">
              Use <b style="color:{_NAVY}">Record Actions Taken</b> to log what you did in response to this escalation
              (called the responsible, reassigned, approved a purchase&hellip;) so it appears in the escalation track.
            </div>

          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background:{_NAVY};border-radius:0 0 10px 10px;padding:16px 32px">
            <div style="font-size:11px;color:#7A8FA0;line-height:1.6">
              This is an automated notification from the AVOCarbon Quality Management System.<br>
              Do not reply to this email.
              If you believe you received this in error, contact your quality manager.
            </div>
          
          </td>
        </tr>

      </table>
    </td></tr>
  </table>

</body>
</html>"""

    return subject, html


# ── Intake (pre-complaint) escalation reminder ──────────────────────────────


def build_intake_escalation_email(
    *,
    intake_id: int,
    stage: str,
    level: int,
    hours_waiting: float,
    sender_email: str | None,
    subject_line: str | None,
    plant: str | None,
    assigned_cqe_email: str | None,
    review_base_url: str,
    test_mode: bool = False,
) -> tuple[str, str]:
    """
    Reminder for an email complaint that has not yet entered the complaint list.

    stage 'awaiting_cqt'        → a QM must assign a CQT
    stage 'awaiting_complaint'  → the assigned CQT must create the complaint
    """
    waited = (
        f"{hours_waiting * 60:.0f} min" if test_mode else f"{hours_waiting:.1f} h"
    )
    base = (review_base_url or "").rstrip("/")

    if stage == "awaiting_cqt":
        headline = "Email complaint awaiting a CQT assignment"
        action = (
            "This complaint arrived by email and no CQT has been assigned yet. "
            "Please assign a CQT so the 8D process can start."
        )
        cta_label = "Review &amp; assign a CQT"
        cta_url = f"{base}/intake/{intake_id}"
    else:  # awaiting_complaint
        headline = "Assigned complaint not yet created"
        action = (
            "A CQT was assigned to this email complaint but the 8D complaint has "
            "not been created yet. Please complete and create it."
        )
        cta_label = "Complete &amp; create the complaint"
        cta_url = f"{base}/intake/{intake_id}/complete"

    subject = (
        f"⏰ [Intake #{intake_id}] {headline} — reminder L{level} "
        f"(waiting {waited})"
    )

    def _row(label: str, value: str | None) -> str:
        return (
            f'<tr><td style="padding:6px 0;font-weight:700;color:{_NAVY};width:38%;">'
            f'{label}</td><td style="padding:6px 0;color:#3D5066;">{value or "—"}</td></tr>'
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#F2F4F7;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#F2F4F7;padding:32px 0">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;width:100%">
        <tr>
          <td style="background:{_NAVY};border-radius:10px 10px 0 0;padding:20px 32px;border-bottom:3px solid {_ORANGE}">
            <div style="font-family:monospace;font-size:11px;font-weight:700;color:{_ORANGE};letter-spacing:0.1em;margin-bottom:4px">
              AVOCARBON · EMAIL INTAKE
            </div>
            <div style="font-size:20px;font-weight:800;color:white;letter-spacing:-0.02em">
              {headline}
            </div>
          </td>
        </tr>
        <tr>
          <td style="background:#FFF7ED;border-left:4px solid {_ORANGE};padding:14px 32px">
            <div style="font-size:13px;font-weight:800;color:{_ORANGE};letter-spacing:0.05em">
              REMINDER · LEVEL {level} · WAITING {waited.upper()}
            </div>
            <div style="font-size:13px;color:#3D5066;margin-top:4px;line-height:1.5">
              {action}
            </div>
          </td>
        </tr>
        <tr>
          <td style="background:white;padding:22px 32px">
            <table width="100%" cellpadding="0" cellspacing="0" border="0" style="font-size:13px">
              {_row("Intake", f"#{intake_id}")}
              {_row("From", sender_email)}
              {_row("Subject", subject_line)}
              {_row("Plant", plant)}
              {_row("Assigned CQT", assigned_cqe_email)}
            </table>
            <div style="margin:22px 0 4px">
              <a href="{cta_url}"
                 style="display:inline-block;background:{_ORANGE};color:#fff;text-decoration:none;
                        padding:11px 22px;border-radius:6px;font-size:14px;font-weight:700">
                {cta_label}
              </a>
            </div>
          </td>
        </tr>
        <tr>
          <td style="background:white;border-radius:0 0 10px 10px;padding:0 32px 24px">
            <p style="font-size:12px;color:#8A95A8;border-top:1px solid #eee;padding-top:14px;margin:0">
              Automated reminder — this complaint has not yet entered the 8D
              workflow. It will keep escalating until a CQT is assigned and the
              complaint is created.
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""

    return subject, html
