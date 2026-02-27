import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText



SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASSWORD")


async def send_email(
    subject: str,
    recipients: list[str],
    body_html: str,
    cc: list[str] | None = None
):
    """Send email via SMTP."""
    msg = MIMEMultipart("alternative")
    msg["From"] = SMTP_USER
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
 
    if cc:
        msg["Cc"] = ", ".join(cc)
 
    msg.attach(MIMEText(body_html, "html"))
    all_recipients = recipients + (cc or [])
 
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.sendmail(SMTP_USER, all_recipients, msg.as_string())
        return {"status": "Email sent successfully!"}
    except Exception as e:
        raise Exception(f"Error sending email: {str(e)}")
