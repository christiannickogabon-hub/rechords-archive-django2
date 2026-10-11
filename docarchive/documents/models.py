import logging
import os
import re
import uuid
from datetime import date

from cloudinary_storage.storage import RawMediaCloudinaryStorage
from django.conf import settings
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone

# Scanned documents, attachments, CSVs and staged scans are PDFs/DOC/DOCX/CSV
# as often as images, so they use Cloudinary's "raw" resource type rather than
# the "image" type the default MediaCloudinaryStorage assumes.
logger = logging.getLogger(__name__)

_raw_storage = RawMediaCloudinaryStorage()


def archive_upload_path(instance, filename):
    """media/archive/<year>/<type>/<uuid>.<ext> — keeps originals from colliding."""
    ext = os.path.splitext(filename)[1].lower()
    return f"archive/{instance.series_year}/{instance.doc_type}/{uuid.uuid4().hex}{ext}"


def attachment_upload_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    return f"attachments/{instance.document.series_year}/{uuid.uuid4().hex}{ext}"


def import_upload_path(instance, filename):
    return f"imports/{timezone.now():%Y/%m}/{uuid.uuid4().hex}.csv"


def scan_stage_upload_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    return f"scan-staging/{instance.batch_id}/{uuid.uuid4().hex}{ext}"


class DocumentType(models.TextChoices):
    MEMORANDUM = "MEMO", "Memorandum"
    INDORSEMENT = "IND", "Endorsement"
    OFFICE_ORDER = "OO", "Office Order"
    EXECUTIVE_ORDER = "EO", "Executive Order"


# Long form used on the printed control number, e.g. "MEMORANDUM No. 012, s. 2026"
TYPE_LONG_NAME = {
    DocumentType.MEMORANDUM: "MEMORANDUM",
    DocumentType.INDORSEMENT: "ENDORSEMENT",
    DocumentType.OFFICE_ORDER: "OFFICE ORDER",
    DocumentType.EXECUTIVE_ORDER: "EXECUTIVE ORDER",
}

# The four above are built in; an administrator can open further folders, so the
# catalogue of series lives in the database rather than in this enum. The four
# keep their codes, which is why everything filed under them is untouched.
FOLDER_ICONS = [
    ("icon-file-signature", "Signed document"),
    ("icon-file-check", "Checked document"),
    ("icon-clipboard-list", "Clipboard"),
    ("icon-scroll-text", "Scroll"),
    ("icon-file-text", "Plain document"),
    ("icon-clipboard", "Clipboard, plain"),
    ("icon-archive", "Archive box"),
    ("icon-inbox", "Inbox"),
    ("icon-shield", "Shield"),
    ("icon-users", "People"),
    ("icon-building-2", "Office"),
    ("icon-paperclip", "Paperclip"),
]

FOLDER_TONES = [
    ("green", "Green"),
    ("teal", "Teal"),
    ("amber", "Amber"),
    ("indigo", "Indigo"),
    ("rose", "Rose"),
    ("slate", "Slate"),
]


class DocumentFolder(models.Model):
    """One series of documents — a card on the overview and a folder in the sidebar.

    Memorandum, Endorsement, Office Order and Executive Order are seeded as
    built-ins and cannot be removed; an administrator may open others.
    """

    code = models.CharField(
        "Code", max_length=8, unique=True,
        help_text="Short code stored on every record, e.g. RES. Letters and digits only.",
    )
    label = models.CharField("Folder name", max_length=40, help_text="e.g. Resolution")
    long_name = models.CharField(
        "Name on the control number", max_length=40,
        help_text="Printed in capitals, e.g. RESOLUTION No. 001, s. 2026.",
    )
    icon = models.CharField(max_length=40, choices=FOLDER_ICONS, default="icon-file-text")
    tone = models.CharField("Colour", max_length=10, choices=FOLDER_TONES, default="slate")
    sort_order = models.PositiveSmallIntegerField(default=100)
    is_builtin = models.BooleanField(default=False, editable=False)
    # A folder that is no longer filed into but still holds records is hidden
    # rather than removed, so its records keep their series and control numbers.
    is_hidden = models.BooleanField("Hidden", default=False)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True,
        related_name="folders_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "label"]
        verbose_name = "document folder"

    def __str__(self):
        return self.label

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        if not self.long_name:
            self.long_name = self.label.upper()
        super().save(*args, **kwargs)

    @property
    def document_count(self):
        return Document.objects.filter(doc_type=self.code, is_deleted=False).count()

    @property
    def can_be_removed(self):
        """Anything empty can go; a folder holding records would orphan them."""
        return self.document_count == 0

    @property
    def removal_blocked_reason(self):
        count = self.document_count
        if count:
            return (f"{count} record{'s are' if count != 1 else ' is'} filed under it. "
                    "Hide the folder instead, or move those records first.")
        return ""


