import os
import sys
import json
import re
from pathlib import Path
from typing import Dict, Optional, Any, List
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

# ============================================================================
# CONFIG
# ============================================================================

MONDAY_API_TOKEN = os.getenv("MONDAY_API_TOKEN")
MONDAY_API_URL = "https://api.monday.com/v2"
MONDAY_BOARD_ID = os.getenv("BOARD_ID", "6514087061")

# DEFAULT_GROUPS = ["Open", "Close", "Cancelled", "Duplicate of Close"]
DEFAULT_GROUPS = ["Close"]
OUTPUT_DIR = Path(__file__).parent / "monday_exports"


# ============================================================================
# HELPERS
# ============================================================================


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "group"


# ============================================================================
# MONDAY SERVICE
# ============================================================================


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

    def _execute_query(self, query: str) -> Dict[str, Any]:
        response = requests.post(
            self.api_url,
            json={"query": query},
            headers=self.headers,
            timeout=60,
        )

        if response.status_code != 200:
            raise Exception(
                f"Monday API Error: {response.status_code} - {response.text}"
            )

        result = response.json()

        if "errors" in result:
            raise Exception(
                f"Monday GraphQL Error: {json.dumps(result['errors'], indent=2)}"
            )

        return result

    def get_board_columns(self) -> List[Dict[str, Any]]:
        query = f"""
        query {{
            boards(ids: [{self.board_id}]) {{
                columns {{
                    id
                    title
                    type
                }}
            }}
        }}
        """
        result = self._execute_query(query)
        boards = result.get("data", {}).get("boards", [])
        if not boards:
            return []
        return boards[0].get("columns", [])

    def get_board_groups(self) -> List[Dict[str, Any]]:
        query = f"""
        query {{
            boards(ids: [{self.board_id}]) {{
                groups {{
                    id
                    title
                }}
            }}
        }}
        """
        result = self._execute_query(query)
        boards = result.get("data", {}).get("boards", [])
        if not boards:
            return []
        return boards[0].get("groups", [])

    def find_groups_by_titles(self, group_titles: List[str]) -> List[Dict[str, Any]]:
        requested_titles = {g.strip().lower() for g in group_titles if g.strip()}
        board_groups = self.get_board_groups()

        matched_groups = [
            g
            for g in board_groups
            if (g.get("title") or "").strip().lower() in requested_titles
        ]
        return matched_groups

    def get_all_items_by_group_id(self, group_id: str) -> List[Dict[str, Any]]:
        all_items: List[Dict[str, Any]] = []
        cursor = None

        while True:
            cursor_part = f', cursor: "{cursor}"' if cursor else ""

            query = f"""
            query {{
                boards(ids: [{self.board_id}]) {{
                    groups(ids: "{group_id}") {{
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
                                    type
                                    text
                                    value
                                    column {{
                                        id
                                        title
                                        type
                                    }}
                                    ... on BoardRelationValue {{
                                        linked_item_ids
                                        linked_items {{ id name }}
                                    }}
                                    ... on FileValue {{
                                        files {{
                                            ... on FileAssetValue {{
                                                asset {{
                                                    id
                                                    name
                                                    url
                                                    public_url
                                                }}
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

            groups = boards[0].get("groups", [])
            if not groups:
                break

            items_page = groups[0].get("items_page", {})
            items = items_page.get("items", [])
            all_items.extend(items)

            cursor = items_page.get("cursor")
            if not cursor:
                break

        return all_items

    def transform_item_full(self, item: Dict[str, Any]) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "item_id": item.get("id"),
            "item_name": item.get("name"),
            "group": item.get("group"),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "columns": [],
        }

        columns_by_id: Dict[str, Any] = {}
        columns_by_title: Dict[str, List[Any]] = {}

        for column_value in item.get("column_values", []):
            transformed = self._transform_column_value(column_value)
            result["columns"].append(transformed)

            col_id = transformed.get("column_id")
            col_title = transformed.get("column_title")

            columns_by_id[col_id] = transformed

            if col_title not in columns_by_title:
                columns_by_title[col_title] = []
            columns_by_title[col_title].append(transformed)

        result["columns_by_id"] = columns_by_id
        result["columns_by_title"] = columns_by_title

        return result

    def _transform_column_value(self, column_value: Dict[str, Any]) -> Dict[str, Any]:
        column = column_value.get("column", {}) or {}

        column_id = column.get("id") or column_value.get("id")
        column_title = column.get("title", "")
        column_type = column.get("type") or column_value.get("type")
        text_value = column_value.get("text")
        raw_value = column_value.get("value")

        parsed_value = self._safe_json_loads(raw_value)
        linked_items = column_value.get("linked_items", []) or []
        linked_item_ids = column_value.get("linked_item_ids", []) or []
        files = column_value.get("files", []) or []

        normalized_value = self._normalize_column_value(
            column_type=column_type,
            text_value=text_value,
            parsed_value=parsed_value,
            linked_items=linked_items,
            files=files,
        )

        extracted_files = []
        for f in files:
            asset = f.get("asset") or {}
            extracted_files.append(
                {
                    "id": asset.get("id"),
                    "name": asset.get("name"),
                    "url": asset.get("url"),
                    "public_url": asset.get("public_url"),
                }
            )

        extracted_linked_items = [
            {
                "id": li.get("id"),
                "name": li.get("name"),
            }
            for li in linked_items
        ]

        return {
            "column_id": column_id,
            "column_title": column_title,
            "column_type": column_type,
            "text": text_value,
            "raw_value": raw_value,
            "parsed_value": parsed_value,
            "linked_item_ids": linked_item_ids,
            "linked_items": extracted_linked_items,
            "files": extracted_files,
            "normalized_value": normalized_value,
        }

    def _normalize_column_value(
        self,
        column_type: Optional[str],
        text_value: Optional[str],
        parsed_value: Any,
        linked_items: List[Dict[str, Any]],
        files: List[Dict[str, Any]],
    ) -> Any:
        if column_type == "file" or files:
            out = []
            for f in files:
                asset = f.get("asset") or {}
                out.append(
                    {
                        "id": asset.get("id"),
                        "name": asset.get("name"),
                        "url": asset.get("url"),
                        "public_url": asset.get("public_url"),
                    }
                )
            return out

        if column_type == "board_relation" or linked_items:
            return [
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                }
                for item in linked_items
            ]

        if column_type == "people":
            if isinstance(parsed_value, dict):
                persons = parsed_value.get("personsAndTeams")
                if persons is not None:
                    return persons
            return text_value

        if column_type == "date":
            if isinstance(parsed_value, dict) and parsed_value.get("date"):
                return parsed_value.get("date")
            return text_value

        if column_type == "numbers":
            if isinstance(parsed_value, dict):
                if parsed_value.get("number") is not None:
                    return parsed_value["number"]
                if parsed_value.get("value") is not None:
                    return parsed_value["value"]

            if text_value is not None:
                stripped = text_value.strip()
                if stripped == "":
                    return None
                try:
                    if "." in stripped:
                        return float(stripped)
                    return int(stripped)
                except ValueError:
                    return stripped

        if column_type in {
            "status",
            "dropdown",
            "text",
            "long_text",
            "formula",
            "name",
        }:
            if text_value is not None and text_value != "":
                return text_value
            return parsed_value

        if column_type in {
            "time_tracking",
            "subtasks",
            "creation_log",
            "last_updated",
            "item_id",
        }:
            if parsed_value is not None:
                return parsed_value
            return text_value

        if parsed_value is not None:
            return parsed_value

        if text_value is not None:
            return text_value

        return None

    @staticmethod
    def _safe_json_loads(value: Optional[str]) -> Any:
        if not value:
            return None
        try:
            return json.loads(value)
        except Exception:
            return value


# ============================================================================
# EXPORT
# ============================================================================


def export_each_group_to_separate_json(
    group_names: List[str],
    output_dir: Path = OUTPUT_DIR,
    include_board_columns: bool = True,
    include_board_groups: bool = True,
) -> None:
    service = MondayService()
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"🚀 Starting export from Monday board {MONDAY_BOARD_ID}")
    print(f"📂 Requested groups: {', '.join(group_names)}")

    matched_groups = service.find_groups_by_titles(group_names)
    matched_titles = [g.get("title", "") for g in matched_groups]

    missing_groups = [
        g for g in group_names if g.lower() not in {t.lower() for t in matched_titles}
    ]
    if missing_groups:
        print(f"⚠️ Groups not found: {', '.join(missing_groups)}")

    board_columns = service.get_board_columns() if include_board_columns else None
    board_groups = service.get_board_groups() if include_board_groups else None

    for group in matched_groups:
        group_id = group.get("id")
        group_title = group.get("title", "unknown_group")
        group_slug = slugify(group_title)
        output_file = output_dir / f"{group_slug}.json"

        print(f"\n📦 Exporting group: {group_title} (id={group_id})")

        items = service.get_all_items_by_group_id(group_id)
        print(f"✅ Found {len(items)} items in group '{group_title}'")

        exported_items = []
        error_count = 0

        for idx, item in enumerate(items, start=1):
            item_name = item.get("name", "Unnamed")
            item_id = item.get("id")
            print(f"[{idx}/{len(items)}] Processing: {item_name} (ID: {item_id})")

            try:
                exported_items.append(service.transform_item_full(item))
            except Exception as exc:
                print(f"  ❌ Error: {exc}")
                error_count += 1

        payload: Dict[str, Any] = {
            "exported_at": datetime.utcnow().isoformat() + "Z",
            "board_id": MONDAY_BOARD_ID,
            "group": {
                "id": group_id,
                "title": group_title,
            },
            "count": len(exported_items),
            "items": exported_items,
        }

        if board_columns is not None:
            payload["board_columns"] = board_columns

        if board_groups is not None:
            payload["board_groups"] = board_groups

        with open(output_file, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)

        print("------------------------------------------------------------")
        print(f"✅ Exported : {len(exported_items)}")
        print(f"❌ Errors   : {error_count}")
        print(f"💾 File     : {output_file}")
        print("------------------------------------------------------------")


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Export all Monday.com item columns into one JSON file per group"
    )
    parser.add_argument(
        "--groups",
        nargs="+",
        default=DEFAULT_GROUPS,
        help="List of group names to export",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(OUTPUT_DIR),
        help=f"Output directory (default: {OUTPUT_DIR})",
    )
    parser.add_argument(
        "--no-board-columns",
        action="store_true",
        help="Do not include board columns metadata in the exported JSON files",
    )
    parser.add_argument(
        "--no-board-groups",
        action="store_true",
        help="Do not include board groups metadata in the exported JSON files",
    )

    args = parser.parse_args()

    try:
        export_each_group_to_separate_json(
            group_names=args.groups,
            output_dir=Path(args.output_dir),
            include_board_columns=not args.no_board_columns,
            include_board_groups=not args.no_board_groups,
        )
    except KeyboardInterrupt:
        print("\n⚠️ Interrupted by user")
        sys.exit(1)
    except Exception as exc:
        print(f"\n❌ Export failed: {exc}")
        sys.exit(1)
# import os
# import sys
# import json
# from pathlib import Path
# from typing import Dict, Optional, Any, List
# from datetime import datetime

# import requests
# from dotenv import load_dotenv

# load_dotenv()

# # ============================================================================
# # CONFIG
# # ============================================================================

# MONDAY_API_TOKEN = os.getenv("MONDAY_API_TOKEN")
# MONDAY_API_URL = "https://api.monday.com/v2"
# MONDAY_BOARD_ID = os.getenv("BOARD_ID", "6514087061")

# DEFAULT_GROUPS = ["Open", "Close", "Cancelled", "Duplicate of Close"]
# OUTPUT_FILE = Path(__file__).parent / "complaints_data_full.json"


# # ============================================================================
# # MONDAY SERVICE
# # ============================================================================

# class MondayService:
#     def __init__(self):
#         if not MONDAY_API_TOKEN:
#             raise ValueError("MONDAY_API_TOKEN is not set in environment variables")

#         self.api_url = MONDAY_API_URL
#         self.headers = {
#             "Authorization": MONDAY_API_TOKEN,
#             "Content-Type": "application/json",
#         }
#         self.board_id = MONDAY_BOARD_ID

#     def _execute_query(self, query: str) -> Dict[str, Any]:
#         response = requests.post(
#             self.api_url,
#             json={"query": query},
#             headers=self.headers,
#             timeout=60,
#         )

#         if response.status_code != 200:
#             raise Exception(
#                 f"Monday API Error: {response.status_code} - {response.text}"
#             )

#         result = response.json()

#         if "errors" in result:
#             raise Exception(
#                 f"Monday GraphQL Error: {json.dumps(result['errors'], indent=2)}"
#             )

#         return result

#     def get_board_columns(self) -> List[Dict[str, Any]]:
#         query = f"""
#         query {{
#             boards(ids: [{self.board_id}]) {{
#                 columns {{
#                     id
#                     title
#                     type
#                 }}
#             }}
#         }}
#         """
#         result = self._execute_query(query)
#         boards = result.get("data", {}).get("boards", [])
#         if not boards:
#             return []
#         return boards[0].get("columns", [])

