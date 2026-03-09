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
SMTP_TIMEOUT_S = int(os.getenv("SMTP_TIMEOUT_S", "10"))  # seconds before giving up


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

    logger.debug(
        "SMTP send: host=%s port=%s user=%r to=%s",
        SMTP_HOST, SMTP_PORT, SMTP_USER, all_recipients,
    )

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_S) as server:
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