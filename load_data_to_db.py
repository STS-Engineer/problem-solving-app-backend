import os
import re
import json
import uuid
import time
import hashlib
import random
import string
import mimetypes
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, date, timezone

import httpx
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import IntegrityError
from app.models.step_conversation import StepConversation
from app.models.complaint import Complaint
from app.models.report import Report
from app.models.report_step import ReportStep
from app.models.file import File
from app.models.step_file import StepFile
from app.models.enums import PlantEnum, ProductLineEnum
from app.services.file_storage import storage

load_dotenv()

# ============================================================================
# CONFIG
# ============================================================================

DATABASE_URL = "postgresql://administrationSTS:St%24%400987@avo-adb-002.postgres.database.azure.com:5432/problem-solving-8d-db"
DEFAULT_USER_ID = int(os.getenv("DEFAULT_IMPORT_USER_ID", "1"))
IMPORT_DIR = Path(os.getenv("MONDAY_EXPORTS_DIR", "monday_exports"))

GROUP_OPEN = "Open"
GROUP_CLOSE = "Close"
GROUP_CANCELLED = "Cancelled"
GROUP_DUPLICATE_CLOSE = "Duplicate of Close"

EXPECTED_FILES = {
    # "open": "open.json",
    "close": "close.json",
    # "cancelled": "cancelled.json",
    # "duplicate_of_close": "duplicate_of_close.json",
}
PRIORITY_MAPPING = {
    "CS2": "critical",
    "CS1": "high",
    "WR": "medium",
    "QUALITY ALERT": "low",
}
def complaint_priority_from_quality_issue(value: Any) -> str:
    text = coerce_to_text(value)
    if not text:
        return "normal"
    return PRIORITY_MAPPING.get(text.strip().upper(), "normal")
STEP_DEFS = [
    ("D1", "D1 - Establish the Team"),
    ("D2", "D2 - Describe the Problem"),
    ("D3", "D3 - Interim Containment Action"),
    ("D4", "D4 - Root Cause Analysis"),
    ("D5", "D5 - Choose Permanent Corrective Actions"),
    ("D6", "D6 - Implement Corrective Actions"),
    ("D7", "D7 - Prevent Recurrence"),
    ("D8", "D8 - Congratulate the Team / Closure"),
]

# Monday grouped legacy blocks → anchor terminal step
LEGACY_BLOCKS = {
    "D1 TO D3": {
        "step_code": "D3",
        "covers_steps": ["D1", "D2", "D3"],
        "file_column_ids": ["files__1"],
        "cost_column_ids": ["d1_to_d3_9__1"],
    },
    "D1 TO D5": {
        "step_code": "D5",
        "covers_steps": ["D4", "D5"],
        "file_column_ids": ["dup__of_d1_to_d3__1"],
        "cost_column_ids": ["d4_to_d5___1"],
    },
    "D1 TO D8": {
        "step_code": "D8",
        "covers_steps": ["D6", "D7", "D8"],
        "file_column_ids": ["file__1"],
        "cost_column_ids": ["d6_to_d8__1"],
    },
}

LLC_FILE_COLUMN_ID = "files9__1"
LLC_COST_COLUMN_ID = "llc___1"

# ============================================================================
# DB SESSION
# ============================================================================

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


# ============================================================================
# ID GENERATORS
# ============================================================================

def generate_complaint_number() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    year = datetime.now().year
    return f"CMP-{year}-{suffix}"


def generate_report_number() -> str:
    return f"8D-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def generate_unique_complaint_number(db: Session, max_attempts: int = 20) -> str:
    for _ in range(max_attempts):
        value = generate_complaint_number()
        exists = db.query(Complaint.id).filter(Complaint.reference_number == value).first()
        if not exists:
            return value
    raise RuntimeError("Could not generate unique complaint reference number")


def generate_unique_report_number(db: Session, max_attempts: int = 20) -> str:
    for _ in range(max_attempts):
        value = generate_report_number()
        exists = db.query(Report.id).filter(Report.report_number == value).first()
        if not exists:
            return value
        time.sleep(1)
    raise RuntimeError("Could not generate unique report number")

