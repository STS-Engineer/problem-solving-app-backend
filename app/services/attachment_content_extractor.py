"""
app/services/attachment_content_extractor.py

Turns a fetched attachment's raw bytes (from
graph_email_fetch_service.fetch_message_attachments) into something the
classification LLM can actually read — closing the gap flagged in Task 3:
the external agent reads PDFs/Excel files and analyzes photos when the
complaint's substance is only in an attachment (empty body + "see attached
report"); the internal agent didn't, until now.

Two extraction strategies, depending on attachment type:
  - PDF / Excel  -> extract text server-side (pypdf / openpyxl), fed to the
                    LLM as plain text alongside the email body.
  - Images       -> NOT text-extracted here. Passed through as base64 data
                    URIs for the caller to attach as multimodal image
                    content parts on the chat completion call, since a
                    vision-capable model reading the photo directly is far
                    more useful than any text description we could write.

Anything else (Word docs, unknown types) is left alone — noted as a
filename only, same as before. Never raises for a single bad/corrupt
attachment; failures are recorded in the returned text instead.
"""

from __future__ import annotations

import base64
import io
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_MAX_TEXT_CHARS = 6000  # per attachment, keeps token usage bounded
_MAX_IMAGES = 5  # cap how many images we embed per email (cost/latency)


def is_pdf(mime_type: Optional[str]) -> bool:
    return (mime_type or "").lower() == "application/pdf"


def is_excel(mime_type: Optional[str]) -> bool:
    mt = (mime_type or "").lower()
    return mt in (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
    )


def is_image(mime_type: Optional[str]) -> bool:
    return (mime_type or "").lower().startswith("image/")


def extract_pdf_text(content: bytes) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        pages = [page.extract_text() or "" for page in reader.pages]
        text = "\n".join(pages).strip()
        return text[:_MAX_TEXT_CHARS] or "(PDF contains no extractable text — likely scanned/image-only)"
    except Exception as exc:
        logger.warning("attachment_content_extractor: failed to read PDF: %s", exc)
        return f"(failed to read PDF content: {exc})"


def extract_excel_text(content: bytes) -> str:
    try:
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
        lines: list[str] = []
        for sheet in wb.worksheets:
            lines.append(f"[Sheet: {sheet.title}]")
            for row in sheet.iter_rows(values_only=True):
                if any(cell is not None for cell in row):
                    lines.append(", ".join("" if c is None else str(c) for c in row))
            if len("\n".join(lines)) > _MAX_TEXT_CHARS:
                break
        text = "\n".join(lines).strip()
        return text[:_MAX_TEXT_CHARS] or "(spreadsheet appears empty)"
    except Exception as exc:
        logger.warning("attachment_content_extractor: failed to read Excel file: %s", exc)
        return f"(failed to read spreadsheet content: {exc})"


def build_attachment_context(
    attachments: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """
    Splits fetched attachments into:
      - text_blocks: a single string with extracted PDF/Excel text, labeled
        per file, ready to append to the classification prompt.
      - image_parts: a list of OpenAI chat-completion image content parts
        (`{"type": "image_url", "image_url": {"url": "data:..."}}`), ready
        to splice into a multimodal user message alongside the text.

    Only attachments with status == "fetched" (content actually downloaded
    — see fetch_message_attachments) are considered; "too_large" /
    "unsupported_type" / "skipped_inline" ones are silently left out here
    (they're already recorded on the intake's attachment metadata
    separately — this function only feeds the LLM, it doesn't decide what
    gets stored).
    """
    text_sections: list[str] = []
    image_parts: list[dict[str, Any]] = []

    for att in attachments or []:
        if att.get("status") != "fetched":
            continue
        content = att.get("content_bytes")
        if not content:
            continue
        filename = att.get("filename", "file")
        mime_type = att.get("mime_type")

        if is_pdf(mime_type):
            text = extract_pdf_text(content)
            text_sections.append(f"--- Attachment (PDF): {filename} ---\n{text}")
        elif is_excel(mime_type):
            text = extract_excel_text(content)
            text_sections.append(f"--- Attachment (Excel): {filename} ---\n{text}")
        elif is_image(mime_type) and len(image_parts) < _MAX_IMAGES:
            b64 = base64.b64encode(content).decode("ascii")
            image_parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{b64}"},
                }
            )
            text_sections.append(f"--- Attachment (image, shown below): {filename} ---")
        # else: unhandled type (Word doc, etc.) — filename-only context is
        # already included via attachment_summary in the caller's prompt.

    return "\n\n".join(text_sections), image_parts