#     def get_board_groups(self) -> List[Dict[str, Any]]:
#         query = f"""
#         query {{
#             boards(ids: [{self.board_id}]) {{
#                 groups {{
#                     id
#                     title
#                 }}
#             }}
#         }}
#         """
#         result = self._execute_query(query)
#         boards = result.get("data", {}).get("boards", [])
#         if not boards:
#             return []
#         return boards[0].get("groups", [])

#     def get_all_items_by_group_titles(self, group_titles: List[str]) -> List[Dict[str, Any]]:
#         """
#         Fetch all items from the requested group titles.
#         Matching is case-insensitive on the group title.
#         """
#         requested_titles = {g.strip().lower() for g in group_titles if g.strip()}
#         board_groups = self.get_board_groups()

#         matched_groups = [
#             g for g in board_groups
#             if (g.get("title") or "").strip().lower() in requested_titles
#         ]

#         if not matched_groups:
#             return []

#         all_items: List[Dict[str, Any]] = []

#         for group in matched_groups:
#             group_id = group.get("id")
#             group_title = group.get("title", "")
#             print(f"📂 Fetching group: {group_title} (id={group_id})")

#             cursor = None

#             while True:
#                 cursor_part = f', cursor: "{cursor}"' if cursor else ""

#                 query = f"""
#                 query {{
#                     boards(ids: [{self.board_id}]) {{
#                         groups(ids: "{group_id}") {{
#                             id
#                             title
#                             items_page(limit: 50{cursor_part}) {{
#                                 cursor
#                                 items {{
#                                     id
#                                     name
#                                     group {{ id title }}
#                                     created_at
#                                     updated_at
#                                     column_values {{
#                                         id
#                                         type
#                                         text
#                                         value
#                                         column {{
#                                             id
#                                             title
#                                             type
#                                         }}
#                                         ... on BoardRelationValue {{
#                                             linked_item_ids
#                                             linked_items {{ id name }}
#                                         }}
#                                         ... on FileValue {{
#                                             files {{
#                                                 ... on FileAssetValue {{
#                                                     asset {{
#                                                         id
#                                                         name
#                                                         url
#                                                         public_url
#                                                     }}
#                                                 }}
#                                             }}
#                                         }}
#                                     }}
#                                 }}
#                             }}
#                         }}
#                     }}
#                 }}
#                 """

