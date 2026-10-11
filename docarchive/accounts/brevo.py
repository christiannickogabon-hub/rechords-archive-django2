"""Transactional email through Brevo (https://brevo.com)."""
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

API_URL = "https://api.brevo.com/v3/smtp/email"
TIMEOUT = 12


def is_configured():
    return bool(getattr(settings, "BREVO_API_KEY", "") and getattr(settings, "BREVO_SENDER_EMAIL", ""))


def send_email(to_email, to_name, subject, html, text=""):
    """Return (sent, error_message). Never raises."""
    if not is_configured():
        return False, "Brevo is not configured on this server."
    if not to_email:
        return False, "That account has no email address on file."

    payload = {
        "sender": {"name": settings.BREVO_SENDER_NAME, "email": settings.BREVO_SENDER_EMAIL},
        "to": [{"email": to_email, "name": to_name or to_email}],
        "subject": subject,
        "htmlContent": html,
    }
    if text:
        payload["textContent"] = text

    try:
        response = requests.post(
            API_URL,
            json=payload,
            headers={"api-key": settings.BREVO_API_KEY, "accept": "application/json",
                     "content-type": "application/json"},
            timeout=TIMEOUT,
        )
    except requests.exceptions.RequestException as exc:
        logger.exception("Brevo request failed")
        return False, f"Could not reach the email service ({type(exc).__name__})."

    if response.status_code in (200, 201, 202):
        return True, ""

    # Brevo returns {"code": "...", "message": "..."} on failure.
    detail = ""
    try:
        detail = response.json().get("message", "")
    except ValueError:
        detail = response.text[:120]
    logger.error("Brevo rejected the message: %s %s", response.status_code, detail)
    return False, f"The email service refused the message ({response.status_code}). {detail}".strip()