def folder_catalogue(include_hidden=False):
    """The folders in use, ordered. Hidden ones are left out of the cards, the
    the filing choices, but stay reachable so their records keep
    working."""
    folders = DocumentFolder.objects.all()
    if not include_hidden:
        folders = folders.filter(is_hidden=False)
    return list(folders)


def folder_choices():
    return [(f.code, f.label) for f in folder_catalogue()]


def folder_tones():
    """{code: tone} for colouring a record by its series."""
    return {f.code: f.tone for f in folder_catalogue(include_hidden=True)}


def folder_labels():
    """Hidden folders included: their records still open from search results."""
    return {f.code: f.label for f in folder_catalogue(include_hidden=True)}


class DocumentQuerySet(models.QuerySet):
    def visible_to(self, user):
        """Staff users only see public records; soft-deleted ones are always hidden."""
        qs = self.filter(is_deleted=False)
        if user.is_archive_admin:
            return qs
        return qs.filter(classification=Document.Classification.PUBLIC)

    def search(self, term):
        if not term:
            return self
        return self.filter(
            models.Q(subject__icontains=term)
            | models.Q(description__icontains=term)
            | models.Q(keywords__icontains=term)
            | models.Q(originating_office__icontains=term)
            | models.Q(signatory__icontains=term)
            | models.Q(addressee__icontains=term)
        )


