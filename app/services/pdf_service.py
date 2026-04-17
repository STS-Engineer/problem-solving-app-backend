import asyncio
import html
import io
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image as RLImage,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy.orm import Session

from app.models.complaint import Complaint
from app.models.report import Report
from app.models.report_step import ReportStep
from app.services.file_storage import storage

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:  # pragma: no cover - optional dependency in local dev
    PdfReader = None
    PdfWriter = None

logger = logging.getLogger(__name__)

STEP_CODES_BY_EXPORT = {
    "D3": ("D1", "D2", "D3"),
    "D5": ("D1", "D2", "D3", "D4", "D5"),
}

PDF_TITLES = {
    "D3": "8D Investigation Report (D1-D3)",
    "D5": "8D Investigation Report (D1-D5)",
}

SECTION_TITLES = {
    "D1": "D1 - Establish the Team",
    "D2": "D2 - Describe the Problem",
    "D3": "D3 - Develop Interim Containment Action",
    "D4": "D4 - Determine Root Cause",
    "D5": "D5 - Choose and Verify Permanent Corrective Actions",
}

ATTACHMENT_SCOPE_LABELS = {
    "occurrence": "Occurrence",
    "detection": "Detection",
    "lesson": "Lesson",
}

SUSPECTED_LOCATION_LABELS = {
    "supplier_site": "Supplier Site",
    "in_transit": "In Transit",
    "production_floor": "Production Floor",
    "warehouse": "Warehouse",
    "customer_site": "Customer Site",
    "others": "Others",
}

ALERT_LABELS = {
    "production_shift_leaders": "Production Shift Leaders",
    "quality_control": "Quality Control",
    "warehouse": "Warehouse",
    "maintenance": "Maintenance",
    "customer_contact": "Customer Contact",
    "production_planner": "Production Planner",
}

FOUR_M_COLUMNS: Sequence[Tuple[str, str]] = (
    ("material", "Material"),
    ("method", "Method"),
    ("machine", "Machine"),
    ("manpower", "Manpower"),
    ("environment", "Environment"),
)


@dataclass
class AttachmentRecord:
    filename: str
    mime_type: str
    uploaded_at: Optional[str]
    action_type: Optional[str]
    action_index: Optional[int]
    content: Optional[bytes]


def _styles():
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="ReportTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=22,
            textColor=colors.HexColor("#0F172A"),
            spaceAfter=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SectionTitle",
            parent=styles["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=14,
            leading=18,
            textColor=colors.HexColor("#0F172A"),
            spaceBefore=8,
            spaceAfter=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SubSectionTitle",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=14,
            textColor=colors.HexColor("#1D4ED8"),
            spaceBefore=6,
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Body",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#111827"),
            spaceAfter=4,
        )
    )
    styles.add(
        ParagraphStyle(
            name="BodySmall",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#475569"),
            spaceAfter=3,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Muted",
            parent=styles["BodyText"],
            fontName="Helvetica-Oblique",
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#64748B"),
            spaceAfter=4,
        )
    )
    return styles


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if hasattr(value, "value"):
        return _text(value.value, default)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value).strip()


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _html_text(value: Any, default: str = "-") -> str:
    text = _text(value, default)
    if not text:
        text = default
    return html.escape(text).replace("\n", "<br/>")


def _paragraph_or_empty(label: str, value: Any, styles, default: str = "No data recorded yet.") -> Paragraph:
    if _has_value(value):
        return Paragraph(
            f"<b>{html.escape(label)}:</b> {_html_text(value)}",
            styles["Body"],
        )
    return Paragraph(
        f"<b>{html.escape(label)}:</b> {html.escape(default)}",
        styles["Muted"],
    )


