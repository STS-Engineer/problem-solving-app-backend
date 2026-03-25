# def get_all_items_by_group(self, group_title: str) -> list:
#         all_items = []
#         cursor = None

#         while True:
#             cursor_part = f', cursor: "{cursor}"' if cursor else ""
#             query = f"""
#             query {{
#                 boards(ids: [{self.board_id}]) {{
#                     groups {{
#                         id
#                         title
#                         items_page(limit: 50{cursor_part}) {{
#                             cursor
#                             items {{
#                                 id
#                                 name
#                                 group {{ id title }}
#                                 created_at
#                                 updated_at
#                                 column_values {{
#                                     id
#                                     column {{ title }}
#                                     text
#                                     value
#                                     ... on BoardRelationValue {{
#                                         linked_item_ids
#                                         linked_items {{ id name }}
#                                     }}
#                                     ... on FileValue {{
#                 files {{
#                     ... on FileAssetValue {{
#                         asset_id
#                         name
#                         is_image
#                         created_at
#                         asset {{
#                             id
#                             url
#                             name
#                             public_url
#                         }}
#                     }}
#                     ... on FileLinkValue {{
#                         file_id
#                         name
#                         url
#                         kind
#                         created_at
#                     }}
#                     ... on FileDocValue {{
#                         file_id
#                         url
#                         created_at
#                     }}
#                                 }}
#                                 }}}}
#                             }}
#                         }}
#                     }}
#                 }}
#             }}
#             """
#             result = self._execute_query(query)
#             boards = result.get("data", {}).get("boards", [])
#             if not boards:
#                 break

#             cursor_found = False
#             for group in boards[0].get("groups", []):
#                 if group.get("title", "").lower() == group_title.lower():
#                     items_page = group.get("items_page", {})
#                     all_items.extend(items_page.get("items", []))
#                     cursor = items_page.get("cursor")
#                     cursor_found = True
#                     break

#             if not cursor_found or not cursor:
#                 break

#         return all_items
"""
Script to sync complaints from Monday.com board → JSON file (for visualization)
DB storage is commented out. Run this to extract data, then open complaints_viz.html.

UPDATES:
- Fixed column mapping to match actual Monday.com board structure
- Updated PlantEnum mapping to match database schema
- Updated ProductLineEnum mapping to match database schema
- Improved error handling and data validation
- ** DB STORAGE COMMENTED OUT — data is saved to complaints_data.json instead **
"""

import os
import sys
import json
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime, date

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

load_dotenv()

# ─── DB imports commented out ────────────────────────────────────────────────
# from sqlalchemy import create_engine
# from sqlalchemy.orm import sessionmaker, Session
# from app.models.complaint import Complaint
# from app.models.enums import PlantEnum, ProductLineEnum
# from app.db.base import Base
# ─────────────────────────────────────────────────────────────────────────────


# Lightweight enum stubs so mapping dicts still work without the app installed
class PlantEnum:
    MONTERREY = type("E", (), {"value": "MONTERREY"})()
    KUNSHAN = type("E", (), {"value": "KUNSHAN"})()
    CHENNAI = type("E", (), {"value": "CHENNAI"})()
    DAUGU = type("E", (), {"value": "DAUGU"})()
    TIANJIN = type("E", (), {"value": "TIANJIN"})()
    POITIERS = type("E", (), {"value": "POITIERS"})()
    FRANKFURT = type("E", (), {"value": "FRANKFURT"})()
    SCEET = type("E", (), {"value": "SCEET"})()
    SAME = type("E", (), {"value": "SAME"})()
    AMIENS = type("E", (), {"value": "AMIENS"})()
    ANHUI = type("E", (), {"value": "ANHUI"})()
    KOREA = type("E", (), {"value": "KOREA"})()


class ProductLineEnum:
    ASSEMBLY = type("E", (), {"value": "ASSEMBLY"})()
    BRUSH = type("E", (), {"value": "BRUSH"})()
    CHOKE = type("E", (), {"value": "CHOKE"})()
    SEAL = type("E", (), {"value": "SEAL"})()
    FRICTION = type("E", (), {"value": "FRICTION"})()


