"""
Utility script to fetch Monday.com users and create a mapping to database users
This helps you map Monday user IDs to your database user IDs
"""
import os
import sys
from pathlib import Path
from typing import Dict, List
import json

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv()

import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.user import User


# ============================================================================
# CONFIGURATION
# ============================================================================

MONDAY_API_TOKEN = os.getenv("MONDAY_API_TOKEN")
MONDAY_API_URL = "https://api.monday.com/v2"
MONDAY_BOARD_ID = os.getenv("BOARD_ID", "6514087061")


# ============================================================================
# MONDAY.COM USER FETCHER
# ============================================================================

def fetch_monday_users() -> List[Dict]:
    """Fetch all users from Monday.com workspace"""
    if not MONDAY_API_TOKEN:
        raise ValueError("MONDAY_API_TOKEN is not set")
    
    headers = {
        "Authorization": MONDAY_API_TOKEN,
        "Content-Type": "application/json"
    }
    
    query = """
    query {
        users {
            id
            name
            email
            title
            enabled
        }
    }
    """
    
    response = requests.post(
        MONDAY_API_URL,
        json={"query": query},
        headers=headers
    )
    
    if response.status_code != 200:
        raise Exception(f"Monday API Error: {response.status_code} - {response.text}")
    
    data = response.json()
    users = data.get("data", {}).get("users", [])
    
    return users


def fetch_database_users() -> List[User]:
    """Fetch all users from database"""
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set")
    
    # Force SSL for Azure if not present
    if "sslmode=" not in db_url:
        sep = "&" if "?" in db_url else "?"
        db_url = f"{db_url}{sep}sslmode=require"
    
    engine = create_engine(db_url, pool_pre_ping=True)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    
    try:
        users = db.query(User).all()
        return users
    finally:
        db.close()


def create_user_mapping():
    """Create a mapping between Monday users and database users"""
    print("🔍 Fetching Monday.com users...")
    monday_users = fetch_monday_users()
    print(f"✅ Found {len(monday_users)} Monday users")
    
    print("\n🔍 Fetching database users...")
    db_users = fetch_database_users()
    print(f"✅ Found {len(db_users)} database users")
    
    print("\n" + "="*80)
    print("MONDAY.COM USERS")
    print("="*80)
    for user in monday_users:
        status = "✅ Enabled" if user.get('enabled') else "❌ Disabled"
        print(f"ID: {user['id']:12} | Email: {user.get('email', 'N/A'):30} | Name: {user.get('name', 'N/A'):30} | {status}")
    
    print("\n" + "="*80)
    print("DATABASE USERS")
    print("="*80)
    for user in db_users:
        status = "✅ Active" if user.is_active else "❌ Inactive"
        print(f"ID: {user.id:5} | Email: {user.email:30} | Name: {user.first_name or ''} {user.last_name or '':20} | Role: {user.role:15} | {status}")
    
    # Try to auto-match by email
    print("\n" + "="*80)
    print("AUTO-MATCHED USERS (by email)")
    print("="*80)
    
    user_mapping = {}
    matched_count = 0
    
    for monday_user in monday_users:
        monday_email = monday_user.get('email', '').lower().strip()
        if not monday_email:
            continue
        
        # Try to find matching database user by email
        db_user = next((u for u in db_users if u.email.lower().strip() == monday_email), None)
        
        if db_user:
            user_mapping[int(monday_user['id'])] = db_user.id
            matched_count += 1
            print(f"✅ Monday ID {monday_user['id']:12} ({monday_user.get('name', 'N/A'):30}) → DB ID {db_user.id:5} ({db_user.username})")
        else:
            print(f"⚠️  Monday ID {monday_user['id']:12} ({monday_user.get('name', 'N/A'):30}) → NO MATCH (email: {monday_email})")
    
    print("\n" + "="*80)
    print("MAPPING SUMMARY")
    print("="*80)
    print(f"✅ Matched: {matched_count}/{len(monday_users)} users")
    print(f"⚠️  Unmatched: {len(monday_users) - matched_count} users")
    
    # Generate Python code for the mapping
    print("\n" + "="*80)
    print("PYTHON CODE FOR USER MAPPING")
    print("="*80)
    print("Add this to your sync script:\n")
    print("# Monday user ID → Database user ID mapping")
    print("MONDAY_USER_MAPPING = {")
    for monday_id, db_id in user_mapping.items():
        monday_user = next((u for u in monday_users if int(u['id']) == monday_id), None)
        if monday_user:
            print(f"    {monday_id}: {db_id},  # {monday_user.get('name', 'N/A')} ({monday_user.get('email', 'N/A')})")
    print("}")
    
    # Save to JSON file
    output_file = "monday_user_mapping.json"
    with open(output_file, 'w') as f:
        json.dump({
            "monday_users": monday_users,
            "database_users": [
                {
                    "id": u.id,
                    "username": u.username,
                    "email": u.email,
                    "first_name": u.first_name,
                    "last_name": u.last_name,
                    "role": u.role,
                    "is_active": u.is_active
                } for u in db_users
            ],
            "mapping": user_mapping
        }, f, indent=2)
    
    print(f"\n✅ Mapping saved to: {output_file}")
    
    return user_mapping


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    try:
        mapping = create_user_mapping()
        
        print("\n" + "="*80)
        print("NEXT STEPS")
        print("="*80)
        print("1. Review the auto-matched users above")
        print("2. Copy the MONDAY_USER_MAPPING code into your sync script")
        print("3. For unmatched users, either:")
        print("   - Create corresponding users in your database")
        print("   - Add manual mappings to MONDAY_USER_MAPPING")
        print("4. Uncomment the quality_manager mapping code in the sync script")
        print("="*80)
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)