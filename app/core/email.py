from __future__ import annotations
import asyncio
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

SMTP_HOST = os.getenv("SMTP_HOST", "localhost")
SMTP_PORT = int(os.getenv("SMTP_PORT", "25"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASSWORD", "")

# Set EMAIL_DRY_RUN=true (e.g. in a test/.env) to skip the real SMTP send —
# logs what WOULD have been sent instead. Leave unset/false in production.
# Lets QA scripts (e.g. test_email_intake_followup_flow.py) exercise the full
# intake/notification code path without emailing real plant contacts.
EMAIL_DRY_RUN = os.getenv("EMAIL_DRY_RUN", "false").strip().lower() == "true"


def _send_sync(
    subject: str,
    recipients: list[str],
    body_html: str,
    cc: list[str] | None,
) -> None:
    """
    Blocking SMTP send — always called via run_in_executor, never directly.
    Raises on any failure so the async caller can persist the error in the outbox.
    """
    msg = MIMEMultipart("alternative")
    msg["From"] = SMTP_USER
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg.attach(MIMEText(body_html, "html"))

    all_recipients = recipients + (cc or [])

    if EMAIL_DRY_RUN:
        logger.info(
            "EMAIL_DRY_RUN — NOT actually sending. Would have sent subject=%r to=%s",
            subject,
            all_recipients,
        )
        return

    logger.debug(
        "SMTP send: host=%s port=%s user=%r to=%s",
        SMTP_HOST,
        SMTP_PORT,
        SMTP_USER,
        all_recipients,
    )

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        # if SMTP_USER and SMTP_PASS:
        #     server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, all_recipients, msg.as_string())

    logger.info("SMTP send OK — subject=%r to=%s", subject, all_recipients)


async def send_email(
    subject: str,
    recipients: list[str],
    body_html: str,
    cc: list[str] | None = None,
) -> None:
    """
    Async wrapper — offloads blocking SMTP to a thread pool via run_in_executor.
    Raises on failure so escalation_service can persist the error for retry.
    """
    if not recipients:
        raise ValueError("send_email called with an empty recipients list")

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _send_sync, subject, recipients, body_html, cc)
