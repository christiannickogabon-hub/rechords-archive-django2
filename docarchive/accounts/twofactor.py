"""Emailed one-time codes for super administrators, delivered through Brevo."""
import logging
import secrets

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone

from . import brevo

logger = logging.getLogger(__name__)

PENDING_USER_KEY = "pending_2fa_user"
PENDING_STARTED_KEY = "pending_2fa_started"
PENDING_ATTEMPTS_KEY = "pending_2fa_attempts"

PENDING_TIMEOUT_SECONDS = 900      # how long the half-finished sign-in lives
CODE_TTL_SECONDS = 600             # how long an emailed code stays valid
RESEND_COOLDOWN_SECONDS = 60
MAX_ATTEMPTS = 5


def new_code():
    return f"{secrets.randbelow(1_000_000):06d}"


def issue_code(user):
    """Store a fresh code against the account and return the plain code."""
    code = new_code()
    user.login_code_hash = make_password(code)
    user.login_code_sent_at = timezone.now()
    user.save(update_fields=["login_code_hash", "login_code_sent_at"])
    return code


def code_is_fresh(user):
    if not user.login_code_hash or not user.login_code_sent_at:
        return False
    return (timezone.now() - user.login_code_sent_at).total_seconds() < CODE_TTL_SECONDS


def seconds_since_sent(user):
    if not user.login_code_sent_at:
        return None
    return (timezone.now() - user.login_code_sent_at).total_seconds()


def verify(user, code):
    if not code_is_fresh(user):
        return False
    return check_password(code.strip().replace(" ", ""), user.login_code_hash)


def clear_code(user):
    user.login_code_hash = ""
    user.login_code_sent_at = None
    user.save(update_fields=["login_code_hash", "login_code_sent_at"])


def mask_email(email):
    if not email or "@" not in email:
        return "your email address"
    name, domain = email.split("@", 1)
    head = name[:2] if len(name) > 2 else name[:1]
    return f"{head}{'•' * max(len(name) - len(head), 2)}@{domain}"


def send_code(user):
    """Issue and email a code. Returns (sent, message_for_the_user)."""
    code = issue_code(user)

    subject = "Your sign-in code for the Carigara Document Archive"
    html = f"""
      <p>Hello {user.get_full_name() or user.username},</p>
      <p>Your sign-in code is:</p>
      <p style="font-size:28px;font-weight:700;letter-spacing:.18em;margin:16px 0">{code}</p>
      <p>It expires in {CODE_TTL_SECONDS // 60} minutes and can be used once.</p>
      <p style="color:#64748b;font-size:13px">
        If you did not try to sign in, ignore this message and tell the records office —
        someone may know your password.
      </p>
    """
    text = (
        f"Your sign-in code for the Carigara Document Archive is {code}. "
        f"It expires in {CODE_TTL_SECONDS // 60} minutes."
    )

    sent, error = brevo.send_email(user.email, user.get_full_name(), subject, html, text)
    if sent:
        return True, f"We sent a code to {mask_email(user.email)}."

    # Without a mail service there is no way to deliver the code. In development
    # put it in the server log so the flow can still be exercised; never in production.
    if settings.DEBUG:
        logger.warning("SIGN-IN CODE for %s: %s  (%s)", user.username, code, error)
        return True, f"Email is not configured, so the code was written to the server log. ({error})"

    clear_code(user)
    return False, error
