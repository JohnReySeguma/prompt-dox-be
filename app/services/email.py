"""Transactional email over SMTP."""

import logging
from email.message import EmailMessage
from email.utils import parseaddr

import aiosmtplib

from app.core.config import settings

logger = logging.getLogger("promptdox.email")


async def send_email(to: str, subject: str, text_body: str, html_body: str | None = None) -> bool:
    if not settings.smtp_configured:
        logger.warning("SMTP is not configured; skipping email to %s", to)
        return False

    message = EmailMessage()
    message["From"] = settings.email_from or settings.smtp_user
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text_body)
    if html_body:
        message.add_alternative(html_body, subtype="html")

    # Port 465 uses implicit TLS; other ports (587, 25) upgrade with STARTTLS.
    implicit_tls = settings.smtp_port == 465
    try:
        await aiosmtplib.send(
            message,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_pass,
            use_tls=implicit_tls,
            start_tls=not implicit_tls,
            sender=parseaddr(message["From"])[1] or settings.smtp_user,
            timeout=20,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to send email to %s", to)
        return False
    logger.info("Sent '%s' email to %s", subject, to)
    return True


async def send_password_reset_email(to: str, name: str, link: str) -> None:
    minutes = settings.password_reset_expire_minutes
    text_body = (
        f"Hi {name},\n\n"
        "We received a request to reset your PromptDox password. "
        f"Use the link below within {minutes} minutes:\n\n{link}\n\n"
        "If you didn't ask for this, you can ignore this email."
    )
    html_body = (
        f"<p>Hi {name},</p>"
        "<p>We received a request to reset your PromptDox password. "
        f"Use the link below within {minutes} minutes:</p>"
        f'<p><a href="{link}">Reset your password</a></p>'
        "<p>If you didn't ask for this, you can ignore this email.</p>"
    )
    await send_email(to, "Reset your PromptDox password", text_body, html_body)
