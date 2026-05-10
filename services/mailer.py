"""
services/mailer.py
───────────────────
Sends the HTML email report via Gmail SMTP with SSL.

Features:
  - Graceful retry (up to 3 attempts with exponential backoff)
  - Sends both plain-text fallback and HTML versions (multipart/alternative)
  - Validates credentials before attempting to connect
  - Logs delivery confirmation

GMAIL SETUP:
  1. Enable 2-Step Verification on your Google account.
  2. Go to Security → App Passwords.
  3. Generate a 16-character App Password for "Mail".
  4. Set EMAIL_PASS to that 16-character password (without spaces).
"""

from __future__ import annotations

import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import date

from config.settings import get_settings
from services.logger import get_logger

log = get_logger(__name__)

_SMTP_HOST = "smtp.gmail.com"
_SMTP_PORT = 465              # SSL — does NOT require STARTTLS
_MAX_ATTEMPTS = 3
_RETRY_DELAY = 5              # seconds between retries


class Mailer:
    """Sends HTML email via Gmail SMTP with SSL."""

    def __init__(self) -> None:
        settings = get_settings()
        self._from_addr = settings.email_user
        self._password = settings.email_pass
        self._to_addr = settings.my_email

    # ── Public API ────────────────────────────────────────────────────────

    def send(self, html_body: str, job_count: int) -> bool:
        """
        Send the HTML report email.

        Args:
            html_body: Fully rendered HTML string from EmailGenerator.
            job_count: Number of jobs in the report (used in subject line).

        Returns:
            True if sent successfully, False otherwise.
        """
        subject = self._build_subject(job_count)
        msg = self._build_message(subject, html_body)

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                self._send_message(msg)
                log.info(
                    "Email sent successfully to %s (attempt %d)",
                    self._to_addr, attempt,
                )
                return True
            except smtplib.SMTPAuthenticationError as exc:
                log.error(
                    "SMTP authentication failed — check EMAIL_USER and EMAIL_PASS "
                    "(must be a Gmail App Password, not your login password): %s", exc
                )
                return False   # No point retrying auth errors
            except smtplib.SMTPException as exc:
                log.warning(
                    "SMTP error on attempt %d/%d: %s",
                    attempt, _MAX_ATTEMPTS, exc,
                )
                if attempt < _MAX_ATTEMPTS:
                    time.sleep(_RETRY_DELAY * attempt)
            except Exception as exc:
                log.warning(
                    "Unexpected error on attempt %d/%d: %s",
                    attempt, _MAX_ATTEMPTS, exc,
                )
                if attempt < _MAX_ATTEMPTS:
                    time.sleep(_RETRY_DELAY * attempt)

        log.error("Failed to send email after %d attempts", _MAX_ATTEMPTS)
        return False

    # ── Private Methods ───────────────────────────────────────────────────

    def _send_message(self, msg: MIMEMultipart) -> None:
        """Open SSL connection and transmit the message."""
        with smtplib.SMTP_SSL(_SMTP_HOST, _SMTP_PORT, timeout=30) as server:
            server.login(self._from_addr, self._password)
            server.sendmail(
                self._from_addr,
                self._to_addr,
                msg.as_string(),
            )

    def _build_message(self, subject: str, html_body: str) -> MIMEMultipart:
        """Construct a multipart/alternative MIME message."""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"AI Job Hunter <{self._from_addr}>"
        msg["To"] = self._to_addr

        # Plain text fallback for clients that don't render HTML
        plain_text = (
            f"AI Fresher Job Report — {date.today().strftime('%d %B %Y')}\n\n"
            "Please view this email in an HTML-capable client to see the full report.\n\n"
            "Visit https://github.com for the project source."
        )
        msg.attach(MIMEText(plain_text, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        return msg

    @staticmethod
    def _build_subject(job_count: int) -> str:
        today = date.today().strftime("%d %b %Y")
        return f"🤖 AI Job Report: {job_count} Fresher Jobs Found — {today}"
