"""Forgot-password codes, emailed through Brevo."""
import logging

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone

from . import brevo, twofactor

logger = logging.getLogger(__name__)

PENDING_USER_KEY = "pending_reset_user"
PENDING_STARTED_KEY = "pending_reset_started"
PENDING_ATTEMPTS_KEY = "pending_reset_attempts"

PENDING_TIMEOUT_SECONDS = 900
CODE_TTL_SECONDS = 600
RESEND_COOLDOWN_SECONDS = 60
MAX_ATTEMPTS = 5


def issue_code(user):
    code = twofactor.new_code()
    user.reset_code_hash = make_password(code)
    user.reset_code_sent_at = timezone.now()
    user.save(update_fields=["reset_code_hash", "reset_code_sent_at"])
    return code


def code_is_fresh(user):
    if not user.reset_code_hash or not user.reset_code_sent_at:
        return False
    return (timezone.now() - user.reset_code_sent_at).total_seconds() < CODE_TTL_SECONDS


def seconds_since_sent(user):
    if not user.reset_code_sent_at:
        return None
    return (timezone.now() - user.reset_code_sent_at).total_seconds()


def verify(user, code):
    if not code_is_fresh(user):
        return False
    return check_password(code.strip().replace(" ", ""), user.reset_code_hash)


def clear_code(user):
    user.reset_code_hash = ""
    user.reset_code_sent_at = None
    user.save(update_fields=["reset_code_hash", "reset_code_sent_at"])


def send_code(user):
    """Issue and email a reset code. Returns (sent, message)."""
    code = issue_code(user)
    minutes = CODE_TTL_SECONDS // 60

    subject = "Reset your Carigara Document Archive password"
    html = f"""
      <p>Hello {user.get_full_name() or user.username},</p>
      <p>Someone asked to reset the password for <strong>{user.username}</strong>. Your code is:</p>
      <p style="font-size:28px;font-weight:700;letter-spacing:.18em;margin:16px 0">{code}</p>
      <p>It expires in {minutes} minutes and can be used once.</p>
      <p style="color:#64748b;font-size:13px">
        If this was not you, ignore this email — your password stays as it is — and tell the records office.
      </p>
    """
    text = f"Your password reset code is {code}. It expires in {minutes} minutes."

    sent, error = brevo.send_email(user.email, user.get_full_name(), subject, html, text)
    if sent:
        return True, ""

    if settings.DEBUG:
        logger.warning("PASSWORD RESET CODE for %s: %s  (%s)", user.username, code, error)
        return True, ""

    clear_code(user)
    return False, error
