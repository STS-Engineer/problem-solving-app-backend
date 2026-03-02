"""
Script to sync complaints from Monday.com board to local database
Run this script separately from the main app to populate the database

UPDATES:
- Fixed column mapping to match actual Monday.com board structure
- Updated PlantEnum mapping to match database schema
- Updated ProductLineEnum mapping to match database schema
- Improved error handling and data validation
"""
import os
import sys
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime, date
import locale

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.models.complaint import Complaint
from app.models.enums import PlantEnum, ProductLineEnum
from app.db.base import Base


# ============================================================================
# MONDAY.COM CONFIGURATION
# ============================================================================

MONDAY_API_TOKEN = os.getenv("MONDAY_API_TOKEN")
MONDAY_API_URL = "https://api.monday.com/v2"
MONDAY_BOARD_ID = os.getenv("BOARD_ID", "6514087061")

# Mapping Monday column names to database fields
# Based on actual board inspection document
COLUMN_MAPPING: Dict[str, str] = {
    # Monday Column Name → Database Field Name
    "AVO PLANT": "avocarbon_plant",
    "PRODUCT LINE": "product_line",
    "AVO PROCESS LINKED WITH THE PROBLEM": "potential_avocarbon_process_linked_to_problem",
    "Quality issue / Warranty": "quality_issue_warranty",
    "Defects": "defects",
    "REPETITIVE complete with the number (0: non repetitive, 1: 1 time repetitive, 2: 2 times repeated, etc)": "repetitive_complete_with_number",
    "Quality Manager": "quality_manager",
    "Customers": "customer",
    "CUSTOMER PLANT NAME": "customer_plant_name",
    "Status": "status",
    "COMPLAINT OPENING DATE": "complaint_opening_date",
    "COMPLAINT DESCRIPTION": "complaint_description",
    "AVO  PART DESCRIPTION": "avocarbon_product_type",  # Using AVO PART DESCRIPTION for product type
    "CUSTOMER APPLICATION": "concerned_application",
    "CUSTOMER COMPLAINT DATE": "customer_complaint_date",
}

# Group names from Monday.com board
GROUP_OPEN = "Open"  # Note: Capital 'O' based on board inspection
GROUP_CLOSED = "Close"  # Note: "Close" not "Closed"


# ============================================================================
# ENUM MAPPINGS
# ============================================================================