class Document(models.Model):
    """One archived record: a Memorandum, Endorsement, Office Order or Executive Order."""

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "In effect"
        SUPERSEDED = "SUPERSEDED", "Superseded"
        REVOKED = "REVOKED", "Revoked"
        ARCHIVED = "ARCHIVED", "Closed and archived"

    class Classification(models.TextChoices):
        PUBLIC = "PUBLIC", "Public"
        INTERNAL = "INTERNAL", "Internal"
        CONFIDENTIAL = "CONFIDENTIAL", "Confidential"

    # No `choices` here: the catalogue is DocumentFolder, and the forms take
    # their options from it so an administrator can open new series.
    doc_type = models.CharField("Document type", max_length=8)
    series_year = models.PositiveIntegerField("Series year", default=date.today().year)
    number = models.PositiveIntegerField(
        "Number", blank=True, null=True,
        help_text="Leave blank to take the next number in this series.",
    )

    subject = models.CharField(max_length=255)
    description = models.TextField("Summary or full text", blank=True)
    date_issued = models.DateField(default=date.today)
    effectivity_date = models.DateField(blank=True, null=True)

    originating_office = models.CharField(max_length=150, blank=True)
    signatory = models.CharField("Signed by", max_length=150, blank=True)
    addressee = models.CharField("Addressed to", max_length=255, blank=True)
    keywords = models.CharField(
        max_length=255, blank=True, help_text="Comma-separated tags used by search."
    )

    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    classification = models.CharField(
        max_length=12, choices=Classification.choices, default=Classification.INTERNAL
    )

    file = models.FileField(
        "Scanned copy", upload_to=archive_upload_path, storage=_raw_storage, blank=True, null=True
    )
    page_count = models.PositiveIntegerField(blank=True, null=True)
    physical_location = models.CharField(
        "Physical file location", max_length=150, blank=True,
        help_text="Where the hard copy sits, e.g. Cabinet 3, Drawer B.",
    )

    # An endorsement usually forwards an earlier document; a superseding order
    # points back at what it replaced. Both use this link.
    related_to = models.ForeignKey(
        "self", on_delete=models.SET_NULL, blank=True, null=True,
        related_name="referenced_by", verbose_name="Refers to document",
    )
    indorsement_level = models.PositiveSmallIntegerField(
        "Endorsement level", blank=True, null=True,
        help_text="1 for 1st Endorsement, 2 for 2nd, and so on.",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="documents_created"
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True,
        related_name="documents_updated",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(blank=True, null=True)

    objects = DocumentQuerySet.as_manager()

    class Meta:
        ordering = ["-series_year", "doc_type", "-number"]
        constraints = [
            models.UniqueConstraint(
                fields=["doc_type", "series_year", "number"],
                name="unique_control_number",
            )
        ]
        indexes = [
            models.Index(fields=["doc_type", "series_year"]),
            models.Index(fields=["date_issued"]),
            models.Index(fields=["subject"]),
        ]

    def __str__(self):
        return f"{self.control_number} — {self.subject}"

    def get_absolute_url(self):
        return reverse("documents:document_detail", args=[self.pk])

    # -- control number -------------------------------------------------
    @property
    def control_number(self) -> str:
        """e.g. 'MEMORANDUM No. 012, s. 2026' or '2nd ENDORSEMENT No. 004, s. 2026'."""
        long_name = TYPE_LONG_NAME.get(self.doc_type) or self.type_long_name
        prefix = ""
        if self.doc_type == DocumentType.INDORSEMENT and self.indorsement_level:
            prefix = f"{ordinal(self.indorsement_level)} "
        number = f"{self.number:03d}" if self.number else "—"
        return f"{prefix}{long_name} No. {number}, s. {self.series_year}"

    @property
    def folder(self):
        return DocumentFolder.objects.filter(code=self.doc_type).first()

    @property
    def type_label(self) -> str:
        """The folder's name, e.g. 'Memorandum'. Falls back to the raw code."""
        folder = self.folder
        return folder.label if folder else self.doc_type

    @property
    def type_long_name(self) -> str:
        folder = self.folder
        return (folder.long_name if folder else self.doc_type).upper()

    @property
    def short_number(self) -> str:
        return f"{self.doc_type}-{self.number:03d}-{self.series_year}" if self.number else "unassigned"

    @classmethod
    def next_number(cls, doc_type, series_year) -> int:
        last = (
            cls.objects.filter(doc_type=doc_type, series_year=series_year)
            .aggregate(models.Max("number"))["number__max"]
        )
        return (last or 0) + 1

    def save(self, *args, **kwargs):
        if self.number is None:
            with transaction.atomic():
                # Lock the series so two clerks saving at once can't take the
                # same number.
                locked = Document.objects.select_for_update().filter(
                    doc_type=self.doc_type, series_year=self.series_year
                )
                last = locked.aggregate(models.Max("number"))["number__max"]
                self.number = (last or 0) + 1
                return super().save(*args, **kwargs)
        return super().save(*args, **kwargs)

    def soft_delete(self):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_deleted", "deleted_at"])

    def restore(self):
        self.is_deleted = False
        self.deleted_at = None
        self.save(update_fields=["is_deleted", "deleted_at"])

    @property
    def keyword_list(self):
        return [k.strip() for k in self.keywords.split(",") if k.strip()]

    @property
    def days_in_bin(self):
        if not self.deleted_at:
            return None
        return (timezone.now() - self.deleted_at).days

    @property
    def due_for_disposal(self):
        """Past the retention window, so a super administrator may approve its destruction."""
        from django.conf import settings as django_settings

        days = self.days_in_bin
        return days is not None and days >= django_settings.RECYCLE_BIN_RETENTION_DAYS

    @property
    def days_until_disposal(self):
        from django.conf import settings as django_settings

        days = self.days_in_bin
        if days is None:
            return None
        return max(django_settings.RECYCLE_BIN_RETENTION_DAYS - days, 0)

    @property
    def file_extension(self):
        return os.path.splitext(self.file.name)[1].lstrip(".").upper() if self.file else ""


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


class Attachment(models.Model):
    """Enclosures filed with the main document."""

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="attachments")
    label = models.CharField(max_length=150)
    file = models.FileField(upload_to=attachment_upload_path, storage=_raw_storage)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at"]

    def __str__(self):
        return self.label


