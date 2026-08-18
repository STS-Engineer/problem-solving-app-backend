"""
app/services/graph_email_classify_service.py

Task 3 of the internal intake agent migration: classify a fetched email
(genuine customer complaint or not) and extract structured fields — the
internal replacement for what the external LLM agent's system prompt
(docs/intake-agent-prompt.md) currently does inside the ChatGPT/Outlook
connector.

The controlled vocabularies are pulled directly from app.core.form_options
(the same lists evaluate_completeness() validates against at promote time),
so the prompt can never drift out of sync with what the backend accepts.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from openai import OpenAI, OpenAIError

from app.core.config import settings
from app.core.form_options import CLAIM_TYPES, CUSTOMERS, DEFECTS, PLANTS, PROCESSES, PRODUCT_LINES

logger = logging.getLogger(__name__)

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text(html: str) -> str:
    """Best-effort tag strip — good enough for feeding email HTML to the LLM
    without wasting tokens on markup. Not a full HTML parser."""
    text = re.sub(r"(?is)<(script|style).*?>.*?(</\1>)", " ", html)
    text = _HTML_TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


_SYSTEM = f"""You are the AVOCarbon Complaint Intake Agent. You read one email at a time
from the shared claims mailbox and decide whether it is a genuine customer
product quality complaint, then extract structured data from it.

## STEP 1 — CLASSIFY
Set is_complaint=true ONLY for genuine customer product complaints / quality
claims. Set is_complaint=false for: spam, newsletters, auto-replies
("out of office"), delivery receipts, internal AVOCarbon mail, pure
thank-you/acknowledgement replies with no new complaint content. Put a short
reason in skip_reason when is_complaint=false.

## STEP 2 — EXTRACT (only if is_complaint=true)
Read the whole email (subject + body). NEVER invent data — if a field is not
stated or clearly implied, leave it "" (empty string). If the subject and
body conflict, follow the body and note the discrepancy in ai_notes.

CONTROLLED VALUES — output EXACTLY one of these (map synonyms yourself,
e.g. "Robert Bosch GmbH" -> "BOSCH"); if none fits, leave the field EMPTY:
- quality_issue_warranty: {" | ".join(CLAIM_TYPES)}
- product_line: {" | ".join(PRODUCT_LINES)}
- defects: {" | ".join(DEFECTS)}
- avocarbon_plant: {" | ".join(PLANTS)}
- potential_avocarbon_process_linked_to_problem: {" | ".join(PROCESSES)}
- customer: {" | ".join(CUSTOMERS)}

Free-text fields (fill when present, never invent):
- complaint_name: short title of the problem
- customer_plant_name: customer site/location
- avocarbon_product_type: our product (e.g. RODCHOKE, brush ref)
- complaint_description: 2-3 sentence factual summary of the problem
- customer_complaint_date: ISO date YYYY-MM-DD if a date is stated
- concerned_application: end application if mentioned

## STEP 3 — DETECT PLANT (optional, only if confident)
If the email clearly identifies the responsible AVOCarbon plant, set
detected_plant to one of: {" | ".join(PLANTS)}. If not sure, leave it "".

## STEP 4 — MISSING FIELDS
List in missing_fields the important fields you could NOT determine, using
the exact field names above. Add a short ai_notes explaining what was
unclear (or why is_complaint was set to false).

Return ONLY valid JSON matching this exact structure. No markdown fences,
no explanation, no extra keys:
{{
  "is_complaint": true,
  "skip_reason": "",
  "extracted_data": {{
    "complaint_name": "",
    "customer": "",
    "customer_plant_name": "",
    "avocarbon_product_type": "",
    "product_line": "",
    "quality_issue_warranty": "",
    "potential_avocarbon_process_linked_to_problem": "",
    "defects": "",
    "complaint_description": "",
    "customer_complaint_date": "",
    "concerned_application": "",
    "avocarbon_plant": ""
  }},
  "detected_plant": "",
  "missing_fields": [],
  "ai_notes": ""
}}
"""

_USER_TEMPLATE = """=== EMAIL ===
From: {sender_name} <{sender_email}>
Subject: {subject}
Attachments: {attachment_summary}

--- Body ---
{body}
"""


def _drop_empty_strings(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in ("", None)}


def classify_and_extract(
    *,
    subject: Optional[str],
    sender_email: Optional[str],
    sender_name: Optional[str],
    raw_body: Optional[str],
    raw_html: Optional[str],
    attachments: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """
    One-shot LLM call: classify + extract. Returns a dict:
      is_complaint : bool
      skip_reason  : str
      extracted_data : dict (controlled fields normalized-ish, free text as-is)
      detected_plant : str | None
      missing_fields : list[str]
      ai_notes : str

    On any failure (OpenAI error, bad JSON), returns a safe default with
    is_complaint=False and skip_reason="classification_failed" — never
    raises, so a bad LLM response can't take down the webhook's background
    task. Failures are logged loudly since silently skipping a real
    complaint is worse than a false positive.

    Note: attachment CONTENT is not analyzed here (the external agent reads
    PDFs/Excels to extract data hidden in attachments — not replicated yet).
    Only filenames/descriptions are passed as context. Known limitation —
    see docs/internal-intake-agent-migration.md Task 3.
    """
    body = raw_body or (_html_to_text(raw_html) if raw_html else "") or "(empty body)"
    attachment_summary = (
        ", ".join(a.get("filename", "file") for a in (attachments or [])) or "none"
    )

    user_msg = _USER_TEMPLATE.format(
        sender_name=sender_name or "",
        sender_email=sender_email or "",
        subject=subject or "",
        attachment_summary=attachment_summary,
        body=body[:8000],  # keep token usage bounded on very long threads
    )

    default_failure = {
        "is_complaint": False,
        "skip_reason": "classification_failed",
        "extracted_data": {},
        "detected_plant": None,
        "missing_fields": [],
        "ai_notes": "",
    }

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    try:
        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.1,
            max_completion_tokens=settings.OPENAI_MAX_TOKENS,
            timeout=30,
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"^```[a-z]*\s*", "", raw, flags=re.MULTILINE).strip("` \n")
        result = json.loads(raw)
    except OpenAIError as exc:
        logger.error("classify_and_extract: OpenAI error: %s", exc)
        return default_failure
    except json.JSONDecodeError as exc:
        logger.error("classify_and_extract: JSON parse error: %s — raw: %.500s", exc, raw)
        return default_failure
    except Exception as exc:
        logger.error("classify_and_extract: unexpected error: %s", exc)
        return default_failure

    result["extracted_data"] = _drop_empty_strings(result.get("extracted_data") or {})
    result["detected_plant"] = result.get("detected_plant") or None
    result["missing_fields"] = result.get("missing_fields") or []
    result["is_complaint"] = bool(result.get("is_complaint"))

    logger.info(
        "classify_and_extract: is_complaint=%s fields=%s missing=%s",
        result["is_complaint"],
        list(result["extracted_data"].keys()),
        result["missing_fields"],
    )
    return result