# ============================================================================
# MONDAY.COM CONFIGURATION
# ============================================================================

MONDAY_API_TOKEN = os.getenv("MONDAY_API_TOKEN")
MONDAY_API_URL = "https://api.monday.com/v2"
MONDAY_BOARD_ID = os.getenv("BOARD_ID", "6514087061")

COLUMN_MAPPING: Dict[str, str] = {
    "AVO PLANT": "avocarbon_plant",
    "PRODUCT LINE": "product_line",
    "AVO PROCESS LINKED WITH THE PROBLEM": "potential_avocarbon_process_linked_to_problem",
    "Quality issue / Warranty": "quality_issue_warranty",
    "Defects": "defects",
    "REPETITIVE complete with the number (0: non repetitive, 1: 1 time repetitive, 2: 2 times repeated, etc)": "repetitive_complete_with_number",
    "Quality Manager": "quality_manager_email",
    "PM": "plant_manager_email",
    "Customers": "customer",
    "CUSTOMER PLANT NAME": "customer_plant_name",
    "Status": "status",
    "COMPLAINT OPENING DATE": "complaint_opening_date",
    "COMPLAINT DESCRIPTION": "complaint_description",
    "AVO  PART DESCRIPTION": "avocarbon_product_type",
    "CUSTOMER APPLICATION": "concerned_application",
    "CUSTOMER COMPLAINT DATE": "customer_complaint_date",
    "D1 TO D3": "D1-D3",  # file/asset column
    "D1 TO D5": "D1-D5",
    "D1 TO D8": "D1-D8",
    "D1 TO D3": "cost D1D3",
    "D4 TO D5": "cost D4D5",
    "D6 TO D8": "cost D6D8",
    "Estimated LLC cost": "estimated_llc_cost",
    "Other Auto Maker": "other_auto_maker",
}

GROUP_OPEN = "Open"
GROUP_CLOSED = "Close"

PLANT_MAPPING = {
    "MONTERREY": PlantEnum.MONTERREY.value,
    "KUNSHAN": PlantEnum.KUNSHAN.value,
    "CHENNAI": PlantEnum.CHENNAI.value,
    "DAUGU": PlantEnum.DAUGU.value,
    "TIANJIN": PlantEnum.TIANJIN.value,
    "POITIERS": PlantEnum.POITIERS.value,
    "FRANKFURT": PlantEnum.FRANKFURT.value,
    "SCEET": PlantEnum.SCEET.value,
    "SAME": PlantEnum.SAME.value,
    "AMIENS": PlantEnum.AMIENS.value,
    "ANHUI": PlantEnum.ANHUI.value,
    "KOREA": PlantEnum.KOREA.value,
}

PRODUCT_LINE_MAPPING = {
    "ASSEMBLY": ProductLineEnum.ASSEMBLY.value,
    "BRUSH": ProductLineEnum.BRUSH.value,
    "CHOKE": ProductLineEnum.CHOKE.value,
    "SEAL": ProductLineEnum.SEAL.value,
    "FRICTION": ProductLineEnum.FRICTION.value,
}


# ============================================================================
# MONDAY.COM SERVICE
# ============================================================================

import requests