#                 result = self._execute_query(query)
#                 boards = result.get("data", {}).get("boards", [])
#                 if not boards:
#                     break

#                 groups = boards[0].get("groups", [])
#                 if not groups:
#                     break

#                 items_page = groups[0].get("items_page", {})
#                 items = items_page.get("items", [])
#                 all_items.extend(items)

#                 cursor = items_page.get("cursor")
#                 if not cursor:
#                     break

#         return all_items

#     def transform_item_full(self, item: Dict[str, Any]) -> Dict[str, Any]:
#         result: Dict[str, Any] = {
#             "item_id": item.get("id"),
#             "item_name": item.get("name"),
#             "group": item.get("group"),
#             "created_at": item.get("created_at"),
#             "updated_at": item.get("updated_at"),
#             "columns": [],
#         }

#         columns_by_id: Dict[str, Any] = {}
#         columns_by_title: Dict[str, List[Any]] = {}

#         for column_value in item.get("column_values", []):
#             transformed = self._transform_column_value(column_value)
#             result["columns"].append(transformed)

#             col_id = transformed.get("column_id")
#             col_title = transformed.get("column_title")

#             columns_by_id[col_id] = transformed

#             if col_title not in columns_by_title:
#                 columns_by_title[col_title] = []
#             columns_by_title[col_title].append(transformed)