def _table(rows: Sequence[Sequence[Any]], col_widths: Sequence[float], repeat_rows: int = 1) -> Table:
    table = Table(rows, colWidths=col_widths, repeatRows=repeat_rows, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("LEADING", (0, 0), (-1, -1), 10),
                ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _info_table(items: Sequence[Tuple[str, Any]], styles) -> Table:
    rows: List[List[Any]] = []
    for label, value in items:
        rows.append(
            [
                Paragraph(f"<b>{html.escape(label)}</b>", styles["BodySmall"]),
                Paragraph(_html_text(value), styles["Body"]),
            ]
        )

    table = Table(rows, colWidths=[48 * mm, 128 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F8FAFC")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def _format_dt(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    return _text(value)


def _step_cost(step: Optional[ReportStep], data: Dict[str, Any]) -> Optional[str]:
    cost = data.get("cost")
    if isinstance(cost, dict):
        if cost.get("no_cost"):
            return "No cost"
        if cost.get("amount") is not None:
            currency = _text(cost.get("currency"), "EUR")
            return f"{cost.get('amount')} {currency}"
    if step is not None and step.cost is not None:
        try:
            if float(step.cost) == 0:
                return "No cost"
        except (TypeError, ValueError):
            pass
        return _text(step.cost)
    return None


def _fetch_file_bytes(stored_name: str) -> Optional[bytes]:
    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(storage.fetch_content(stored_name))
        finally:
            loop.close()
    except Exception as exc:
        logger.warning("Could not fetch attachment %s: %s", stored_name, exc)
        return None


def _scope_label(action_type: Optional[str], action_index: Optional[int]) -> str:
    if action_type is None or action_index is None:
        return "Step attachments"
    scope = ATTACHMENT_SCOPE_LABELS.get(action_type, action_type.title())
    return f"{scope} action #{action_index + 1}"


def _collect_attachments(step: Optional[ReportStep]) -> List[AttachmentRecord]:
    if step is None:
        return []

    attachments: List[AttachmentRecord] = []
    ordered = sorted(
        step.step_files or [],
        key=lambda sf: (
            sf.action_type or "",
            sf.action_index if sf.action_index is not None else -1,
            sf.attachment_order or 0,
            sf.created_at.timestamp() if sf.created_at else 0,
            sf.id,
        ),
    )
    for step_file in ordered:
        file = step_file.file
        if file is None:
            continue
        content = None
        if (file.mime_type or "").startswith("image/") or (file.mime_type or "") == "application/pdf":
            content = _fetch_file_bytes(file.stored_path)
        attachments.append(
            AttachmentRecord(
                filename=file.original_name,
                mime_type=file.mime_type or "application/octet-stream",
                uploaded_at=_format_dt(file.created_at),
                action_type=step_file.action_type,
                action_index=step_file.action_index,
                content=content,
            )
        )
    return attachments


def _add_step_header(story: List[Any], styles, title: str, step: Optional[ReportStep], data: Dict[str, Any]) -> None:
    story.append(Paragraph(title, styles["SectionTitle"]))
    if step is not None:
        summary_bits = [f"Status: {_text(step.status, 'draft').title()}"]
        if step.completed_at:
            summary_bits.append(f"Completed: {_format_dt(step.completed_at)}")
        cost = _step_cost(step, data)
        if cost:
            summary_bits.append(f"Step cost: {cost}")
        story.append(Paragraph(" | ".join(summary_bits), styles["BodySmall"]))
    story.append(Spacer(1, 4))


def _add_empty_state(story: List[Any], styles, message: str = "No data recorded yet.") -> None:
    story.append(Paragraph(message, styles["Muted"]))
    story.append(Spacer(1, 6))


def _add_attachments(story: List[Any], styles, attachments: Sequence[AttachmentRecord]) -> None:
    if not attachments:
        return

    story.append(Paragraph("Attachments", styles["SubSectionTitle"]))
    previous_scope = None
    for attachment in attachments:
        scope = _scope_label(attachment.action_type, attachment.action_index)
        if scope != previous_scope:
            story.append(Paragraph(scope, styles["BodySmall"]))
            previous_scope = scope

        meta = f"<b>{html.escape(attachment.filename)}</b> ({html.escape(attachment.mime_type)})"
        if attachment.uploaded_at:
            meta += f" - uploaded {html.escape(attachment.uploaded_at)}"
        story.append(Paragraph(meta, styles["Body"]))

        if attachment.content and attachment.mime_type.startswith("image/"):
            try:
                img = RLImage(io.BytesIO(attachment.content))
                scale = min((170 * mm) / img.imageWidth, (85 * mm) / img.imageHeight, 1)
                img.drawWidth = img.imageWidth * scale
                img.drawHeight = img.imageHeight * scale
                img.hAlign = "LEFT"
                story.append(img)
            except Exception as exc:
                logger.warning("Could not render attachment preview for %s: %s", attachment.filename, exc)
                story.append(
                    Paragraph(
                        "Image preview is unavailable, but the attachment metadata is included.",
                        styles["Muted"],
                    )
                )
        else:
            if (
                attachment.mime_type == "application/pdf"
                and PdfReader is not None
                and PdfWriter is not None
            ):
                message = "PDF attachment will be appended to the export."
            elif attachment.mime_type == "application/pdf":
                message = "PDF attachment stored with the report evidence."
            else:
                message = "Binary attachment stored with the report evidence."
            story.append(
                Paragraph(
                    message,
                    styles["Muted"],
                )
            )
        story.append(Spacer(1, 6))


def _merge_pdf_attachments(
    main_pdf: bytes,
    attachments: Iterable[AttachmentRecord],
) -> bytes:
    if PdfReader is None or PdfWriter is None:
        return main_pdf

    pdf_attachments = [
        attachment
        for attachment in attachments
        if attachment.mime_type == "application/pdf" and attachment.content
    ]
    if not pdf_attachments:
        return main_pdf

    try:
        writer = PdfWriter()
        main_reader = PdfReader(io.BytesIO(main_pdf))
        for page in main_reader.pages:
            writer.add_page(page)

        for attachment in pdf_attachments:
            attachment_reader = PdfReader(io.BytesIO(attachment.content))
            for page in attachment_reader.pages:
                writer.add_page(page)

        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()
    except Exception as exc:
        logger.warning("Could not merge PDF attachments into export: %s", exc)
        return main_pdf


def _add_d1(story: List[Any], styles, step: Optional[ReportStep], data: Dict[str, Any]) -> None:
    _add_step_header(story, styles, SECTION_TITLES["D1"], step, data)
    members = data.get("team_members") or []
    if not members:
        _add_empty_state(story, styles)
        return

    rows: List[List[Any]] = [
        [
            Paragraph("<b>#</b>", styles["BodySmall"]),
            Paragraph("<b>Name</b>", styles["BodySmall"]),
            Paragraph("<b>Function</b>", styles["BodySmall"]),
            Paragraph("<b>Department</b>", styles["BodySmall"]),
        ]
    ]
    for index, member in enumerate(members, start=1):
        rows.append(
            [
                Paragraph(str(index), styles["Body"]),
                Paragraph(_html_text(member.get("name")), styles["Body"]),
                Paragraph(_html_text(member.get("function")), styles["Body"]),
                Paragraph(_html_text(member.get("department")), styles["Body"]),
            ]
        )

    story.append(_table(rows, [12 * mm, 52 * mm, 62 * mm, 52 * mm]))
    story.append(Spacer(1, 8))


def _add_d2(story: List[Any], styles, step: Optional[ReportStep], data: Dict[str, Any], attachments: Sequence[AttachmentRecord]) -> None:
    _add_step_header(story, styles, SECTION_TITLES["D2"], step, data)
    story.append(_paragraph_or_empty("Problem description", data.get("problem_description"), styles))
    story.append(Spacer(1, 4))

    five_w = data.get("five_w_2h") or {}
    overview_items = [
        ("What", five_w.get("what")),
        ("Where", five_w.get("where")),
        ("When", five_w.get("when")),
        ("Who", five_w.get("who")),
        ("How", five_w.get("how")),
        ("How many", five_w.get("how_many")),
        ("Why", five_w.get("why")),
        ("Standard applicable", data.get("standard_applicable")),
        ("Expected situation", data.get("expected_situation")),
        ("Observed situation", data.get("observed_situation")),
    ]
    story.append(_info_table(overview_items, styles))
    story.append(Spacer(1, 6))

    factors = data.get("is_is_not_factors") or []
    if factors:
        story.append(Paragraph("Is / Is Not Analysis", styles["SubSectionTitle"]))
        factor_rows: List[List[Any]] = [
            [
                Paragraph("<b>Factor</b>", styles["BodySmall"]),
                Paragraph("<b>Is</b>", styles["BodySmall"]),
                Paragraph("<b>Is Not</b>", styles["BodySmall"]),
            ]
        ]
        for factor in factors:
            factor_rows.append(
                [
                    Paragraph(_html_text(factor.get("factor")), styles["Body"]),
                    Paragraph(
                        _html_text(
                            factor.get("is_problem") or factor.get("is_value"),
                        ),
                        styles["Body"],
                    ),
                    Paragraph(
                        _html_text(
                            factor.get("is_not_problem") or factor.get("is_not_value"),
                        ),
                        styles["Body"],
                    ),
                ]
            )
        story.append(_table(factor_rows, [36 * mm, 70 * mm, 70 * mm]))
        story.append(Spacer(1, 6))

    _add_attachments(story, styles, attachments)


def _add_d3(story: List[Any], styles, step: Optional[ReportStep], data: Dict[str, Any], attachments: Sequence[AttachmentRecord]) -> None:
    _add_step_header(story, styles, SECTION_TITLES["D3"], step, data)

    defected = data.get("defected_part_status") or {}
    story.append(Paragraph("Defected Part Status", styles["SubSectionTitle"]))
    story.append(
        _info_table(
            [
                ("Returned to supplier", defected.get("returned")),
                ("Isolated", defected.get("isolated")),
                ("Isolation location", defected.get("isolated_location")),
                ("Identified / labelled", defected.get("identified")),
                ("Identification method", defected.get("identified_method")),
            ],
            styles,
        )
    )
    story.append(Spacer(1, 6))

    suspected_rows = data.get("suspected_parts_status") or []
    if suspected_rows:
        story.append(Paragraph("Suspected Parts Status", styles["SubSectionTitle"]))
        rows: List[List[Any]] = [
            [
                Paragraph("<b>Location</b>", styles["BodySmall"]),
                Paragraph("<b>Inventory</b>", styles["BodySmall"]),
                Paragraph("<b>Actions</b>", styles["BodySmall"]),
                Paragraph("<b>Leader</b>", styles["BodySmall"]),
                Paragraph("<b>Results</b>", styles["BodySmall"]),
            ]
        ]
        for row in suspected_rows:
            rows.append(
                [
                    Paragraph(
                        _html_text(
                            SUSPECTED_LOCATION_LABELS.get(
                                row.get("location"),
                                _text(row.get("location")),
                            )
                        ),
                        styles["Body"],
                    ),
                    Paragraph(_html_text(row.get("inventory")), styles["Body"]),
                    Paragraph(_html_text(row.get("actions")), styles["Body"]),
                    Paragraph(_html_text(row.get("leader")), styles["Body"]),
                    Paragraph(_html_text(row.get("results")), styles["Body"]),
                ]
            )
        story.append(_table(rows, [32 * mm, 25 * mm, 63 * mm, 32 * mm, 28 * mm]))
        story.append(Spacer(1, 6))

    alert = data.get("alert_communicated_to") or {}
    recipients = [
        label for key, label in ALERT_LABELS.items() if alert.get(key)
    ]
    story.append(Paragraph("Alert Communication", styles["SubSectionTitle"]))
    story.append(
        _info_table(
            [
                ("Recipients", ", ".join(recipients) or "None selected"),
                ("Alert number", data.get("alert_number")),
            ],
            styles,
        )
    )
    story.append(Spacer(1, 6))

    restart = data.get("restart_production") or {}
    story.append(Paragraph("Restart Production", styles["SubSectionTitle"]))
    story.append(
        _info_table(
            [
                ("When", restart.get("when")),
                ("First certified lot", restart.get("first_certified_lot")),
                ("Approved by", restart.get("approved_by")),
                ("Verification method", restart.get("method")),
                ("Parts & boxes identification", restart.get("identification")),
                ("Containment responsible", data.get("containment_responsible")),
            ],
            styles,
        )
    )
    story.append(Spacer(1, 6))
    _add_attachments(story, styles, attachments)


def _four_m_rows(four_m: Dict[str, Any], styles) -> Optional[Table]:
    has_data = any(
        _has_value((four_m.get(row_key) or {}).get(field))
        for row_key in ("row_1", "row_2", "row_3")
        for field, _ in FOUR_M_COLUMNS
    )
    if not has_data:
        return None

    rows: List[List[Any]] = [
        [Paragraph("<b>Row</b>", styles["BodySmall"])]
        + [
            Paragraph(f"<b>{html.escape(label)}</b>", styles["BodySmall"])
            for _, label in FOUR_M_COLUMNS
        ]
    ]
    for row_key, row_label in (("row_1", "A"), ("row_2", "B"), ("row_3", "C")):
        row = four_m.get(row_key) or {}
        rows.append(
            [Paragraph(row_label, styles["Body"])]
            + [
                Paragraph(_html_text(row.get(field)), styles["Body"])
                for field, _ in FOUR_M_COLUMNS
            ]
        )
    return _table(rows, [12 * mm, 32 * mm, 32 * mm, 32 * mm, 32 * mm, 32 * mm])


def _five_whys_rows(whys: Dict[str, Any], styles) -> Optional[Table]:
    rows: List[List[Any]] = [
        [
            Paragraph("<b>Why</b>", styles["BodySmall"]),
            Paragraph("<b>Question</b>", styles["BodySmall"]),
            Paragraph("<b>Answer</b>", styles["BodySmall"]),
        ]
    ]
    has_data = False
    for index in range(1, 6):
        item = whys.get(f"why_{index}") or {}
        question = item.get("question")
        answer = item.get("answer")
        has_data = has_data or _has_value(question) or _has_value(answer)
        rows.append(
            [
                Paragraph(str(index), styles["Body"]),
                Paragraph(_html_text(question), styles["Body"]),
                Paragraph(_html_text(answer), styles["Body"]),
            ]
        )
    if not has_data:
        return None
    return _table(rows, [14 * mm, 82 * mm, 82 * mm])


def _add_root_cause_block(
    story: List[Any],
    styles,
    subtitle: str,
    four_m: Dict[str, Any],
    whys: Dict[str, Any],
    root_cause: Dict[str, Any],
) -> None:
    story.append(Paragraph(subtitle, styles["SubSectionTitle"]))
    four_m_table = _four_m_rows(four_m, styles)
    if four_m_table:
        story.append(four_m_table)
        story.append(Spacer(1, 4))
        story.append(
            _paragraph_or_empty(
                "Selected problem",
                (four_m or {}).get("selected_problem"),
                styles,
            )
        )
        story.append(Spacer(1, 4))
    else:
        story.append(Paragraph("4M analysis not recorded yet.", styles["Muted"]))
        story.append(Spacer(1, 4))

    whys_table = _five_whys_rows(whys, styles)
    if whys_table:
        story.append(whys_table)
        story.append(Spacer(1, 4))
    else:
        story.append(Paragraph("5 Whys analysis not recorded yet.", styles["Muted"]))
        story.append(Spacer(1, 4))

    story.append(
        _info_table(
            [
                ("Root cause", (root_cause or {}).get("root_cause")),
                ("Validation method", (root_cause or {}).get("validation_method")),
            ],
            styles,
        )
    )
    story.append(Spacer(1, 6))


def _add_d4(story: List[Any], styles, step: Optional[ReportStep], data: Dict[str, Any], attachments: Sequence[AttachmentRecord]) -> None:
    _add_step_header(story, styles, SECTION_TITLES["D4"], step, data)
    _add_root_cause_block(
        story,
        styles,
        "Occurrence Root Cause",
        data.get("four_m_occurrence") or {},
        data.get("five_whys_occurrence") or {},
        data.get("root_cause_occurrence") or {},
    )
    _add_root_cause_block(
        story,
        styles,
        "Non-Detection Root Cause",
        data.get("four_m_non_detection") or {},
        data.get("five_whys_non_detection") or {},
        data.get("root_cause_non_detection") or {},
    )
    _add_attachments(story, styles, attachments)


def _add_actions_section(
    story: List[Any],
    styles,
    actions: Sequence[Dict[str, Any]],
    section_title: str,
    include_monitoring: bool = False,
    monitoring: Optional[Dict[str, Any]] = None,
    checklist: Optional[Sequence[Dict[str, Any]]] = None,
) -> None:
    story.append(Paragraph(section_title, styles["SubSectionTitle"]))
    if not actions:
        story.append(Paragraph("No actions recorded yet.", styles["Muted"]))
        story.append(Spacer(1, 4))
    else:
        rows: List[List[Any]] = [
            [
                Paragraph("<b>#</b>", styles["BodySmall"]),
                Paragraph("<b>Action</b>", styles["BodySmall"]),
                Paragraph("<b>Responsible</b>", styles["BodySmall"]),
                Paragraph("<b>Due date</b>", styles["BodySmall"]),
            ]
        ]
        for index, action in enumerate(actions, start=1):
            rows.append(
                [
                    Paragraph(str(index), styles["Body"]),
                    Paragraph(_html_text(action.get("action")), styles["Body"]),
                    Paragraph(_html_text(action.get("responsible")), styles["Body"]),
                    Paragraph(_html_text(action.get("due_date")), styles["Body"]),
                ]
            )
        story.append(_table(rows, [12 * mm, 100 * mm, 44 * mm, 24 * mm]))
        story.append(Spacer(1, 4))

    if include_monitoring:
        story.append(
            _info_table(
                [
                    ("Monitoring interval", (monitoring or {}).get("monitoring_interval")),
                    ("Pieces produced", (monitoring or {}).get("pieces_produced")),
                    ("Rejection rate", (monitoring or {}).get("rejection_rate")),
                    ("Checklist items", len(checklist or [])),
                ],
                styles,
            )
        )
        story.append(Spacer(1, 4))


def _add_d5(story: List[Any], styles, step: Optional[ReportStep], data: Dict[str, Any], attachments: Sequence[AttachmentRecord]) -> None:
    _add_step_header(story, styles, SECTION_TITLES["D5"], step, data)
    _add_actions_section(
        story,
        styles,
        data.get("corrective_actions_occurrence") or [],
        "Occurrence Actions",
    )
    _add_actions_section(
        story,
        styles,
        data.get("corrective_actions_detection") or [],
        "Detection Actions",
    )
    _add_attachments(story, styles, attachments)


def _draw_page_header(title: str, complaint: Complaint, report: Report):
    def _draw(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#CBD5E1"))
        canvas.setLineWidth(0.6)
        canvas.line(doc.leftMargin, A4[1] - 16 * mm, A4[0] - doc.rightMargin, A4[1] - 16 * mm)

        canvas.setFont("Helvetica-Bold", 11)
        canvas.setFillColor(colors.HexColor("#0F172A"))
        canvas.drawString(doc.leftMargin, A4[1] - 12 * mm, title)

        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#475569"))
        canvas.drawRightString(
            A4[0] - doc.rightMargin,
            A4[1] - 12 * mm,
            f"Report {_text(report.report_number)} | Complaint {_text(complaint.reference_number)}",
        )
        canvas.drawRightString(
            A4[0] - doc.rightMargin,
            10 * mm,
            f"Page {canvas.getPageNumber()}",
        )
        canvas.restoreState()

    return _draw


class PDFService:
    @staticmethod
    def _resolve_report(db: Session, complaint_identifier: str | int) -> Tuple[Complaint, Report]:
        identifier = str(complaint_identifier).strip()
        complaint = (
            db.query(Complaint)
            .filter(Complaint.reference_number == identifier)
            .first()
        )
        if complaint is None and identifier.isdigit():
            complaint = db.query(Complaint).filter(Complaint.id == int(identifier)).first()
        if complaint is None:
            raise ValueError("Complaint not found")

        report = db.query(Report).filter(Report.complaint_id == complaint.id).first()
        if report is None:
            raise ValueError("No 8D report found for this complaint")
        return complaint, report

    @staticmethod
    def _step_map(report: Report) -> Dict[str, ReportStep]:
        return {step.step_code: step for step in report.steps or []}

    @staticmethod
    def _build_story(
        complaint: Complaint,
        report: Report,
        steps: Dict[str, ReportStep],
        include_until: str,
        attachments_by_step: Dict[str, List[AttachmentRecord]],
    ) -> Tuple[List[Any], Any]:
        styles = _styles()
        story: List[Any] = []
        title = PDF_TITLES[include_until]

        story.append(Paragraph(title, styles["ReportTitle"]))
        story.append(
            Paragraph(
                _html_text(
                    f"Complaint {_text(complaint.reference_number)} - {_text(complaint.complaint_name)}"
                ),
                styles["Body"],
            )
        )
        story.append(Spacer(1, 6))
        story.append(
            _info_table(
                [
                    ("Report number", report.report_number),
                    ("Complaint status", complaint.status),
                    ("Customer", complaint.customer),
                    ("Customer plant", complaint.customer_plant_name),
                    ("AVOCarbon plant", complaint.avocarbon_plant),
                    ("Product line", complaint.product_line),
                    ("Product type", complaint.avocarbon_product_type),
                    (
                        "Complaint opening date",
                        complaint.complaint_opening_date.isoformat()
                        if complaint.complaint_opening_date
                        else "",
                    ),
                    (
                        "Customer complaint date",
                        complaint.customer_complaint_date.isoformat()
                        if complaint.customer_complaint_date
                        else "",
                    ),
                    (
                        "Export scope",
                        "D1 to D3" if include_until == "D3" else "D1 to D5",
                    ),
                ],
                styles,
            )
        )
        story.append(Spacer(1, 8))
        story.append(_paragraph_or_empty("Complaint description", complaint.complaint_description, styles))
        story.append(Spacer(1, 10))

        section_builders = {
            "D1": lambda: _add_d1(
                story,
                styles,
                steps.get("D1"),
                (steps.get("D1").data or {}) if steps.get("D1") else {},
            ),
            "D2": lambda: _add_d2(
                story,
                styles,
                steps.get("D2"),
                (steps.get("D2").data or {}) if steps.get("D2") else {},
                attachments_by_step.get("D2", []),
            ),
            "D3": lambda: _add_d3(
                story,
                styles,
                steps.get("D3"),
                (steps.get("D3").data or {}) if steps.get("D3") else {},
                attachments_by_step.get("D3", []),
            ),
            "D4": lambda: _add_d4(
                story,
                styles,
                steps.get("D4"),
                (steps.get("D4").data or {}) if steps.get("D4") else {},
                attachments_by_step.get("D4", []),
            ),
            "D5": lambda: _add_d5(
                story,
                styles,
                steps.get("D5"),
                (steps.get("D5").data or {}) if steps.get("D5") else {},
                attachments_by_step.get("D5", []),
            ),
        }

        selected_steps = STEP_CODES_BY_EXPORT[include_until]
        for index, step_code in enumerate(selected_steps):
            section_builders[step_code]()
            if index < len(selected_steps) - 1:
                story.append(PageBreak())

        return story, _draw_page_header(title, complaint, report)

    @staticmethod
    def _generate_report_until(
        db: Session,
        complaint_id: str | int,
        include_until: str = "D3",
    ) -> bytes:
        if include_until not in STEP_CODES_BY_EXPORT:
            raise ValueError(f"Unsupported export scope '{include_until}'")

        complaint, report = PDFService._resolve_report(db, complaint_id)
        steps = PDFService._step_map(report)
        selected_steps = STEP_CODES_BY_EXPORT[include_until]
        attachments_by_step = {
            step_code: _collect_attachments(steps.get(step_code))
            for step_code in selected_steps
        }
        story, page_header = PDFService._build_story(
            complaint=complaint,
            report=report,
            steps=steps,
            include_until=include_until,
            attachments_by_step=attachments_by_step,
        )

        buffer = io.BytesIO()
        document = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=15 * mm,
            rightMargin=15 * mm,
            topMargin=22 * mm,
            bottomMargin=16 * mm,
            title=PDF_TITLES[include_until],
            author="AVOCarbon 8D",
        )
        document.build(story, onFirstPage=page_header, onLaterPages=page_header)

        file_bytes = buffer.getvalue()
        return _merge_pdf_attachments(
            file_bytes,
            (
                attachment
                for step_code in selected_steps
                for attachment in attachments_by_step.get(step_code, [])
            ),
        )

    @staticmethod
    def generate_report(db: Session, complaint_id: str | int) -> bytes:
        return PDFService.generate_report_d1_d3(db, complaint_id)

    @staticmethod
    def generate_report_d1_d3(db: Session, complaint_id: str | int) -> bytes:
        return PDFService._generate_report_until(db, complaint_id, include_until="D3")

    @staticmethod
    def generate_report_d1_to_d5(db: Session, complaint_id: str | int) -> bytes:
        return PDFService._generate_report_until(db, complaint_id, include_until="D5")


def _generate_report_until(
    db: Session,
    complaint_id: str | int,
    include_until: str = "D3",
) -> bytes:
    return PDFService._generate_report_until(db, complaint_id, include_until)


def generate_report(db: Session, complaint_id: str | int) -> bytes:
    return PDFService.generate_report(db, complaint_id)


def generate_report_d1_d3(db: Session, complaint_id: str | int) -> bytes:
    return PDFService.generate_report_d1_d3(db, complaint_id)


def generate_report_d1_to_d5(db: Session, complaint_id: str | int) -> bytes:
    return PDFService.generate_report_d1_to_d5(db, complaint_id)
