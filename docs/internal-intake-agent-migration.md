# Internal Intake Agent — Migration Documentation

Master documentation for the migration of the complaint email-intake feature
from an **external LLM agent** (polling a shared Outlook mailbox via an MCP
connector, see [`docs/intake-agent-prompt.md`](intake-agent-prompt.md) and
[`docs/mcp-intake-openapi.json`](mcp-intake-openapi.json)) to an **internal
LLM agent** that:

- gets **pushed** new mail via a Microsoft Graph subscription (webhook),
  instead of an external agent polling Outlook,
- fetches the message + attachments itself via Microsoft Graph,
- runs classification/extraction internally,
- calls the existing intake pipeline directly (no external MCP hop).

This file is appended to as each task lands — one section per task, in order.

---

## Task list overview

1. **Microsoft Graph mailbox subscription setup** — ✅ **DONE** (2026-08-14) — live subscription created and verified end-to-end
2. **Internal email-fetch service** — ✅ **DONE** (2026-08-17) — fetches real messages + attachments, verified against a live mailbox message
3. Internal LLM classification & extraction service — not started (next up)
4. Wire internal pipeline into existing intake flow — not started
5. Error handling & observability — not started
6. Testing & cutover — not started

---

## Task 1 — Microsoft Graph mailbox subscription setup

**Goal:** have the backend get notified by Microsoft Graph whenever new mail
arrives in the shared claims mailbox, instead of an external agent polling it.

### What was built

| Piece | File |
|---|---|
| Config (env vars) | `app/core/config.py` |
| Graph auth (MSAL client-credentials) | `app/services/graph_client.py` |
| Subscription tracking table + migration | `app/models/graph_subscription.py`, `alembic/versions/a1b2c3d4e5f6_add_graph_subscription.py` |
| Create/renew/delete subscription logic | `app/services/graph_subscription_service.py` |
| Webhook receiver (validation handshake + notification intake) | `app/api/routes/graph_webhook.py` → `POST /api/v1/graph/webhook/mailbox` |
| Bootstrap/status admin endpoints | `app/api/routes/admin_router.py` → `POST /api/v1/admin/graph/subscribe`, `GET /api/v1/admin/graph/subscription-status` |
| Auto-renewal scheduler job | `app/services/scheduler.py` (`graph_subscription_renewal`, every 30 min, no-ops if not configured) |
| New dependency | `msal` (added to `requirements.txt`) |

### Environment variables

| Variable | Meaning | Example |
|---|---|---|
| `AZURE_TENANT_ID` | AVOCarbon's Azure AD tenant id | `xxxxxxxx-xxxx-...` |
| `AZURE_CLIENT_ID` | App registration (client) id used to authenticate to Graph | `xxxxxxxx-xxxx-...` |
| `AZURE_CLIENT_SECRET` | Secret ("password") for that app registration | (kept in `.env`, never committed) |
| `GRAPH_MAILBOX_UPN` | Email address of the shared claims mailbox to monitor | `customer_complain@avocarbon.com` |
| `GRAPH_NOTIFICATION_URL` | Public HTTPS URL Graph will POST notifications to — **must** be reachable from the internet, not localhost | `https://<deployed-host>/api/v1/graph/webhook/mailbox` |
| `GRAPH_WEBHOOK_CLIENT_STATE` | Shared secret echoed back by Graph on every notification, used to reject spoofed calls to the webhook | any long random string |
| `GRAPH_SUBSCRIPTION_MAX_MINUTES` | Subscription lifetime requested per create/renew call (Graph max ≈ 4230 min / 2.9 days for the messages resource) | `4230` (default) |
| `GRAPH_SUBSCRIPTION_RENEW_MARGIN_MINUTES` | How long before expiry the renewal job kicks in | `60` (default) |

### App registration decision (2026-08-14)

The Azure AD app initially used (`AZURE_CLIENT_ID` pointing at **"Audit-app"**)
turned out to be **shared with another, unrelated feature** — a direct query
of its granted Microsoft Graph application permissions
(`GET /servicePrincipals/{id}/appRoleAssignments`) showed:

| Permission ID | Permission |
|---|---|
| `798ee544-9d2d-430c-a058-570e29e34338` | Calendars.Read |
| `ef54d2bf-783f-4e0f-bca1-3210c0444d99` | Calendars.ReadWrite |
| `b633e1c5-b582-4048-a93e-9f11b44c7e96` | Mail.Send |
| `9492366f-7969-46a4-8d15-ed1a20078fff` | (unidentified) |
| `8ba4a692-bc31-4128-9094-475872af8a53` | (unidentified) |