#         result["columns_by_id"] = columns_by_id
#         result["columns_by_title"] = columns_by_title

#         return result

#     def _transform_column_value(self, column_value: Dict[str, Any]) -> Dict[str, Any]:
#         column = column_value.get("column", {}) or {}

#         column_id = column.get("id") or column_value.get("id")
#         column_title = column.get("title", "")
#         column_type = column.get("type") or column_value.get("type")
#         text_value = column_value.get("text")
#         raw_value = column_value.get("value")

#         parsed_value = self._safe_json_loads(raw_value)
#         linked_items = column_value.get("linked_items", []) or []
#         linked_item_ids = column_value.get("linked_item_ids", []) or []
#         files = column_value.get("files", []) or []

#         normalized_value = self._normalize_column_value(
#             column_type=column_type,
#             text_value=text_value,
#             parsed_value=parsed_value,
#             linked_items=linked_items,
#             files=files,
#         )

#         extracted_files = []
#         for f in files:
#             asset = f.get("asset") or {}
#             extracted_files.append(
#                 {
#                     "id": asset.get("id"),
#                     "name": asset.get("name"),
#                     "url": asset.get("url"),
#                     "public_url": asset.get("public_url"),
#                 }
#             )

#         extracted_linked_items = [
#             {
#                 "id": li.get("id"),
#                 "name": li.get("name"),
#             }
#             for li in linked_items
#         ]

