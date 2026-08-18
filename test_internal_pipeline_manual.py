"""
Ad-hoc manual smoke test for Task 4's internal_intake_pipeline.handle_new_message.

Fetches the real top Inbox message from the configured mailbox, runs it
through the full fetch -> classify -> extract -> ingest pipeline against
whatever DATABASE_URL is configured (should be the isolated test DB, NOT
production), with mutate_mailbox=False so the real mailbox is never touched
(no mark-as-read, no move) even though the message content itself is real.

Usage:
    python test_internal_pipeline_manual.py
"""
from app.core.config import settings
from app.db.session import SessionLocal
from app.services.graph_client import graph_get
from app.services.internal_intake_pipeline import handle_new_message

print("DATABASE_URL ->", settings.DATABASE_URL.split("@")[-1])
assert "problem-solving-8d-db" in settings.DATABASE_URL, "Refusing to run against a non-test DB!"

top1 = graph_get(
    f"/users/{settings.GRAPH_MAILBOX_UPN}/mailFolders/Inbox/messages",
    params={"$top": 1, "$select": "id,subject"},
).json().get("value", [])

if not top1:
    print("Inbox is empty — nothing to test.")
else:
    print(f"Using real message: {top1[0]['subject']!r} (id={top1[0]['id']})")
    db = SessionLocal()
    try:
        result = handle_new_message(db, top1[0]["id"], mutate_mailbox=False)
        print("Pipeline result:", result)
    finally:
        db.close()