**Neither `Mail.Read` nor `Mail.ReadWrite` were present** — this, not the
Exchange Application Access Policy, was the actual root cause of the earlier
`403 ErrorAccessDenied` (a policy only *restricts* access that's already
granted; the base permission was never granted at all).

Decision: **do not reuse "Audit-app".** It already has `Mail.Send`, meaning
some other feature sends mail through it (from a mailbox we don't control) —
scoping an Exchange Application Access Policy to this app for the claims
mailbox only would risk breaking that other feature. A **new, dedicated app
registration** (e.g. `Complaint-Intake-Agent`) is used instead, with only
`Mail.Read` + `Mail.ReadWrite`, isolated blast radius, and its own Application
Access Policy.

### Prerequisites checklist

- [ ] New dedicated app registration created (`Complaint-Intake-Agent` or similar) — **not "Audit-app"**
- [ ] `Mail.Read` + `Mail.ReadWrite` **application permissions** added and admin-consented on the new app
- [ ] `.env` updated: `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` point at the new app (`AZURE_TENANT_ID` unchanged)
- [ ] **Exchange Online Application Access Policy** scoping the **new app id** to the claims mailbox:
  ```powershell
  New-ApplicationAccessPolicy -AppId "<NEW_AZURE_CLIENT_ID>" -PolicyScopeGroupId "customer_complain@avocarbon.com" -AccessRight RestrictAccess -Description "Complaint Intake Agent - claims mailbox only"
  ```
  **Status as of 2026-08-14: NOT yet done — BLOCKING, waiting on the Exchange/M365 admin** (after the app-registration + permission steps above, which don't need anyone else).
- [ ] `GRAPH_NOTIFICATION_URL` set to the real deployed URL with the exact path `/api/v1/graph/webhook/mailbox`. **Status: WRONG PATH** — currently set to
  `https://complaint-back.azurewebsites.net/api/webhooks/graph-notifications`,
  must be changed to
  `https://complaint-back.azurewebsites.net/api/v1/graph/webhook/mailbox`.
- [ ] This feature's code (this task's files) deployed to the production backend — ngrok isn't usable here, so the real deploy is also how the public-URL requirement gets satisfied.

### How to test

Two test aids were built for this:

- **`test_graph_intake_setup.py`** (repo root) — standalone diagnostic
  script, safe to re-run any time.
- **`docs/postman/internal-intake-agent.postman_collection.json`** —
  importable Postman collection covering the webhook + admin endpoints.

Note: ngrok is not permitted here, and the backend is already deployed to
production, so **the public-URL requirement is satisfied by deploying this
feature's code to production** (not by a tunnel) — there is no local
end-to-end option for this project; the webhook route logic itself can
still be exercised locally (Part A below), but the real Graph round-trip
requires the production deploy (Part B).

#### A. What you CAN test right now (no admin/deploy needed)

1. Run the diagnostic script with no arguments — safe, read-only, exercises
   config + token + mailbox access:
   ```bash
   python test_graph_intake_setup.py
   ```
   Today this prints `config: PASS`, `token: PASS`, `mailbox_access: FAIL`
   (blocked on the Exchange Application Access Policy) — expected until the
   admin applies it.

2. Import `docs/postman/internal-intake-agent.postman_collection.json` into
   Postman, set the `baseUrl` variable to your **local** dev server (e.g.
   `http://localhost:8000`, run via `uvicorn app.main:app --reload`), and set
   the `clientState` variable to your `GRAPH_WEBHOOK_CLIENT_STATE` value.
   Run requests **1–3** ("Webhook validation handshake", "Notification -
   matching clientState", "Notification - wrong clientState") — these only
   exercise this backend's own route logic and never touch real Graph, so
   they work today regardless of the mailbox-access blocker. Confirm request
   1 echoes the token back, request 2 returns 202 and logs a "new message"
   line, request 3 returns 202 but logs "unrecognized clientState" with NO
   "new message" line.
   You can also run these two same checks from the script instead of Postman:
   ```bash
   python test_graph_intake_setup.py --base-url http://localhost:8000
   ```

Requests **4–5** ("Admin - create Graph subscription", "Admin - subscription
status") and the script's `--subscribe` flag are **NOT** safe to run yet —
they either need the mailbox-access blocker resolved (request 4) or a
deployed host (both).

#### B. Once the admin confirms mailbox access AND this code is deployed

1. Fix `GRAPH_NOTIFICATION_URL` in production `.env` to
   `https://complaint-back.azurewebsites.net/api/v1/graph/webhook/mailbox`
   (currently wrong — see Prerequisites checklist above).
2. Deploy this task's code to production (see "Push & deploy" below).
3. Re-run the script against production to confirm the blocker is cleared and the
   route is live:
   ```bash
   python test_graph_intake_setup.py --base-url https://complaint-back.azurewebsites.net
   ```
   Expect `config/token/mailbox_access/webhook_handshake/webhook_notification`
   all `PASS`.
4. Bootstrap the real subscription (one-time) — either via Postman request 4,
   or:
   ```bash
   python test_graph_intake_setup.py --base-url https://complaint-back.azurewebsites.net --subscribe
   ```
   Expect a `subscription_id` and future `expires_at` back.
5. `GET /api/v1/admin/graph/subscription-status` (Postman request 5) —
   confirm `status: active`.
6. Send a real test email to `customer_complain@avocarbon.com` → tail
   production logs for
   `Graph webhook: new message ... — fetch/classify/ingest not wired yet`
   arriving within a few seconds. This confirms the full loop: Graph → your
   webhook → clientState check → background task, end to end. (The message
   is only logged, not processed further — that's tasks 2-4.)
7. To test renewal without waiting ~2.9 days: temporarily lower
   `GRAPH_SUBSCRIPTION_MAX_MINUTES` (e.g. to `15`) and
   `GRAPH_SUBSCRIPTION_RENEW_MARGIN_MINUTES` (e.g. to `10`) in production,
   re-subscribe, and watch the scheduler log `Renewed Graph subscription ...`
   on its next 30-min tick. Revert the values afterwards.

### Push & deploy

This code has not been pushed yet. Standard flow for this repo:
```bash
git checkout -b feature/internal-intake-graph-subscription
git add .
git commit -m "Add Graph mailbox subscription webhook (Task 1 of internal intake agent)"
git push -u origin feature/internal-intake-graph-subscription
```
then open a PR / merge per however this repo deploys to
`complaint-back.azurewebsites.net` (CI/CD pipeline or manual Azure deploy —
confirm which with whoever manages deployments if unsure). The database
migration (`alembic upgrade head`, adds `graph_subscription` table) has
already been applied against the configured `DATABASE_URL` in this session —
confirm that's the same database production points at; if it's a separate
prod DB, run the migration there too before/during deploy.

### Known limitations / not yet done

- `_handle_notification()` in `graph_webhook.py` only logs — actual
  fetch/classify/extract/ingest is tasks 2–4.
- No delete/cleanup admin endpoint yet (only create + status); can be added
  if a subscription ever needs to be torn down manually.
- `docs/mcp-intake-openapi.json` / the external agent path stay live in
  parallel until task 6 (cutover) — nothing here removes the external agent.

### Resolution log (how the blockers were actually cleared)

1. Dedicated app registration (`Complaint-Intake-Agent`) created with only
   `Mail.Read` + `Mail.ReadWrite`, admin-consented — replacing the shared
   "Audit-app" credentials.
2. Exchange Application Access Policy applied by the M365 admin, scoping
   that app id to `customer_complain@avocarbon.com` only.
3. `GRAPH_NOTIFICATION_URL` in Azure App Service → Configuration →
   Application settings corrected from the wrong placeholder path
   (`/api/webhooks/graph-notifications`) to the real route
   (`/api/v1/graph/webhook/mailbox`) — the first `POST /subscriptions` call
   failed with `ValidationError: HTTP status code is 'NotFound'` until this
   was fixed, which is what confirmed the root cause.
4. `graph_client.py` was updated to surface Graph's actual error body on
   failed requests (previously only "400 Bad Request" with no detail) —
   this is what made the `NotFound` diagnosis possible instead of guessing.
5. `python test_graph_intake_setup.py --base-url https://complaint-back.azurewebsites.net --subscribe`
   run successfully — subscription created, `subscription_id` + `expires_at`
   returned, confirmed `active` via `/admin/graph/subscription-status`.

### Status: **✅ DONE.** Live Graph subscription active on
`customer_complain@avocarbon.com`, auto-renewing every 30 min via the
scheduler job. `_handle_notification()` in `graph_webhook.py` still only
logs the incoming message id — actual fetch/classify/ingest is Task 2+.

---

## Task 2 — Internal email-fetch service

**Goal:** when `_handle_notification()` in `graph_webhook.py` receives a new
message id, fetch the real message (subject, body, sender, `conversationId`,
attachments) from Microsoft Graph — using the same `graph_client.py`
auth/token plumbing already built in Task 1 — instead of just logging it.

### What was built

| Piece | File |
|---|---|
| Fetch a message's metadata/body, shaped close to `EmailIntakeCreate` | `app/services/graph_email_fetch_service.py::fetch_message` |
| Fetch attachment metadata + content (`fileAttachment` only) | `app/services/graph_email_fetch_service.py::fetch_message_attachments` |
| Mark-as-read / move-to-folder (built, **not yet called anywhere** — see decision below) | `mark_message_read`, `move_message_to_folder` |
| Webhook now fetches + logs real message details instead of the placeholder log line | `app/api/routes/graph_webhook.py::_handle_notification` |
| Read-only diagnostic step against a real mailbox message | `test_graph_intake_setup.py` step 4 ("Fetch a real message") |

### Design decision (2026-08-17): mailbox is NOT mutated yet

`mark_message_read()` and `move_message_to_folder()` exist and are tested in
isolation, but `_handle_notification()` deliberately does **not** call them
yet. Reasoning: Task 3 (classify/extract) and Task 4 (call
`EmailIntakeService.ingest()`) aren't wired in yet, so marking a message read
or filing it into "Processed" now would file it away as handled before it's
actually become an intake — if Task 3/4 have a bug on first deploy, those
messages would need to be manually moved back to Inbox to reprocess. These
two mailbox-mutating calls will be invoked from Task 4, only after
`EmailIntakeService.ingest()` returns successfully for that message.

### Open question for Task 4: attachment bytes vs. `download_url`

`app/services/intake_attachments.py::process_intake_attachments()` expects
an attachment dict with a `download_url` it fetches itself via an
**unauthenticated** `requests.get()` — that's how the external agent's
short-lived Graph-signed URLs work today. `fetch_message_attachments()`
instead returns raw `content_bytes` already downloaded (Graph attachment
content needs our own bearer token, not a plain public URL). Task 4 needs to
either (a) upload `content_bytes` to blob storage directly, bypassing
`process_intake_attachments()`'s own download step, or (b) extend that
function to accept pre-fetched bytes as an alternative to `download_url`.
Not decided yet — flagging so Task 4 doesn't rediscover this from scratch.

### Known limitations

- Attachments over ~3MB aren't inlined by Graph as `contentBytes` —
  `fetch_message_attachments()` records these as `status: "too_large"`
  rather than fetching them via the streaming `$value` endpoint (not
  implemented). Revisit if large attachments turn out to be common on this
  mailbox.
- Only `#microsoft.graph.fileAttachment` is handled. `itemAttachment`
  (a forwarded email as an attachment) and `referenceAttachment` (a
  OneDrive/SharePoint link) are recorded as `status: "unsupported_type"`,
  not fetched.
- Uses `internetMessageId` (RFC 5322 Message-ID) as `source_message_id` when
  available, matching what the external agent sends today — falls back to
  the Graph message id if missing.

### How to test

```bash
python test_graph_intake_setup.py
```
Step 4 fetches the real top message in Inbox (read-only, does not mark it
read or move it) and prints subject/sender/conversation_id/attachment count
— confirmed working against a live mailbox message on 2026-08-17.

- Capture `conversationId` from the fetched message and pass it through to
  `EmailIntakeService.ingest()` — the follow-up/threading logic already
  exists and needs no new work (see "Client replies" below), it just needs
  `conversation_id` populated correctly by this fetch step.

### Client replies (found + fixed during Task 1 review, 2026-08-14)

A customer reply is just another `created` message event on the same
`conversationId` — no special handling needed at the webhook/subscription
level. `EmailIntakeService.ingest()` already matches it to the open intake
and attaches it instead of creating a duplicate
(`app/services/email_intake_service.py:260-...`, status
`attached_to_existing`).

Two gaps found in that existing follow-up path were fixed directly (not
gated on Task 2, since they apply to any caller — external agent or
internal):
- **Reply attachments were silently dropped** — the follow-up branch only
  stored `subject`/`raw_body` text, never called
  `process_intake_attachments()`. Fixed: reply attachments are now
  downloaded/stored the same way as first-contact attachments, and if the
  thread was already promoted, immediately linked to the existing complaint.
- **No one was notified on a reply** — fixed: `_notify_followup()` now
  emails the assigned CQT (if promoted) or the original QM/PM recipients
  (if still pending review).

**Known limitation, kept as-is by decision (2026-08-14):** the Graph
subscription only watches the mailbox's **Inbox** folder
(`/users/{mailbox}/mailFolders('Inbox')/messages`). If an Outlook rule
auto-files a customer reply into a different folder before it lands in
Inbox, no notification fires for it at all — the reply would sit unprocessed
until someone finds it manually. Decided to keep Inbox-only for now (simpler,
matches current mailbox usage) rather than widen to the whole mailbox
(noisier — fires on every Sent/Draft/Deleted/Junk item too, and requires
recreating the subscription). **Action item: confirm with whoever manages
the claims mailbox that no rule moves incoming customer mail out of Inbox.**
If one exists, this needs revisiting.