#         return {
#             "column_id": column_id,
#             "column_title": column_title,
#             "column_type": column_type,
#             "text": text_value,
#             "raw_value": raw_value,
#             "parsed_value": parsed_value,
#             "linked_item_ids": linked_item_ids,
#             "linked_items": extracted_linked_items,
#             "files": extracted_files,
#             "normalized_value": normalized_value,
#         }

#     def _normalize_column_value(
#         self,
#         column_type: Optional[str],
#         text_value: Optional[str],
#         parsed_value: Any,
#         linked_items: List[Dict[str, Any]],
#         files: List[Dict[str, Any]],
#     ) -> Any:
#         if column_type == "file" or files:
#             out = []
#             for f in files:
#                 asset = f.get("asset") or {}
#                 out.append(
#                     {
#                         "id": asset.get("id"),
#                         "name": asset.get("name"),
#                         "url": asset.get("url"),
#                         "public_url": asset.get("public_url"),
#                     }
#                 )
#             return out

#         if column_type == "board_relation" or linked_items:
#             return [
#                 {
#                     "id": item.get("id"),
#                     "name": item.get("name"),
#                 }
#                 for item in linked_items
#             ]

#         if column_type == "people":
#             if isinstance(parsed_value, dict):
#                 persons = parsed_value.get("personsAndTeams")
#                 if persons is not None:
#                     return persons
#             return text_value

#         if column_type == "date":
#             if isinstance(parsed_value, dict) and parsed_value.get("date"):
#                 return parsed_value.get("date")
#             return text_value

#         if column_type == "numbers":
#             if isinstance(parsed_value, dict):
#                 if parsed_value.get("number") is not None:
#                     return parsed_value["number"]
#                 if parsed_value.get("value") is not None:
#                     return parsed_value["value"]

#             if text_value is not None:
#                 stripped = text_value.strip()
#                 if stripped == "":
#                     return None
#                 try:
#                     if "." in stripped:
#                         return float(stripped)
#                     return int(stripped)
#                 except ValueError:
#                     return stripped