class AccessLog(models.Model):
    """Immutable trail of who touched what. Administrators can read it; nobody edits it."""

    class Action(models.TextChoices):
        LOGIN = "LOGIN", "Signed in"
        LOGOUT = "LOGOUT", "Signed out"
        VIEW = "VIEW", "Opened document"
        DOWNLOAD = "DOWNLOAD", "Downloaded file"
        CREATE = "CREATE", "Filed document"
        UPDATE = "UPDATE", "Edited document"
        DELETE = "DELETE", "Removed document"
        RESTORE = "RESTORE", "Restored document"
        SEARCH = "SEARCH", "Ran a search"
        REPORT = "REPORT", "Ran a report"
        USER_MANAGE = "USER_MANAGE", "Managed an account"
        DENIED = "DENIED", "Access denied"
        IMPORT = "IMPORT", "Bulk import"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="access_logs"
    )
    action = models.CharField(max_length=16, choices=Action.choices)
    document = models.ForeignKey(
        Document, on_delete=models.SET_NULL, blank=True, null=True, related_name="access_logs"
    )
    note = models.CharField(max_length=255, blank=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.CharField(max_length=255, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [models.Index(fields=["-timestamp"]), models.Index(fields=["action"])]

    def __str__(self):
        who = self.user.username if self.user else "anonymous"
        return f"{self.timestamp:%Y-%m-%d %H:%M} {who} {self.action}"

    _OS_PATTERNS = [
        (r"Windows NT", "Windows"),
        (r"Mac OS X", "macOS"),
        (r"iPhone", "iPhone"),
        (r"iPad", "iPad"),
        (r"Android", "Android"),
        (r"Linux", "Linux"),
    ]
    _BROWSER_PATTERNS = [
        (r"Edg/", "Edge"),
        (r"OPR/|Opera", "Opera"),
        (r"CriOS/", "Chrome"),
        (r"Chrome/", "Chrome"),
        (r"FxiOS/", "Firefox"),
        (r"Firefox/", "Firefox"),
        (r"Safari/", "Safari"),
    ]

    @property
    def device_label(self):
        """Human-readable "Browser on OS" derived from the stored user agent string."""
        ua = self.user_agent or ""
        if not ua:
            return "—"
        browser = next((name for pattern, name in self._BROWSER_PATTERNS if re.search(pattern, ua)), None)
        os_name = next((name for pattern, name in self._OS_PATTERNS if re.search(pattern, ua)), None)
        if browser and os_name:
            return f"{browser} on {os_name}"
        return browser or os_name or "Unknown device"

    @staticmethod
    def client_ip(request):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")

    @classmethod
    def record(cls, request, action, document=None, note=""):
        cls.objects.create(
            user=request.user if request.user.is_authenticated else None,
            action=action,
            document=document,
            note=note[:255],
            ip_address=cls.client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:255],
        )


class Notification(models.Model):
    """A short, dismissable alert about document activity, polled by the bell icon in the UI."""

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    verb = models.CharField(max_length=255)
    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, blank=True, null=True, related_name="notifications"
    )
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["recipient", "is_read"])]

    def __str__(self):
        return f"{self.recipient}: {self.verb}"

    def get_absolute_url(self):
        return self.document.get_absolute_url() if self.document else ""

    @classmethod
    def notify(cls, recipients, actor, verb, document=None, email_subject=None, link=""):
        """Fan out one notification per recipient, skipping the actor themself.

        When `email_subject` is given the same message is also emailed, so people
        who are not looking at the bell icon still hear about it. Accounts with no
        email address simply get the in-app notification.
        """
        targets = [r for r in recipients if r.pk != getattr(actor, "pk", None)]
        if email_subject:
            cls._email(targets, actor, verb, email_subject, link)
        cls.objects.bulk_create([
            cls(recipient=recipient, actor=actor, verb=verb, document=document)
            for recipient in targets
        ])


    @staticmethod
    def _email(recipients, actor, verb, subject, link):
        """Best effort: an email problem must never stop the notification itself."""
        from accounts import brevo

        if not brevo.is_configured():
            return
        who = getattr(actor, "get_full_name", lambda: "")() or getattr(actor, "username", "")
        for person in recipients:
            if not person.email:
                continue
            html = (
                f"<p>Hello {person.get_full_name() or person.username},</p>"
                f"<p>{verb}</p>"
                + (f"<p><a href=\"{link}\">Open it in the archive</a></p>" if link else "")
                + (f"<p style='color:#64748b;font-size:13px'>Sent by {who}, "
                   f"Municipality of Carigara Document Archiving System.</p>" if who else "")
            )
            try:
                brevo.send_email(person.email, person.get_full_name(), subject, html, verb)
            except Exception:
                logger.exception("Could not email notification to %s", person.username)