# Map Monday.com plant values to PlantEnum values
PLANT_MAPPING = {
    # Monday value → Database enum value
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

# Map Monday.com product line values to ProductLineEnum values
PRODUCT_LINE_MAPPING = {
    # Monday value → Database enum value
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
import json


class MondayService:
    """Service to interact with Monday.com API"""
    
    def __init__(self):
        if not MONDAY_API_TOKEN:
            raise ValueError("MONDAY_API_TOKEN is not set in environment variables")
        
        self.api_url = MONDAY_API_URL
        self.headers = {
            "Authorization": MONDAY_API_TOKEN,
            "Content-Type": "application/json"
        }
        self.board_id = MONDAY_BOARD_ID
    
    def _execute_query(self, query: str) -> Dict:
        """Execute a GraphQL query"""
        response = requests.post(
            self.api_url,
            json={"query": query},
            headers=self.headers
        )
        
        if response.status_code != 200:
            raise Exception(f"Monday API Error: {response.status_code} - {response.text}")
        
        return response.json()
    
    def get_all_items_by_group(self, group_title: str) -> list:
        """Get ALL items from a specific group (handles pagination)"""
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
                                created_at
                                updated_at
                                group {{
                                    id
                                    title
                                }}
                                column_values {{
                                    id
                                    column {{
                                        title
                                    }}
                                    text
                                    value
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

            # Stop if group not found or no more pages
            if not cursor_found or not cursor:
                break

        return all_items
    
    def transform_item_to_complaint_data(self, item: Dict) -> Dict:
        """Transform a Monday item to Complaint data"""
        complaint_data = {
            "complaint_name": item.get('name', ''),
        }
        
        # Map columns
        for column_value in item.get('column_values', []):
            column_title = column_value.get('column', {}).get('title', '')
            text_value = column_value.get('text')
            value_json = column_value.get('value')
            
            # Find corresponding DB field
            db_field = COLUMN_MAPPING.get(column_title)
            
            if db_field:
                extracted_value = self._extract_column_value(
                    text_value, 
                    value_json, 
                    column_title
                )
                
                if extracted_value:
                    # Special handling based on field type
                    if db_field in ["complaint_opening_date", "customer_complaint_date"]:
                        complaint_data[db_field] = self._parse_monday_date(extracted_value)
                    elif db_field == "avocarbon_plant":
                        # Map to PlantEnum
                        complaint_data[db_field] = self._map_to_plant_enum(extracted_value)
                    elif db_field == "product_line":
                        # Map to ProductLineEnum
                        complaint_data[db_field] = self._map_to_product_line_enum(extracted_value)
                    elif db_field == "quality_manager":
                        # Skip quality_manager field - Monday user IDs don't match database user IDs
                        # You'll need to set this manually or create a user ID mapping
                        # Monday people columns return JSON with personsAndTeams array
                        pass  # Don't set quality_manager from Monday data
                    else:
                        complaint_data[db_field] = extracted_value
        
        # Determine status based on group
        group_title = item.get('group', {}).get('title', '')
        if group_title.lower() == GROUP_CLOSED.lower():
            complaint_data['status'] = 'closed'
        else:
            complaint_data['status'] = complaint_data.get('status', 'open')
        
        return complaint_data
    
    def _parse_monday_date(self, date_str: Optional[str]) -> Optional[date]:
        """Parse a Monday.com date column"""
        if not date_str:
            return None

        try:
            # Try ISO format first (YYYY-MM-DD)
            return datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            pass

        try:
            # Try with time component
            return datetime.fromisoformat(date_str.replace("Z", "")).date()
        except ValueError:
            pass

        try:
            # Try common date formats
            for fmt in ["%B %d, %Y", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"]:
                try:
                    return datetime.strptime(date_str, fmt).date()
                except ValueError:
                    continue
        except Exception:
            pass

        print(f"  ⚠️  Could not parse date: {date_str}")
        return None
    
    def _map_to_plant_enum(self, value: str) -> Optional[str]:
        """Map Monday plant value to PlantEnum"""
        if not value:
            return None
        
        # Normalize the value (uppercase, strip whitespace)
        normalized = value.upper().strip()
        
        # Check exact match first
        if normalized in PLANT_MAPPING:
            return PLANT_MAPPING[normalized]
        
        # Try partial match
        for key in PLANT_MAPPING:
            if key in normalized or normalized in key:
                print(f"  ℹ️  Partial plant match: '{value}' → {key}")
                return PLANT_MAPPING[key]
        
        print(f"  ⚠️  Unknown plant value: '{value}' - available: {list(PLANT_MAPPING.keys())}")
        return None
    
    def _map_to_product_line_enum(self, value: str) -> Optional[str]:
        """Map Monday product line value to ProductLineEnum"""
        if not value:
            return None
        
        # Normalize the value (uppercase, strip whitespace)
        normalized = value.upper().strip()
        
        # Check exact match first
        if normalized in PRODUCT_LINE_MAPPING:
            return PRODUCT_LINE_MAPPING[normalized]
        
        # Try partial match
        for key in PRODUCT_LINE_MAPPING:
            if key in normalized or normalized in key:
                print(f"  ℹ️  Partial product line match: '{value}' → {key}")
                return PRODUCT_LINE_MAPPING[key]
        
        print(f"  ⚠️  Unknown product line value: '{value}' - available: {list(PRODUCT_LINE_MAPPING.keys())}")
        return None
    
    def _extract_user_id(self, value_json: Optional[str]) -> Optional[int]:
        """Extract user ID from Monday people column"""
        if not value_json:
            return None
        
        try:
            data = json.loads(value_json)
            if isinstance(data, dict) and 'personsAndTeams' in data:
                persons = data['personsAndTeams']
                if persons and len(persons) > 0:
                    # Get first person's ID
                    person_id = persons[0].get('id')
                    if person_id:
                        # Note: This is Monday's user ID, not your database user ID
                        # You'll need to map Monday user IDs to your database user IDs
                        return int(person_id)
        except (json.JSONDecodeError, ValueError, KeyError, IndexError):
            pass
        
        return None
    
    def _extract_column_value(
        self, 
        text_value: Optional[str], 
        value_json: Optional[str],
        column_title: str
    ) -> Optional[str]:
        """Extract value from a column"""
        # If text_value exists and is not empty, use it
        if text_value and text_value.strip():
            return text_value.strip()
        
        # Try to parse JSON for connected/mirror columns
        if value_json:
            try:
                value_data = json.loads(value_json)
                
                if isinstance(value_data, dict):
                    # Case 1: Status/dropdown column with label
                    if 'label' in value_data:
                        return str(value_data['label'])
                    
                    # Case 2: Mirror column with display_value
                    if 'display_value' in value_data:
                        return str(value_data['display_value'])
                    
                    # Case 3: Connected column - extract linked IDs
                    if 'linkedPulseIds' in value_data:
                        linked_ids = value_data.get('linkedPulseIds', [])
                        if linked_ids:
                            return ','.join(map(str, linked_ids))
                    
                    # Case 4: Text column
                    if 'text' in value_data:
                        return str(value_data['text'])
                    
                    # Case 5: Date column
                    if 'date' in value_data:
                        return str(value_data['date'])
                
                # Case 6: List of values
                if isinstance(value_data, list) and value_data:
                    if isinstance(value_data[0], dict):
                        names = [
                            item.get('name') or item.get('text') 
                            for item in value_data 
                            if item.get('name') or item.get('text')
                        ]
                        if names:
                            return ', '.join(names)
                    else:
                        return ', '.join(map(str, value_data))
                
            except (json.JSONDecodeError, AttributeError, KeyError) as e:
                print(f"  ⚠️  Error parsing column '{column_title}': {e}")
        
        return None


# ============================================================================
# DATABASE SYNC LOGIC
# ============================================================================

def get_database_session() -> Session:
    """Create database session"""
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set")
    
    # Force SSL for Azure if not present
    if "sslmode=" not in db_url:
        sep = "&" if "?" in db_url else "?"
        db_url = f"{db_url}{sep}sslmode=require"
    
    engine = create_engine(db_url, pool_pre_ping=True)
    
    # Create tables if they don't exist
    Base.metadata.create_all(bind=engine)
    
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal()


def sync_monday_to_database(group_name: str = GROUP_OPEN, default_user_id: int = 1, dry_run: bool = False):
    """
    Sync complaints from Monday.com to database
    
    Args:
        group_name: Name of the Monday group to sync (default: "Open")
        default_user_id: Default user ID for reported_by field
        dry_run: If True, don't actually write to database (useful for testing)
    """
    print(f"🚀 Starting sync from Monday.com board {MONDAY_BOARD_ID}")
    print(f"📂 Syncing group: '{group_name}'")
    if dry_run:
        print("🔍 DRY RUN MODE - No data will be written to database")
    
    # Initialize services
    monday_service = MondayService()
    db = get_database_session()
    
    try:
        # Fetch items from Monday
        print(f"\n📥 Fetching items from Monday.com...")
        items = monday_service.get_all_items_by_group(group_name)
        print(f"✅ Found {len(items)} items in group '{group_name}'")
        
        if not items:
            print("⚠️  No items found. Exiting.")
            return
        
        # Process each item
        created_count = 0
        skipped_count = 0
        error_count = 0
        
        for idx, item in enumerate(items, 1):
            item_id = item.get('id')
            item_name = item.get('name', 'Unnamed')
            
            print(f"\n[{idx}/{len(items)}] Processing: {item_name} (ID: {item_id})")
            
            try:
                # Transform Monday item to complaint data
                complaint_data = monday_service.transform_item_to_complaint_data(item)
                
                # Add required fields
                complaint_data['reported_by'] = default_user_id
                
                # Validate required fields
                if not complaint_data.get('complaint_name'):
                    print(f"  ⚠️  Skipping - missing complaint_name")
                    skipped_count += 1
                    continue
                
                if not complaint_data.get('product_line'):
                    print(f"  ⚠️  Skipping - missing product_line (required field)")
                    skipped_count += 1
                    continue
                
                # Print extracted data for review
                print(f"  📋 Extracted data:")
                for key, value in complaint_data.items():
                    if value:
                        print(f"     • {key}: {value}")
                
                if not dry_run:
                    # Create complaint
                    complaint = Complaint(**complaint_data)
                    db.add(complaint)
                    db.commit()
                    db.refresh(complaint)
                    
                    print(f"  ✅ Created complaint ID: {complaint.id}")
                    created_count += 1
                else:
                    print(f"  ✅ Would create complaint (dry run)")
                    created_count += 1
                
            except Exception as e:
                print(f"  ❌ Error processing item: {e}")
                import traceback
                traceback.print_exc()
                error_count += 1
                if not dry_run:
                    db.rollback()
                continue
        
        # Summary
        print("\n" + "="*60)
        print("📊 SYNC SUMMARY")
        print("="*60)
        if dry_run:
            print("🔍 DRY RUN - No changes made to database")
        print(f"✅ Created: {created_count}")
        print(f"⚠️  Skipped: {skipped_count}")
        print(f"❌ Errors: {error_count}")
        print(f"📝 Total processed: {len(items)}")
        print("="*60)
        
    except Exception as e:
        print(f"\n❌ Fatal error during sync: {e}")
        import traceback
        traceback.print_exc()
        if not dry_run:
            db.rollback()
        raise
    
    finally:
        db.close()
        print("\n✅ Database connection closed")


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Sync Monday.com board to database")
    parser.add_argument(
        "--group",
        type=str,
        default=GROUP_OPEN,
        help=f"Group name to sync (default: '{GROUP_OPEN}')"
    )
    parser.add_argument(
        "--user-id",
        type=int,
        default=1,
        help="Default user ID for reported_by field (default: 1)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without writing to database (test mode)"
    )
    
    args = parser.parse_args()
    
    try:
        sync_monday_to_database(
            group_name=args.group,
            default_user_id=args.user_id,
            dry_run=args.dry_run
        )
    except KeyboardInterrupt:
        print("\n\n⚠️  Sync interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Sync failed: {e}")
        sys.exit(1)