"""
test_graph_intake_setup.py

Standalone diagnostic script for the internal intake agent's Microsoft Graph
setup (Task 1 — mailbox subscription). Run this any time to check where
things stand, without needing Postman or a public URL.

Usage:
    python test_graph_intake_setup.py                     # safe checks only (steps 1-3)
    python test_graph_intake_setup.py --base-url http://localhost:8000
                                                            # also runs the local
                                                            # webhook-route checks (steps 4-5)
    python test_graph_intake_setup.py --subscribe --base-url https://<prod-host>
                                                            # ALSO creates a real Graph
                                                            # subscription via the admin
                                                            # endpoint (step 6) — only do
                                                            # this once, against the real
                                                            # deployed URL, after
                                                            # GRAPH_NOTIFICATION_URL is set
                                                            # correctly.

Steps 1-3 talk to Microsoft Graph directly (read-only) and never touch your
own backend. Steps 4-5 talk to whatever --base-url you pass — point it at
your local dev server to test the webhook logic without deploying. Step 6
is opt-in and mutates state (creates a real subscription), so it's gated
behind --subscribe.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys

import requests


def step(n: int, title: str) -> None:
    print(f"\n=== Step {n}: {title} ===")


def ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def check_config() -> bool:
    step(1, "Config sanity (AZURE_*/GRAPH_* env vars)")
    from app.core.config import settings
    from app.services.graph_client import is_configured

    fields = {
        "AZURE_TENANT_ID": bool(settings.AZURE_TENANT_ID),
        "AZURE_CLIENT_ID": bool(settings.AZURE_CLIENT_ID),
        "AZURE_CLIENT_SECRET": bool(settings.AZURE_CLIENT_SECRET),
        "GRAPH_MAILBOX_UPN": settings.GRAPH_MAILBOX_UPN or None,
        "GRAPH_NOTIFICATION_URL": settings.GRAPH_NOTIFICATION_URL or None,
        "GRAPH_WEBHOOK_CLIENT_STATE": bool(settings.GRAPH_WEBHOOK_CLIENT_STATE),
    }
    for k, v in fields.items():
        print(f"    {k}: {v}")

    if not is_configured():
        fail("Not all required settings are present — fix .env first.")
        return False
    ok("All required settings present.")

    if settings.GRAPH_NOTIFICATION_URL.startswith("https://your-app"):
        fail(
            "GRAPH_NOTIFICATION_URL still looks like the placeholder value — "
            "set it to the real deployed URL + /api/v1/graph/webhook/mailbox."
        )
        return False
    return True


def check_token() -> bool:
    step(2, "Graph token acquisition (Azure AD)")
    from app.services.graph_client import get_access_token

    try:
        token = get_access_token()
    except Exception as exc:
        fail(f"Token acquisition failed: {exc}")
        return False
    ok(f"Token acquired (length={len(token)}).")
    return True


def check_mailbox_access() -> bool:
    step(3, "Mailbox read access (Exchange Application Access Policy)")
    from app.core.config import settings
    from app.services.graph_client import graph_get

    try:
        resp = graph_get(
            f"/users/{settings.GRAPH_MAILBOX_UPN}/mailFolders/Inbox/messages",
            params={"$top": 1},
        )
    except requests.HTTPError as exc:
        body = exc.response.text if exc.response is not None else ""
        fail(f"{exc} — {body[:300]}")
        if exc.response is not None and exc.response.status_code == 403:
            fail(
                "403 ErrorAccessDenied means the Exchange Application Access "
                "Policy is still missing/not propagated yet. Ask the admin "
                "to confirm New-ApplicationAccessPolicy was applied for this "
                "AppId + mailbox, then re-run this script."
            )
        return False

    count = len(resp.json().get("value", []))
    ok(f"Mailbox reachable — {count} message(s) returned for Inbox top-1.")
    return True


def check_fetch_real_message() -> bool:
    step(4, "Fetch a real message via the Task 2 fetch service (read-only)")
    from app.core.config import settings
    from app.services.graph_client import graph_get
    from app.services.graph_email_fetch_service import fetch_message, fetch_message_attachments

    try:
        top1 = graph_get(
            f"/users/{settings.GRAPH_MAILBOX_UPN}/mailFolders/Inbox/messages",
            params={"$top": 1, "$select": "id"},
        ).json().get("value", [])
    except Exception as exc:
        fail(f"Could not list Inbox to find a message to fetch: {exc}")
        return False

    if not top1:
        print("  (Inbox is empty — nothing to fetch. Not a failure, just nothing to test.)")
        return True

    message_id = top1[0]["id"]
    try:
        message = fetch_message(message_id)
        attachments = fetch_message_attachments(message_id) if message["has_attachments"] else []
    except Exception as exc:
        fail(f"fetch_message/fetch_message_attachments failed: {exc}")
        return False

    ok(
        f"Fetched message — subject={message.get('subject')!r}, "
        f"from={message.get('sender_email')!r}, conversation_id={message.get('conversation_id')}, "
        f"attachments={len(attachments)}"
    )
    for a in attachments:
        print(f"    - {a.get('filename')} ({a.get('mime_type')}, {a.get('size')} bytes) status={a.get('status')}")
    print("  (Read-only — this did NOT mark the message read or move it.)")
    return True


def check_webhook_validation_handshake(base_url: str) -> bool:
    step(5, f"Local webhook validation handshake ({base_url})")
    token = secrets.token_hex(8)
    url = f"{base_url}/api/v1/graph/webhook/mailbox"
    try:
        resp = requests.post(url, params={"validationToken": token}, timeout=10)
    except requests.RequestException as exc:
        fail(f"Could not reach {url}: {exc}")
        return False

    if resp.status_code == 200 and resp.text == token:
        ok(f"Handshake echoed correctly ({resp.status_code}, body matches).")
        return True
    fail(f"Unexpected response: {resp.status_code} body={resp.text!r}")
    return False


def check_webhook_notification(base_url: str) -> bool:
    step(6, f"Local webhook notification handling ({base_url})")
    from app.core.config import settings

    url = f"{base_url}/api/v1/graph/webhook/mailbox"
    good_state = settings.GRAPH_WEBHOOK_CLIENT_STATE or "test-client-state"

    payload_good = {
        "value": [
            {
                "subscriptionId": "test-sub-id",
                "clientState": good_state,
                "resourceData": {"id": "test-message-id-123"},
            }
        ]
    }
    payload_bad_state = json.loads(json.dumps(payload_good))
    payload_bad_state["value"][0]["clientState"] = "wrong-state"

    try:
        resp_good = requests.post(url, json=payload_good, timeout=10)
        resp_bad = requests.post(url, json=payload_bad_state, timeout=10)
    except requests.RequestException as exc:
        fail(f"Could not reach {url}: {exc}")
        return False

    passed = True
    if resp_good.status_code == 202:
        ok("Matching clientState accepted (202) — check server logs for "
           "'new message test-message-id-123'.")
    else:
        fail(f"Expected 202 for matching clientState, got {resp_good.status_code}")
        passed = False

    if resp_bad.status_code == 202:
        ok("Wrong clientState also returned 202 (per-item drop, not a hard "
           "error) — check server logs for 'unrecognized clientState'.")
    else:
        fail(f"Expected 202 (silently dropped) for wrong clientState, got {resp_bad.status_code}")
        passed = False

    return passed


def do_subscribe(base_url: str) -> bool:
    step(7, f"Create real Graph subscription via admin endpoint ({base_url})")
    url = f"{base_url}/api/v1/admin/graph/subscribe"
    try:
        resp = requests.post(url, timeout=30)
    except requests.RequestException as exc:
        fail(f"Could not reach {url}: {exc}")
        return False

    if resp.status_code == 200:
        ok(f"Subscription created: {resp.json()}")
        return True
    fail(f"{resp.status_code}: {resp.text[:500]}")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=None,
        help="Backend base URL for steps 4-6 (e.g. http://localhost:8000 or "
        "the production URL). Omit to skip steps 4-6.",
    )
    parser.add_argument(
        "--subscribe",
        action="store_true",
        help="Also run step 6 (creates a REAL Graph subscription). Requires --base-url.",
    )
    args = parser.parse_args()

    results = {
        "config": check_config(),
        "token": check_token(),
        "mailbox_access": check_mailbox_access(),
    }
    if results["mailbox_access"]:
        results["fetch_real_message"] = check_fetch_real_message()

    if args.base_url:
        results["webhook_handshake"] = check_webhook_validation_handshake(args.base_url)
        results["webhook_notification"] = check_webhook_notification(args.base_url)
        if args.subscribe:
            results["subscribe"] = do_subscribe(args.base_url)
    else:
        print("\n(Skipping steps 5-7 — pass --base-url to run them.)")

    print("\n=== Summary ===")
    for k, v in results.items():
        print(f"  {k}: {'PASS' if v else 'FAIL'}")

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