def coerce_to_text(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        for key in ("display_text", "text", "label", "name", "value"):
            v = value.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                name = item.get("name") or item.get("label") or item.get("text")
                if name:
                    parts.append(str(name).strip())
            elif item is not None:
                parts.append(str(item).strip())
        return ", ".join([p for p in parts if p]) or None
    return str(value).strip()
# ============================================================================
# NORMALIZERS
# ============================================================================

def slug_email_name(value: str) -> str:
    import unicodedata

    value = (value or "").strip()
    if not value:
        return ""

    value = unicodedata.normalize("NFD", value)
    value = "".join(c for c in value if unicodedata.category(c) != "Mn")
    parts = re.split(r"[\s\-_/]+", value.strip())
    parts = [p.lower() for p in parts if p]

    if not parts:
        return ""

    if len(parts) == 1:
        return parts[0]

    return f"{parts[0]}.{parts[-1]}"


def person_display_to_email(display_text: Optional[Any]) -> Optional[str]:
    text = coerce_to_text(display_text)
    if not text:
        return None

    if "@" in text:
        return text.lower()

    return f"{slug_email_name(text)}@avocarbon.com"

def normalize_plant(value: Optional[Any]) -> Optional[PlantEnum]:
    text = coerce_to_text(value)
    if not text:
        return None

    normalized = text.upper()
    aliases = {
        "DAUGU": "DAEGU",
        "DAEGU": "DAEGU",
    }
    normalized = aliases.get(normalized, normalized)

    for enum_val in PlantEnum:
        if enum_val.value == normalized:
            return enum_val

    for enum_val in PlantEnum:
        if enum_val.value in normalized or normalized in enum_val.value:
            return enum_val

    return None

def normalize_product_line(value: Optional[Any]) -> Optional[ProductLineEnum]:
    text = coerce_to_text(value)
    if not text:
        return None

    normalized = text.upper()

    for enum_val in ProductLineEnum:
        if enum_val.value == normalized:
            return enum_val

    for enum_val in ProductLineEnum:
        if enum_val.value in normalized or normalized in enum_val.value:
            return enum_val

    return None

def parse_date(value: Any) -> Optional[date]:
    if value in (None, ""):
        return None

    if isinstance(value, date) and not isinstance(value, datetime):
        return value

    value = str(value).strip()

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%B %d, %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass

    try:
        return datetime.fromisoformat(value.replace("Z", "")).date()
    except ValueError:
        return None


def to_int_or_none(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return int(float(value))
    except Exception:
        return None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ============================================================================
# JSON HELPERS
# ============================================================================

def load_json_file(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def get_group_title(payload: Dict[str, Any]) -> str:
    group = payload.get("group")
    if isinstance(group, dict):
        return group.get("title", "")
    return payload.get("group", "")


def get_columns_by_id(item: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    data = item.get("columns_by_id") or {}
    return data if isinstance(data, dict) else {}


def get_column(item: Dict[str, Any], column_id: str) -> Optional[Dict[str, Any]]:
    return get_columns_by_id(item).get(column_id)


def get_column_normalized(item: Dict[str, Any], column_id: str) -> Any:
    col = get_column(item, column_id)
    if not col:
        return None
    return col.get("normalized_value")


def get_column_text(item: Dict[str, Any], column_id: str) -> Optional[str]:
    col = get_column(item, column_id)
    if not col:
        return None
    return col.get("text")


def get_column_files(item: Dict[str, Any], column_id: str) -> List[Dict[str, Any]]:
    col = get_column(item, column_id)
    if not col:
        return []
    return col.get("files") or []


def get_people_display(item: Dict[str, Any], column_id: str) -> Optional[str]:
    value = get_column_normalized(item, column_id)
    return coerce_to_text(value) or coerce_to_text(get_column_text(item, column_id))

# ============================================================================
# IMPORT STATUS / GROUP RULES
# ============================================================================

def complaint_status_from_group(group_title: str, item: Dict[str, Any]) -> str:
    group_title_l = (group_title or "").strip().lower()

    if group_title_l == GROUP_CANCELLED.lower():
        return "cancelled"

    if group_title_l in {GROUP_CLOSE.lower(), GROUP_DUPLICATE_CLOSE.lower()}:
        return "closed"

    # Open group logic:
    # if D1 TO D3 file column is not empty, complaint is already in progress
    if get_column_files(item, "files__1"):
        return "in_progress"

    return "open"
def should_create_report(group_title: str) -> bool:
    return (group_title or "").strip().lower() != GROUP_CANCELLED.lower()


def report_status_from_group(group_title: str) -> str:
    group_title = (group_title or "").strip().lower()
    if group_title in {GROUP_CLOSE.lower(), GROUP_DUPLICATE_CLOSE.lower()}:
        return "approved"
    return "in_progress"


def default_step_status(group_title: str, step_code: str, item: Dict[str, Any]) -> str:
    group_title_l = (group_title or "").strip().lower()

    if group_title_l in {GROUP_CLOSE.lower(), GROUP_DUPLICATE_CLOSE.lower()}:
        return "fulfilled"

    if group_title_l == GROUP_CANCELLED.lower():
        return "not_started"

    has_d1_d3 = bool(get_column_files(item, "files__1"))
    has_d1_d5 = bool(get_column_files(item, "dup__of_d1_to_d3__1"))
    has_d1_d8 = bool(get_column_files(item, "file__1"))

    # Highest completed block wins
    if has_d1_d8:
        return "fulfilled"

    if has_d1_d5 and step_code in {"D1", "D2", "D3", "D4", "D5"}:
        return "fulfilled"

    if has_d1_d3 and step_code in {"D1", "D2", "D3"}:
        return "fulfilled"

    return "not_started"
# ============================================================================
# DUPLICATE DETECTION
# NOTE:
# No monday_item_id column exists in Complaint, so this is a heuristic.
# ============================================================================

def find_existing_complaint(
    db: Session,
    complaint_name: Optional[str],
    customer: Optional[str],
    opening_date: Optional[date],
) -> Optional[Complaint]:
    q = db.query(Complaint)

    if complaint_name:
        q = q.filter(Complaint.complaint_name == complaint_name)

    if customer:
        q = q.filter(Complaint.customer == customer)

    if opening_date:
        q = q.filter(Complaint.complaint_opening_date == opening_date)

    return q.first()


# ============================================================================
# FILE INGESTION
# ============================================================================

async def upload_bytes_to_github(content: bytes, original_name: str, mime_type: str) -> Dict[str, str]:
    return await storage.upload(content=content, original_name=original_name, mime_type=mime_type)


def infer_filename(file_meta: Dict[str, Any], url: str) -> str:
    name = file_meta.get("name")
    if name:
        return name

    candidate = url.split("?")[0].rstrip("/").split("/")[-1]
    if candidate:
        return candidate

    return f"{uuid.uuid4().hex}.bin"


def infer_mime_type(filename: str, response_headers: Dict[str, str]) -> str:
    content_type = response_headers.get("content-type")
    if content_type:
        return content_type.split(";")[0].strip()
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def download_file_bytes(url: str) -> Tuple[bytes, Dict[str, str]]:
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.content, dict(r.headers)


def create_file_and_link(
    db: Session,
    report_step: ReportStep,
    monday_file: Dict[str, Any],
    uploaded_by: int,
) -> Optional[File]:
    url = monday_file.get("public_url") or monday_file.get("url")
    if not url:
        return None

    original_name = infer_filename(monday_file, url)

    try:
        content, headers = download_file_bytes(url)
    except Exception as exc:
        print(f"    ⚠️ Could not download Monday file: {url} ({exc})")
        return None

    mime_type = infer_mime_type(original_name, headers)
    size_bytes = len(content)
    checksum = sha256_hex(content)

    try:
        upload_result = asyncio.run(
            upload_bytes_to_github(content=content, original_name=original_name, mime_type=mime_type)
        )
    except Exception as exc:
        print(f"    ⚠️ GitHub upload failed for {original_name}: {exc}")
        return None

    stored_name = upload_result["stored_name"]

    existing = db.query(File).filter(File.stored_path == stored_name).first()
    if existing:
        file_row = existing
    else:
        file_row = File(
            purpose="evidence",
            original_name=original_name,
            stored_path=stored_name,
            size_bytes=size_bytes,
            mime_type=mime_type,
            uploaded_by=uploaded_by,
            checksum=checksum,
        )
        db.add(file_row)
        db.flush()

    existing_link = (
        db.query(StepFile)
        .filter(
            StepFile.report_step_id == report_step.id,
            StepFile.file_id == file_row.id,
        )
        .first()
    )
    if not existing_link:
        db.add(
            StepFile(
                report_step_id=report_step.id,
                file_id=file_row.id,
                attachment_order=0,
                description=f"Imported from Monday ({url})",
            )
        )

    return file_row


# ============================================================================
# LEGACY STEP DATA
# ============================================================================

def build_legacy_step_data(
    *,
    item: Dict[str, Any],
    group_title: str,
    source_scope: str,
    covers_steps: List[str],
    file_column_id: Optional[str],
    cost_column_id: Optional[str],
    monday_files: List[Dict[str, Any]],
    cost_value: Optional[int],
    imported_file_rows: List[File],
) -> Dict[str, Any]:
    return {
        "source": "monday_import",
        "source_group": group_title,
        "monday_item_id": item.get("item_id"),
        "monday_item_name": item.get("item_name"),
        "legacy_scope": source_scope,
        "block_covers_steps": covers_steps,
        "imported_at": utcnow().isoformat(),
        "legacy_columns": {
            "file_column_id": file_column_id,
            "cost_column_id": cost_column_id,
        },
        "legacy_cost": cost_value,
        "source_urls": [
            f.get("public_url") or f.get("url")
            for f in monday_files
            if f.get("public_url") or f.get("url")
        ],
        "imported_files": [
            {
                "file_id": f.id,
                "stored_path": f.stored_path,
                "original_name": f.original_name,
                "mime_type": f.mime_type,
            }
            for f in imported_file_rows
        ],
        "notes": "Legacy grouped data imported from Monday. No per-step structured form/AI content was fabricated.",
    }


# ============================================================================
# COMPLAINT MAPPING
# ============================================================================

def map_complaint_fields(item: Dict[str, Any], group_title: str) -> Dict[str, Any]:
    complaint_name = item.get("item_name") or "Unnamed complaint"

    customer_complaint_date = parse_date(get_column_normalized(item, "date0__1"))
    complaint_opening_date = parse_date(get_column_normalized(item, "date__1")) or date.today()

    plant_raw = get_column_normalized(item, "avo_plant___1") or get_column_text(item, "avo_plant___1")
    product_line_raw = get_column_normalized(item, "product_line___1") or get_column_text(item, "product_line___1")

    q_manager_display = get_people_display(item, "people__1")
    p_manager_display = get_people_display(item, "people_1__1")

    customer_value = get_column_normalized(item, "connect_boards__1")
    if isinstance(customer_value, list):
        customer = ", ".join(v.get("name", "") for v in customer_value if v.get("name"))
    else:
        customer = get_column_text(item, "connect_boards__1") or None

    quality_issue_value = get_column_normalized(item, "status__1") or get_column_text(item, "status__1")

    return {
        "complaint_name": complaint_name,
        "quality_issue_warranty": quality_issue_value,
        "customer": customer,
        "customer_plant_name": get_column_normalized(item, "text__1") or get_column_text(item, "text__1"),
        "customer_complaint_date": customer_complaint_date,
        "avocarbon_plant": normalize_plant(plant_raw),
        "avocarbon_product_type": get_column_normalized(item, "color3__1") or get_column_text(item, "color3__1"),
        "potential_avocarbon_process_linked_to_problem": get_column_normalized(item, "color7__1") or get_column_text(item, "color7__1"),
        "concerned_application": get_column_normalized(item, "color__1") or get_column_text(item, "color__1"),
        "product_line": normalize_product_line(product_line_raw),
        "complaint_opening_date": complaint_opening_date,
        "complaint_description": get_column_normalized(item, "long_text__1") or get_column_text(item, "long_text__1"),
        "defects": get_column_normalized(item, "status_1__1") or get_column_text(item, "status_1__1"),
        "repetitive_complete_with_number": str(get_column_normalized(item, "numbers__1")) if get_column_normalized(item, "numbers__1") not in (None, "") else None,
        "status": complaint_status_from_group(group_title, item),
        "priority": complaint_priority_from_quality_issue(quality_issue_value),
        "reported_by": DEFAULT_USER_ID,
        "quality_manager_email": person_display_to_email(q_manager_display),
        "plant_manager_email": person_display_to_email(p_manager_display),
        "approved_by_email": None,
        "cqt_email": None,
    }

# ============================================================================
# CREATE ENTITIES
# ============================================================================

def create_complaint(db: Session, item: Dict[str, Any], group_title: str) -> Complaint:
    mapped = map_complaint_fields(item, group_title)

    existing = find_existing_complaint(
        db=db,
        complaint_name=mapped["complaint_name"],
        customer=mapped["customer"],
        opening_date=mapped["complaint_opening_date"],
    )
    if existing:
        return existing

    complaint = Complaint(
        reference_number=generate_unique_complaint_number(db),
        **mapped,
    )

    if mapped["status"] == "closed":
        complaint.closed_at = utcnow()

    db.add(complaint)
    db.flush()
    return complaint


def create_report(db: Session, complaint: Complaint, group_title: str) -> Report:
    if complaint.report:
        return complaint.report

    report = Report(
        complaint_id=complaint.id,
        report_number=generate_unique_report_number(db),
        title=f"8D Report - {complaint.complaint_name}",
        summary="Imported from Monday legacy data",
        plant=complaint.avocarbon_plant,
        created_by=DEFAULT_USER_ID,
        reviewed_by=DEFAULT_USER_ID if complaint.status == "closed" else None,
        status=report_status_from_group(group_title),
        created_at=utcnow(),
        updated_at=utcnow(),
        submitted_at=utcnow() if complaint.status == "closed" else None,
        approved_at=utcnow() if complaint.status == "closed" else None,
    )
    db.add(report)
    db.flush()
    return report


def create_steps(db: Session, report: Report, group_title: str, item: Dict[str, Any]) -> Dict[str, ReportStep]:
    existing_steps = {s.step_code: s for s in report.steps}
    out: Dict[str, ReportStep] = dict(existing_steps)

    complaint_status = complaint_status_from_group(group_title, item)

    for step_code, step_name in STEP_DEFS:
        if step_code in out:
            continue

        step_status = default_step_status(group_title, step_code, item)

        row = ReportStep(
            report_id=report.id,
            step_code=step_code,
            step_name=step_name,
            status=step_status,
            data=None,
            completed_by=DEFAULT_USER_ID if complaint_status == "closed" else None,
            completed_at=utcnow() if complaint_status == "closed" else None,
            created_at=utcnow(),
            updated_at=utcnow(),
            cost=None,
        )
        db.add(row)
        db.flush()
        out[step_code] = row

    return out

# ============================================================================
# APPLY LEGACY BLOCKS TO STEPS
# ============================================================================

def apply_legacy_blocks(
    db: Session,
    item: Dict[str, Any],
    group_title: str,
    report: Report,
    steps: Dict[str, ReportStep],
) -> None:
    for scope_name, cfg in LEGACY_BLOCKS.items():
        step = steps[cfg["step_code"]]

        monday_files: List[Dict[str, Any]] = []
        for file_col_id in cfg["file_column_ids"]:
            monday_files.extend(get_column_files(item, file_col_id))

        cost_value = None
        for cost_col_id in cfg["cost_column_ids"]:
            cost_value = to_int_or_none(get_column_normalized(item, cost_col_id))
            if cost_value is not None:
                break

        if cost_value is not None:
            step.cost = cost_value

        imported_file_rows: List[File] = []
        file_column_id = cfg["file_column_ids"][0] if cfg["file_column_ids"] else None
        cost_column_id = cfg["cost_column_ids"][0] if cfg["cost_column_ids"] else None

        for monday_file in monday_files:
            file_row = create_file_and_link(
                db=db,
                report_step=step,
                monday_file=monday_file,
                uploaded_by=DEFAULT_USER_ID,
            )
            if file_row:
                imported_file_rows.append(file_row)

        step.data = build_legacy_step_data(
            item=item,
            group_title=group_title,
            source_scope=scope_name,
            covers_steps=cfg["covers_steps"],
            file_column_id=file_column_id,
            cost_column_id=cost_column_id,
            monday_files=monday_files,
            cost_value=cost_value,
            imported_file_rows=imported_file_rows,
        )
        step.updated_at = utcnow()

    # LLC handling: attach as extra closure evidence on D8
    d8 = steps["D8"]
    llc_files = get_column_files(item, LLC_FILE_COLUMN_ID)
    llc_cost = to_int_or_none(get_column_normalized(item, LLC_COST_COLUMN_ID))

    imported_llc_rows: List[File] = []
    for monday_file in llc_files:
        file_row = create_file_and_link(
            db=db,
            report_step=d8,
            monday_file=monday_file,
            uploaded_by=DEFAULT_USER_ID,
        )
        if file_row:
            imported_llc_rows.append(file_row)

    existing_data = d8.data or {}
    if not isinstance(existing_data, dict):
        existing_data = {"legacy_payload": existing_data}

    existing_data["llc"] = {
        "cost": llc_cost,
        "source_urls": [
            f.get("public_url") or f.get("url")
            for f in llc_files
            if f.get("public_url") or f.get("url")
        ],
        "imported_files": [
            {
                "file_id": f.id,
                "stored_path": f.stored_path,
                "original_name": f.original_name,
                "mime_type": f.mime_type,
            }
            for f in imported_llc_rows
        ],
    }
    d8.data = existing_data
    d8.updated_at = utcnow()


# ============================================================================
# IMPORT ONE ITEM
# ============================================================================

def import_item(db: Session, item: Dict[str, Any], payload_group_title: str) -> Complaint:
    complaint = create_complaint(db, item, payload_group_title)

    if not should_create_report(payload_group_title):
        return complaint

    report = create_report(db, complaint, payload_group_title)
    steps = create_steps(db, report, payload_group_title, item)
    apply_legacy_blocks(db, item, payload_group_title, report, steps)

    return complaint


# ============================================================================
# IMPORT FILES
# ============================================================================

def resolve_input_files(import_dir: Path) -> List[Path]:
    paths: List[Path] = []
    for _, filename in EXPECTED_FILES.items():
        path = import_dir / filename
        if path.exists():
            paths.append(path)

    if not paths:
        paths = sorted(import_dir.glob("*.json"))

    return paths


# ============================================================================
# MAIN IMPORT
# ============================================================================

def run_import(import_dir: Path = IMPORT_DIR) -> None:
    files = resolve_input_files(import_dir)
    if not files:
        raise FileNotFoundError(f"No JSON files found in {import_dir}")

    print(f"🚀 Starting DB import from: {import_dir}")
    print(f"📄 Found {len(files)} JSON files")

    db = SessionLocal()

    created_or_found = 0
    errors = 0

    try:
        for file_path in files:
            payload = load_json_file(file_path)
            group_title = get_group_title(payload)
            items = payload.get("items", [])

            print(f"\n📂 File: {file_path.name} | Group: {group_title} | Items: {len(items)}")

            for idx, item in enumerate(items, start=1):
                item_name = item.get("item_name", "Unnamed")
                item_id = item.get("item_id")

                print(f"[{idx}/{len(items)}] Importing: {item_name} (Monday ID: {item_id})")

                try:
                    complaint = import_item(db, item, group_title)
                    db.commit()
                    created_or_found += 1
                    print(f"   ✅ Complaint imported/found: {complaint.reference_number}")
                except IntegrityError as exc:
                    db.rollback()
                    errors += 1
                    print(f"   ❌ IntegrityError: {exc}")
                except Exception as exc:
                    db.rollback()
                    errors += 1
                    print(f"   ❌ Error: {exc}")

        print("\n" + "=" * 60)
        print("📊 IMPORT SUMMARY")
        print("=" * 60)
        print(f"✅ Imported/Found: {created_or_found}")
        print(f"❌ Errors        : {errors}")
        print("=" * 60)

    finally:
        db.close()


# ============================================================================
# CLI
# ============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Load Monday export JSON files into DB")
    parser.add_argument(
        "--import-dir",
        type=str,
        default=str(IMPORT_DIR),
        help=f"Directory containing Monday export JSON files (default: {IMPORT_DIR})",
    )

    args = parser.parse_args()

    try:
        run_import(import_dir=Path(args.import_dir))
    except KeyboardInterrupt:
        print("\n⚠️ Interrupted by user")
        raise SystemExit(1)
    except Exception as exc:
        print(f"\n❌ Import failed: {exc}")
        raise SystemExit(1)