class Dissemination(models.Model):
    """One copy of a document released to one office, and whether they acknowledged it."""

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="disseminations")
    office = models.ForeignKey("accounts.Office", on_delete=models.CASCADE, related_name="disseminations")
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="disseminations_sent"
    )
    sent_at = models.DateTimeField(auto_now_add=True)
    remarks = models.CharField(max_length=255, blank=True)

    received_at = models.DateTimeField(blank=True, null=True)
    received_by = models.CharField("Received by", max_length=120, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True,
        related_name="receipts_recorded",
    )

    class Meta:
        ordering = ["-sent_at"]
        constraints = [
            models.UniqueConstraint(fields=["document", "office"], name="unique_dissemination")
        ]
        indexes = [models.Index(fields=["received_at"])]

    def __str__(self):
        return f"{self.document.control_number} to {self.office}"

    @property
    def is_received(self):
        return self.received_at is not None

    @property
    def days_outstanding(self):
        if self.is_received:
            return None
        return (timezone.now() - self.sent_at).days


class ImportBatch(models.Model):
    """One CSV upload, dry-run validated and then optionally committed.

    The two-phase split matters: nothing in ImportRow ever touches Document
    until commit(), and commit() is all-or-nothing inside one transaction.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Uploaded"
        VALIDATED = "VALIDATED", "Validated"
        COMMITTED = "COMMITTED", "Committed"
        FAILED = "FAILED", "Failed"

    file = models.FileField("CSV file", upload_to=import_upload_path, storage=_raw_storage)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="import_batches"
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    rows_read = models.PositiveIntegerField(default=0)
    rows_ready = models.PositiveIntegerField(default=0)
    rows_skipped = models.PositiveIntegerField(default=0)
    error_summary = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    validated_at = models.DateTimeField(blank=True, null=True)
    committed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Import batch #{self.pk} ({self.get_status_display()})"

    @property
    def has_blocking_errors(self) -> bool:
        return self.rows.filter(row_status=ImportRow.RowStatus.ERROR).exists()


class ImportRow(models.Model):
    """One line of an ImportBatch's CSV. Kept even after commit so an
    administrator can see exactly what happened instead of re-deriving it.
    """

    class RowStatus(models.TextChoices):
        OK = "OK", "Ready"
        WARNING = "WARNING", "Ready, with a note"
        ERROR = "ERROR", "Blocked"

    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="rows")
    line_number = models.PositiveIntegerField()
    raw_data = models.JSONField()
    parsed_data = models.JSONField(blank=True, null=True)
    row_status = models.CharField(max_length=8, choices=RowStatus.choices)
    messages = models.JSONField(default=list, blank=True)
    document = models.ForeignKey(
        Document, on_delete=models.SET_NULL, blank=True, null=True, related_name="import_row"
    )

    class Meta:
        ordering = ["batch", "line_number"]
        indexes = [models.Index(fields=["batch", "row_status"])]

    def __str__(self):
        return f"Batch #{self.batch_id} line {self.line_number} ({self.row_status})"


class ScanBatch(models.Model):
    """One bulk upload of scanned PDFs, matched against existing Document
    records by filename and attached on commit. Mirrors ImportBatch's
    stage-then-commit shape.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Staged"
        REVIEWED = "REVIEWED", "Reviewed"
        COMMITTED = "COMMITTED", "Committed"
        FAILED = "FAILED", "Failed"

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="scan_batches"
    )
    filename_pattern = models.CharField(max_length=100, default="{TYPE}-{NUMBER}-{YEAR}.pdf")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    committed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Scan batch #{self.pk} ({self.get_status_display()})"


class ScanFile(models.Model):
    """One staged PDF within a ScanBatch, and the Document it was matched to."""

    class MatchStatus(models.TextChoices):
        MATCHED = "MATCHED", "Matched"
        UNMATCHED = "UNMATCHED", "No match"
        CONFLICT = "CONFLICT", "File already on record"
        MANUAL = "MANUAL", "Picked by hand"
        ATTACHED = "ATTACHED", "Attached"
        SKIPPED = "SKIPPED", "Skipped"

    batch = models.ForeignKey(ScanBatch, on_delete=models.CASCADE, related_name="files")
    original_filename = models.CharField(max_length=255)
    staged_file = models.FileField(upload_to=scan_stage_upload_path, storage=_raw_storage)
    parsed_doc_type = models.CharField(max_length=8, blank=True)
    parsed_number = models.PositiveIntegerField(blank=True, null=True)
    parsed_series_year = models.PositiveIntegerField(blank=True, null=True)
    document = models.ForeignKey(
        Document, on_delete=models.SET_NULL, blank=True, null=True, related_name="scan_file"
    )
    match_status = models.CharField(max_length=10, choices=MatchStatus.choices)
    resolution = models.CharField(max_length=10, blank=True)
    messages = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["batch", "original_filename"]

    def __str__(self):
        return f"{self.original_filename} ({self.match_status})"