class MondayService:
    def __init__(self):
        if not MONDAY_API_TOKEN:
            raise ValueError("MONDAY_API_TOKEN is not set in environment variables")
        self.api_url = MONDAY_API_URL
        self.headers = {
            "Authorization": MONDAY_API_TOKEN,
            "Content-Type": "application/json",
        }
        self.board_id = MONDAY_BOARD_ID

    def _execute_query(self, query: str) -> Dict:
        response = requests.post(
            self.api_url, json={"query": query}, headers=self.headers
        )
        if response.status_code != 200:
            raise Exception(
                f"Monday API Error: {response.status_code} - {response.text}"
            )
        return response.json()

    def get_all_items_by_group(self, group_title: str) -> list:
        all_items = []
        cursor = None

        while True:
            cursor_part = f', cursor: "{cursor}"' if cursor else ""
            query = f"""
            query {{
                boards(ids: [{self.board_id}]) {{
                    groups {{
                        id
                        title
                        items_page(limit: 50{cursor_part}) {{
                            cursor
                            items {{
                                id
                                name
                                group {{ id title }}
                                created_at
                                updated_at
                                column_values {{
                                    id
                                    column {{ title }}
                                    text
                                    value
                                    ... on BoardRelationValue {{
                                        linked_item_ids
                                        linked_items {{ id name }}
                                    }}
                                    ... on FileValue {{
                                        files {{
                                            ... on FileAssetValue {{
                                                asset {{ url public_url }}
                                            }}
                                        }}
                                    }}
                                }}
                            }}
                        }}
                    }}
                }}
            }}
            """
            result = self._execute_query(query)
            boards = result.get("data", {}).get("boards", [])
            if not boards:
                break

            cursor_found = False
            for group in boards[0].get("groups", []):
                if group.get("title", "").lower() == group_title.lower():
                    items_page = group.get("items_page", {})
                    all_items.extend(items_page.get("items", []))
                    cursor = items_page.get("cursor")
                    cursor_found = True
                    break

            if not cursor_found or not cursor:
                break

        return all_items

    def transform_item_to_complaint_data(self, item: Dict) -> Dict:
        complaint_data = {"complaint_name": item.get("name", "")}

        for column_value in item.get("column_values", []):
            column_title = column_value.get("column", {}).get("title", "")
            text_value = column_value.get("text")
            value_json = column_value.get("value")
            linked_items = column_value.get("linked_items", [])
            files = column_value.get("files", [])

            db_field = COLUMN_MAPPING.get(column_title)
            if db_field:
                extracted = self._extract_column_value(
                    text_value,
                    value_json,
                    column_title,
                    linked_items=linked_items,
                    files=files,
                )
                if extracted:
                    if db_field in (
                        "complaint_opening_date",
                        "customer_complaint_date",
                    ):
                        parsed = self._parse_monday_date(extracted)
                        complaint_data[db_field] = (
                            parsed.isoformat() if parsed else None
                        )
                    elif db_field == "avocarbon_plant":
                        complaint_data[db_field] = self._map_to_plant_enum(extracted)
                    elif db_field == "product_line":
                        complaint_data[db_field] = self._map_to_product_line_enum(
                            extracted
                        )
                    elif db_field in ("quality_manager_email", "plant_manager_email"):
                        # Value may be a plain name ("Jean Dupont") or already an email
                        complaint_data[db_field] = self._name_to_email(extracted)
                    else:
                        complaint_data[db_field] = extracted

        group_title = item.get("group", {}).get("title", "")
        if group_title.lower() == GROUP_CLOSED.lower():
            complaint_data["status"] = "closed"
        else:
            complaint_data["status"] = complaint_data.get("status", "open")

        return complaint_data

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _name_to_email(value: str) -> Optional[str]:
        """
        Convert a person name to an avocarbon.com email address.

        Handles both orderings:
          "Jean Dupont"   → jean.dupont@avocarbon.com
          "Dupont Jean"   → jean.dupont@avocarbon.com   (heuristic: title-case last word = surname)

        Already-formatted emails are returned as-is (lowercased).
        """
        if not value:
            return None
        value = value.strip()

        # Already an email address — just normalise case
        if "@" in value:
            return value.lower()

        # Strip accidental extra whitespace and split
        parts = value.split()
        if len(parts) == 0:
            return None
        if len(parts) == 1:
            # Single token — use it as-is
            return f"{parts[0].lower()}@avocarbon.com"

        # Two or more tokens — treat as "Firstname Lastname"
        # Normalise each part: lowercase, replace spaces/hyphens between compound names
        def slug(s: str) -> str:
            import unicodedata

            # Decompose accented chars then drop combining marks
            s = unicodedata.normalize("NFD", s)
            s = "".join(c for c in s if unicodedata.category(c) != "Mn")
            return s.lower()

        firstname = slug(parts[0])
        lastname = slug(
            parts[-1]
        )  # last token = surname (works for "Jean Dupont" and "Dupont Jean" equally well when paired with firstname)
        return f"{firstname}.{lastname}@avocarbon.com"

    def _parse_monday_date(self, date_str: Optional[str]) -> Optional[date]:
        if not date_str:
            return None
        for fmt in ("%Y-%m-%d", "%B %d, %Y", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(date_str, fmt).date()
            except ValueError:
                pass
        try:
            return datetime.fromisoformat(date_str.replace("Z", "")).date()
        except ValueError:
            pass
        print(f"  ⚠️  Could not parse date: {date_str}")
        return None

    def _map_to_plant_enum(self, value: str) -> Optional[str]:
        if not value:
            return None
        normalized = value.upper().strip()
        if normalized in PLANT_MAPPING:
            return PLANT_MAPPING[normalized]
        for key in PLANT_MAPPING:
            if key in normalized or normalized in key:
                return PLANT_MAPPING[key]
        print(f"  ⚠️  Unknown plant value: '{value}'")
        return None

    def _map_to_product_line_enum(self, value: str) -> Optional[str]:
        if not value:
            return None
        normalized = value.upper().strip()
        if normalized in PRODUCT_LINE_MAPPING:
            return PRODUCT_LINE_MAPPING[normalized]
        for key in PRODUCT_LINE_MAPPING:
            if key in normalized or normalized in key:
                return PRODUCT_LINE_MAPPING[key]
        print(f"  ⚠️  Unknown product line value: '{value}'")
        return None

    def _extract_column_value(
        self,
        text_value: Optional[str],
        value_json: Optional[str],
        column_title: str,
        linked_items: Optional[list] = None,
        files: Optional[list] = None,
    ) -> Optional[str]:
        if files:
            urls = [f.get("url") or (f.get("asset") or {}).get("url") for f in files]
            urls = [u for u in urls if u]
            if urls:
                return ", ".join(urls)

        if linked_items:
            names = [i.get("name") for i in linked_items if i.get("name")]
            if names:
                return ", ".join(names)

        if text_value and text_value.strip():
            return text_value.strip()

        return None


# ============================================================================
# FILE-BASED EXTRACTION  (replaces DB storage)
# ============================================================================

# ── DB session helper — commented out ────────────────────────────────────────
# def get_database_session() -> Session:
#     db_url = os.getenv("DATABASE_URL")
#     if not db_url:
#         raise RuntimeError("DATABASE_URL is not set")
#     if "sslmode=" not in db_url:
#         sep = "&" if "?" in db_url else "?"
#         db_url = f"{db_url}{sep}sslmode=require"
#     engine = create_engine(db_url, pool_pre_ping=True)
#     Base.metadata.create_all(bind=engine)
#     SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
#     return SessionLocal()
# ─────────────────────────────────────────────────────────────────────────────


OUTPUT_FILE = Path(__file__).parent / "complaints_data.json"


def sync_monday_to_file(
    group_name: str = GROUP_OPEN,
    default_user_id: int = 1,
    dry_run: bool = False,
    output_file: Path = OUTPUT_FILE,
):
    """
    Fetch complaints from Monday.com and save them to a JSON file.
    No database writes are performed.

    Args:
        group_name:      Monday group to sync (default "Open")
        default_user_id: Stored in each record as reported_by
        dry_run:         Print data only, do not write file
        output_file:     Destination JSON path
    """
    print(f"🚀 Starting sync — board {MONDAY_BOARD_ID}")
    print(f"📂 Group: '{group_name}'")
    if dry_run:
        print("🔍 DRY RUN — file will NOT be written")

    monday_service = MondayService()

    # ── DB session commented out ──────────────────────────────────────────
    # db = get_database_session()
    # ─────────────────────────────────────────────────────────────────────

    try:
        print("\n📥 Fetching items from Monday.com…")
        items = monday_service.get_all_items_by_group(group_name)
        print(f"✅ Found {len(items)} items in '{group_name}'")

        if not items:
            print("⚠️  No items found. Exiting.")
            return

        records = []
        created_count = skipped_count = error_count = 0

        for idx, item in enumerate(items, 1):
            item_id = item.get("id")
            item_name = item.get("name", "Unnamed")
            print(f"\n[{idx}/{len(items)}] {item_name} (ID: {item_id})")

            try:
                data = monday_service.transform_item_to_complaint_data(item)
                data["reported_by"] = default_user_id
                data["monday_item_id"] = item_id  # keep traceability

                if not data.get("complaint_name"):
                    print("  ⚠️  Skipping — missing complaint_name")
                    skipped_count += 1
                    continue

                if not data.get("product_line"):
                    print("  ⚠️  Skipping — missing product_line")
                    skipped_count += 1
                    continue

                print("  📋 Extracted:")
                for k, v in data.items():
                    if v:
                        print(f"     • {k}: {v}")

                records.append(data)
                created_count += 1

                # ── DB write commented out ────────────────────────────────
                # if not dry_run:
                #     complaint = Complaint(**data)
                #     db.add(complaint)
                #     db.commit()
                #     db.refresh(complaint)
                #     print(f"  ✅ Saved to DB — ID: {complaint.id}")
                # ─────────────────────────────────────────────────────────

            except Exception as exc:
                import traceback

                print(f"  ❌ Error: {exc}")
                traceback.print_exc()
                error_count += 1

                # ── DB rollback commented out ─────────────────────────────
                # if not dry_run:
                #     db.rollback()
                # ─────────────────────────────────────────────────────────

        # ── Write JSON file ───────────────────────────────────────────────
        if not dry_run and records:
            payload = {
                "synced_at": datetime.utcnow().isoformat() + "Z",
                "board_id": MONDAY_BOARD_ID,
                "group": group_name,
                "count": len(records),
                "complaints": records,
            }
            with open(output_file, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
            print(f"\n💾 Data written → {output_file}")
        # ─────────────────────────────────────────────────────────────────

        print("\n" + "=" * 60)
        print("📊 SYNC SUMMARY")
        print("=" * 60)
        if dry_run:
            print("🔍 DRY RUN — no file written")
        print(f"✅ Extracted : {created_count}")
        print(f"⚠️  Skipped  : {skipped_count}")
        print(f"❌ Errors   : {error_count}")
        print(f"📝 Total    : {len(items)}")
        print("=" * 60)

    except Exception as exc:
        import traceback

        print(f"\n❌ Fatal error: {exc}")
        traceback.print_exc()

        # ── DB rollback commented out ─────────────────────────────────────
        # if not dry_run:
        #     db.rollback()
        # ─────────────────────────────────────────────────────────────────

    finally:
        # ── DB close commented out ────────────────────────────────────────
        # db.close()
        # ─────────────────────────────────────────────────────────────────
        print("\n✅ Done")


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Sync Monday.com board to JSON file (DB-free)"
    )
    parser.add_argument(
        "--group",
        type=str,
        default=GROUP_OPEN,
        help=f"Group name to sync (default: '{GROUP_OPEN}')",
    )
    parser.add_argument(
        "--user-id",
        type=int,
        default=1,
        help="Default user ID for reported_by field (default: 1)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview extracted data without writing file",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(OUTPUT_FILE),
        help=f"Output JSON path (default: {OUTPUT_FILE})",
    )

    args = parser.parse_args()

    try:
        sync_monday_to_file(
            group_name=args.group,
            default_user_id=args.user_id,
            dry_run=args.dry_run,
            output_file=Path(args.output),
        )
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted")
        sys.exit(1)
    except Exception as exc:
        print(f"\n\n❌ Sync failed: {exc}")
        sys.exit(1)