#         if column_type in {"status", "dropdown", "text", "long_text", "formula", "name"}:
#             if text_value is not None and text_value != "":
#                 return text_value
#             return parsed_value

#         if column_type in {"time_tracking", "subtasks", "creation_log", "last_updated", "item_id"}:
#             if parsed_value is not None:
#                 return parsed_value
#             return text_value

#         if parsed_value is not None:
#             return parsed_value

#         if text_value is not None:
#             return text_value

#         return None

#     @staticmethod
#     def _safe_json_loads(value: Optional[str]) -> Any:
#         if not value:
#             return None
#         try:
#             return json.loads(value)
#         except Exception:
#             return value


# # ============================================================================
# # EXPORT
# # ============================================================================

# def export_groups_to_json(
#     group_names: List[str],
#     output_file: Path = OUTPUT_FILE,
#     include_board_columns: bool = True,
#     include_board_groups: bool = True,
# ) -> None:
#     service = MondayService()

#     print(f"🚀 Starting export from Monday board {MONDAY_BOARD_ID}")
#     print(f"📂 Groups: {', '.join(group_names)}")

#     items = service.get_all_items_by_group_titles(group_names)
#     print(f"✅ Found {len(items)} items across requested groups")

#     exported_items = []
#     error_count = 0

#     for idx, item in enumerate(items, start=1):
#         item_name = item.get("name", "Unnamed")
#         item_id = item.get("id")
#         group_title = ((item.get("group") or {}).get("title") or "")
#         print(f"[{idx}/{len(items)}] Processing: {item_name} (ID: {item_id}, Group: {group_title})")

#         try:
#             exported_items.append(service.transform_item_full(item))
#         except Exception as exc:
#             print(f"  ❌ Error: {exc}")
#             error_count += 1

#     payload: Dict[str, Any] = {
#         "exported_at": datetime.utcnow().isoformat() + "Z",
#         "board_id": MONDAY_BOARD_ID,
#         "groups_requested": group_names,
#         "count": len(exported_items),
#         "items": exported_items,
#     }

#     if include_board_columns:
#         payload["board_columns"] = service.get_board_columns()

#     if include_board_groups:
#         payload["board_groups"] = service.get_board_groups()

#     output_file.parent.mkdir(parents=True, exist_ok=True)

#     with open(output_file, "w", encoding="utf-8") as fh:
#         json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)

#     print("\n" + "=" * 60)
#     print("📊 EXPORT SUMMARY")
#     print("=" * 60)
#     print(f"✅ Exported : {len(exported_items)}")
#     print(f"❌ Errors   : {error_count}")
#     print(f"💾 File     : {output_file}")
#     print("=" * 60)


# # ============================================================================
# # MAIN
# # ============================================================================

# if __name__ == "__main__":
#     import argparse

#     parser = argparse.ArgumentParser(
#         description="Export all Monday.com item columns from multiple groups to a JSON file"
#     )
#     parser.add_argument(
#         "--groups",
#         nargs="+",
#         default=DEFAULT_GROUPS,
#         help="List of group names to export",
#     )
#     parser.add_argument(
#         "--output",
#         type=str,
#         default=str(OUTPUT_FILE),
#         help=f"Output JSON path (default: {OUTPUT_FILE})",
#     )
#     parser.add_argument(
#         "--no-board-columns",
#         action="store_true",
#         help="Do not include board columns metadata in the exported JSON",
#     )
#     parser.add_argument(
#         "--no-board-groups",
#         action="store_true",
#         help="Do not include board groups metadata in the exported JSON",
#     )

#     args = parser.parse_args()

#     try:
#         export_groups_to_json(
#             group_names=args.groups,
#             output_file=Path(args.output),
#             include_board_columns=not args.no_board_columns,
#             include_board_groups=not args.no_board_groups,
#         )
#     except KeyboardInterrupt:
#         print("\n⚠️ Interrupted by user")
#         sys.exit(1)
#     except Exception as exc:
#         print(f"\n❌ Export failed: {exc}")
#         sys.exit(1)
# python data_ingestion.py --group Open --output complaints_data.json --include-columns
# python data_ingestion.py --groups Open Close Cancelled "Duplicate of Close"
