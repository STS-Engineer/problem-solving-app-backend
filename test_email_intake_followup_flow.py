"""
test_email_intake_followup_flow.py

End-to-end functional test for EmailIntakeService's follow-up/threading
path — specifically the two gaps fixed on 2026-08-14:
  1. Reply attachments are downloaded/stored (previously silently dropped).
  2. Someone is notified when a reply arrives (previously silent).

Drives the SAME entry point (`POST /intake/email`) the external MCP agent
uses today, so this exercises the real `EmailIntakeService.ingest()` code
path without needing the internal Graph fetch service (Task 2) to exist yet.

Two scenarios:
  A. pending_review thread: first email -> reply with attachment.
     Expects: reply attaches (not duplicated), attachment shows status
     "stored" with a file_id, and (per code) a notification is sent to the
     original QM/PM recipients.
  B. promoted thread: first email (complete data) -> promote to a real
     complaint -> reply with attachment.
     Expects: reply attaches, attachment is stored AND linked to the
     complaint, and (per code) a notification goes to the assigned CQT.

Usage:
    python test_email_intake_followup_flow.py --base-url http://localhost:8000
    python test_email_intake_followup_flow.py --base-url https://complaint-back.azurewebsites.net --intake-key <X-Intake-Key value>

Notes:
- Uses a small, reliably-available public image URL as the test attachment's
  download_url (real download, not a Graph URL) — good enough to exercise
  the download/store/checksum code path end to end.
- Does NOT verify the notification email actually landed in an inbox — that
  needs a mailbox to check. Treat "server returned 200/201 with no error"
  plus a manual glance at your own inbox (recipients default to
  INTAKE_FALLBACK_EMAIL / plant contacts) as the practical confirmation.
- Test data is tagged with a "TEST-QA-" prefix in source_message_id /
  conversation_id so it's easy to spot and clean up in the DB afterwards.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys

import requests

TEST_ATTACHMENT_URL = "https://www.python.org/static/img/python-logo.png"


def step(n: str, title: str) -> None:
    print(f"\n=== Step {n}: {title} ===")


def ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def post_email(base_url: str, headers: dict, payload: dict) -> dict:
    resp = requests.post(f"{base_url}/api/v1/intake/email", json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_intake(base_url: str, intake_id: int) -> dict:
    resp = requests.get(f"{base_url}/api/v1/intake/{intake_id}", timeout=30)
    resp.raise_for_status()
    return resp.json()


def find_followup_entry(intake: dict, source_message_id: str) -> dict | None:
    for entry in intake.get("attachments") or []:
        if entry.get("type") == "followup_email" and entry.get("source_message_id") == source_message_id:
            return entry
    return None


def scenario_pending_review(base_url: str, headers: dict, tag: str) -> bool:
    step("A", "Follow-up on a PENDING_REVIEW thread")
    conv_id = f"TEST-QA-conv-{tag}"

    first = post_email(base_url, headers, {
        "source_message_id": f"TEST-QA-msg1-{tag}",
        "conversation_id": conv_id,
        "sender_email": "qa-tester@example.com",
        "sender_name": "QA Tester",
        "subject": "TEST-QA - initial complaint",
        "raw_body": "This is an automated QA test email (initial contact).",
        "extracted_data": {},
    })
    if first["status"] != "created":
        fail(f"expected 'created', got {first}")
        return False
    intake_id = first["intake_id"]
    ok(f"initial intake created — id={intake_id}, notified_to={first['notified_to']}")

    reply = post_email(base_url, headers, {
        "source_message_id": f"TEST-QA-msg2-{tag}",
        "conversation_id": conv_id,
        "sender_email": "qa-tester@example.com",
        "sender_name": "QA Tester",
        "subject": "RE: TEST-QA - initial complaint",
        "raw_body": "This is an automated QA test reply with an attachment.",
        "attachments": [{
            "filename": "test-attachment.png",
            "mime_type": "image/png",
            "download_url": TEST_ATTACHMENT_URL,
            "description": "QA test attachment",
            "is_inline": False,
        }],
    })
    if reply["status"] != "attached_to_existing":
        fail(f"expected 'attached_to_existing', got {reply}")
        return False
    if reply["intake_id"] != intake_id:
        fail(f"reply attached to a different intake ({reply['intake_id']} != {intake_id})")
        return False
    ok("reply attached to the same intake (no duplicate created)")

    intake = get_intake(base_url, intake_id)
    followup = find_followup_entry(intake, f"TEST-QA-msg2-{tag}")
    if not followup:
        fail("follow-up entry not found in intake.attachments")
        return False

    att_results = followup.get("attachments") or []
    if not att_results:
        fail("follow-up entry has no processed attachments (should have 1)")
        return False
    stored = [a for a in att_results if a.get("status") == "stored"]
    if not stored:
        fail(f"attachment was not stored — got: {json.dumps(att_results)[:500]}")
        return False
    ok(f"reply attachment stored — file_id={stored[0].get('file_id')}, url={stored[0].get('url')}")

    print(
        "  [MANUAL CHECK] A follow-up notification should have been sent to: "
        f"{intake.get('notified_to')} (or the fallback address) — subject "
        f"'[AVOCarbon] Customer replied — #{intake_id}'. Check that inbox."
    )
    return True


def scenario_promoted(base_url: str, headers: dict, tag: str) -> bool:
    step("B", "Follow-up on a PROMOTED thread (already a real complaint)")
    conv_id = f"TEST-QA-conv-promoted-{tag}"

    complete_data = {
        "quality_issue_warranty": "CS2",
        "product_line": "CHOKE",
        "avocarbon_plant": "POITIERS",
        "potential_avocarbon_process_linked_to_problem": "ASSEMBLY",
        "defects": "Function",
        "customer": "BOSCH",
        "complaint_name": "TEST-QA automated complaint",
        "customer_plant_name": "TEST-QA Plant",
        "avocarbon_product_type": "TEST-QA Product",
        "complaint_description": "Automated QA test complaint — safe to delete.",
        "customer_complaint_date": "2026-08-01",
    }

    first = post_email(base_url, headers, {
        "source_message_id": f"TEST-QA-promo-msg1-{tag}",
        "conversation_id": conv_id,
        "sender_email": "qa-tester@example.com",
        "sender_name": "QA Tester",
        "subject": "TEST-QA - complete complaint",
        "raw_body": "Automated QA test email with complete data (should auto-promote).",
        "extracted_data": complete_data,
        "detected_plant": "POITIERS",
    })
    if first["status"] != "created":
        fail(f"expected 'created', got {first}")
        return False
    intake_id = first["intake_id"]
    ok(f"initial intake created — id={intake_id}")

    promote_resp = requests.post(f"{base_url}/api/v1/intake/{intake_id}/promote", timeout=30)
    promote_resp.raise_for_status()
    promote_result = promote_resp.json()
    if promote_result["status"] not in ("created", "already_promoted"):
        fail(f"promotion did not succeed: {promote_result}")
        return False
    ok(f"intake promoted -> complaint {promote_result.get('reference_number')}")

    reply = post_email(base_url, headers, {
        "source_message_id": f"TEST-QA-promo-msg2-{tag}",
        "conversation_id": conv_id,
        "sender_email": "qa-tester@example.com",
        "sender_name": "QA Tester",
        "subject": "RE: TEST-QA - complete complaint",
        "raw_body": "Automated QA reply on an already-promoted thread, with an attachment.",
        "attachments": [{
            "filename": "test-attachment-promoted.png",
            "mime_type": "image/png",
            "download_url": TEST_ATTACHMENT_URL,
            "description": "QA test attachment on promoted thread",
            "is_inline": False,
        }],
    })
    if reply["status"] != "attached_to_existing":
        fail(f"expected 'attached_to_existing', got {reply}")
        return False
    ok("reply attached to the promoted intake (not a new complaint)")

    intake = get_intake(base_url, intake_id)
    followup = find_followup_entry(intake, f"TEST-QA-promo-msg2-{tag}")
    if not followup:
        fail("follow-up entry not found in intake.attachments")
        return False
    stored = [a for a in (followup.get("attachments") or []) if a.get("status") == "stored"]
    if not stored:
        fail(f"attachment was not stored on promoted thread — got: {json.dumps(followup)[:500]}")
        return False
    ok(f"reply attachment on promoted thread stored — file_id={stored[0].get('file_id')}")
    print(
        "  [MANUAL CHECK] File_id above should now be linked to complaint "
        f"{intake.get('complaint_id')} (via link_intake_files_to_complaint) — "
        "spot-check the complaint's files list in the app if you want visual confirmation."
    )
    print(
        "  [MANUAL CHECK] A follow-up notification should have gone to the assigned CQT "
        "(or plant QM/PM if none assigned yet) — subject "
        f"'[AVOCarbon] Customer replied — {promote_result.get('reference_number')}'."
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument(
        "--intake-key",
        default=None,
        help="X-Intake-Key header value. Omit if INTAKE_API_KEY is unset on the target (dev only).",
    )
    args = parser.parse_args()

    headers = {}
    if args.intake_key:
        headers["X-Intake-Key"] = args.intake_key

    tag = secrets.token_hex(4)
    print(f"Test tag: {tag} (all test data is prefixed TEST-QA- for easy cleanup)")

    results = {
        "pending_review_followup": scenario_pending_review(args.base_url, headers, tag),
        "promoted_followup": scenario_promoted(args.base_url, headers, tag),
    }

    print("\n=== Summary ===")
    for k, v in results.items():
        print(f"  {k}: {'PASS' if v else 'FAIL'}")

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
