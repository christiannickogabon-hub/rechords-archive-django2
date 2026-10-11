import os
import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models


def avatar_upload_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    return f"avatars/{uuid.uuid4().hex}{ext}"


class Office(models.Model):
    """An office or division of the municipal hall."""

    name = models.CharField("Office or division", max_length=120, unique=True)
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def active_members(self):
        return self.members.filter(is_active=True)


class User(AbstractUser):
    """Records-office user: Super administrator, Administrator or Staff user."""

    class Role(models.TextChoices):
        SUPERADMIN = "SUPER", "Super administrator"
        ADMIN = "ADMIN", "Administrator"
        USER = "USER", "Staff user"

    role = models.CharField(max_length=10, choices=Role.choices, default=Role.USER)
    office = models.ForeignKey(
        Office, on_delete=models.SET_NULL, blank=True, null=True,
        related_name="members", verbose_name="Office or division",
    )
    position = models.CharField(max_length=120, blank=True)
    contact_number = models.CharField(max_length=32, blank=True)
    avatar = models.ImageField("Profile picture", upload_to=avatar_upload_path, blank=True, null=True)

    class Approval(models.TextChoices):
        PENDING = "PENDING", "Awaiting approval"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    # Self-registered accounts stay locked until a super administrator approves them.
    approval_status = models.CharField(max_length=10, choices=Approval.choices, default=Approval.APPROVED)
    approved_by = models.ForeignKey(
        "self", on_delete=models.SET_NULL, blank=True, null=True, related_name="approved_accounts"
    )
    approved_at = models.DateTimeField(blank=True, null=True)
    requested_role = models.CharField(
        "Requested role", max_length=10, blank=True,
        help_text="What a self-registered user asked for; the super administrator decides the real role.",
    )

    # Second factor, required for super administrators only: a one-time code emailed at sign-in.
    login_code_hash = models.CharField(max_length=128, blank=True)
    login_code_sent_at = models.DateTimeField(blank=True, null=True)

    # Separate pair so a password reset in progress cannot interfere with a sign-in code.
    reset_code_hash = models.CharField(max_length=128, blank=True)
    reset_code_sent_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["last_name", "first_name", "username"]

    def __str__(self):
        full = self.get_full_name()
        return f"{full} ({self.username})" if full else self.username

    @property
    def is_archive_admin(self) -> bool:
        """Runs the archive itself. Super administrators manage accounts, not documents."""
        return self.is_superuser or self.role == self.Role.ADMIN

    @property
    def is_super_admin(self) -> bool:
        """May add accounts — the only role allowed to, Django superusers included."""
        return self.role == self.Role.SUPERADMIN

    @property
    def can_view_accounts(self) -> bool:
        return self.is_archive_admin or self.is_super_admin

    @property
    def role_label(self) -> str:
        if self.role == self.Role.SUPERADMIN:
            return "Super administrator"
        return "Administrator" if self.is_archive_admin else "Staff user"

    @property
    def is_awaiting_approval(self) -> bool:
        return self.approval_status == self.Approval.PENDING

    @property
    def needs_second_factor(self) -> bool:
        """Super administrators must pass an authenticator check at sign-in."""
        return self.is_super_admin

    @property
    def avatar_url(self) -> str:
        """Avatar URL, falling back to the local copy for files uploaded before Cloudinary."""
        if not self.avatar:
            return ""
        from pathlib import Path

        from django.conf import settings

        local = Path(settings.MEDIA_ROOT) / self.avatar.name
        if local.is_file():
            media = settings.MEDIA_URL if settings.MEDIA_URL.startswith("/") else "/" + settings.MEDIA_URL
            return media + self.avatar.name
        try:
            return self.avatar.url
        except Exception:
            return ""

    @property
    def initials(self) -> str:
        letters = (self.first_name[:1] + self.last_name[:1]).upper()
        return letters or self.username[:2].upper()
