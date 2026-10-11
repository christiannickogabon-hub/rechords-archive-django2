"""Bulk CSV import of Document records.

Two phases, both driven from here so the web view and the
`import_documents` management command share one set of rules:

  parse_and_validate() — dry run. Reads the CSV, validates every row,
    persists ImportBatch + ImportRow, writes nothing to Document.

  commit() — only reachable once a batch is VALIDATED with no ERROR rows.
    Creates every Document inside one transaction; a failure partway
    through rolls back completely.
"""
import csv
import io
import re
from datetime import date

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from ..models import Document, DocumentType, ImportBatch, ImportRow
from .common import ImportNotReady

TEMPLATE_COLUMNS = (
    "doc_type", "series_year", "number", "subject", "description",
    "date_issued", "effectivity_date", "originating_office", "signatory",
    "addressee", "keywords", "status", "classification",
    "indorsement_level", "physical_location",
)
EXPECTED_COLUMNS = set(TEMPLATE_COLUMNS)

DOC_TYPE_ALIASES = {}
for _code, _label in DocumentType.choices:
    DOC_TYPE_ALIASES[_code.lower()] = _code
    DOC_TYPE_ALIASES[_label.lower()] = _code
# Older spreadsheets use the former spelling.
DOC_TYPE_ALIASES["indorsement"] = DocumentType.INDORSEMENT

_SLASH_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


class _DateFormat:
    """The slash-date convention inferred for one batch, from its own evidence."""

    def __init__(self, resolved, conflict):
        self.resolved = resolved  # "MM/DD" | "DD/MM" | None
        self.conflict = conflict


def _infer_date_format(values):
    mm_dd_proven = False
    dd_mm_proven = False
    for value in values:
        if not value:
            continue
        match = _SLASH_DATE.match(value.strip())
        if not match:
            continue
        a, b = int(match.group(1)), int(match.group(2))
        if a > 12 and b <= 12:
            dd_mm_proven = True
        elif b > 12 and a <= 12:
            mm_dd_proven = True
    if mm_dd_proven and dd_mm_proven:
        return _DateFormat(None, True)
    if mm_dd_proven:
        return _DateFormat("MM/DD", False)
    if dd_mm_proven:
        return _DateFormat("DD/MM", False)
    return _DateFormat(None, False)


def _parse_date(raw, date_format: _DateFormat):
    """Returns (date_or_None, error_message_or_None)."""
    value = (raw or "").strip()
    if not value:
        return None, None

    iso = _ISO_DATE.match(value)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3))), None
        except ValueError:
            return None, f"'{value}' is not a valid calendar date."

    slash = _SLASH_DATE.match(value)
    if slash:
        a, b, y = int(slash.group(1)), int(slash.group(2)), int(slash.group(3))
        if a > 12 and b > 12:
            return None, f"'{value}' is not a valid date — neither part can be a month."
        if a > 12:
            day, month = a, b
        elif b > 12:
            day, month = b, a
        elif date_format.conflict:
            return None, (
                f"'{value}' is ambiguous — this file has other dates that prove both "
                "MM/DD/YYYY and DD/MM/YYYY. Use YYYY-MM-DD for this row."
            )
        elif date_format.resolved == "MM/DD":
            month, day = a, b
        elif date_format.resolved == "DD/MM":
            day, month = a, b
        else:
            return None, (
                f"'{value}' is ambiguous — nothing else in this file reveals whether dates "
                "are MM/DD/YYYY or DD/MM/YYYY. Use YYYY-MM-DD for this row."
            )
        try:
            return date(y, month, day), None
        except ValueError:
            return None, f"'{value}' is not a valid calendar date."

    return None, f"'{value}' isn't a date I recognize. Use YYYY-MM-DD, MM/DD/YYYY, or DD/MM/YYYY."


class _RowResult:
    def __init__(self, line_number, raw):
        self.line_number = line_number
        self.raw = raw
        self.parsed = {}
        self.status = ImportRow.RowStatus.OK
        self.messages = []

    def add_error(self, message):
        self.status = ImportRow.RowStatus.ERROR
        self.messages.append(message)

    def add_warning(self, message):
        if self.status == ImportRow.RowStatus.OK:
            self.status = ImportRow.RowStatus.WARNING
        self.messages.append(message)


def _validate_row(result, *, existing_keys, seen_in_batch, date_format):
    raw = result.raw
    parsed = result.parsed

    doc_type_raw = (raw.get("doc_type") or "").strip()
    doc_type = None
    if not doc_type_raw:
        result.add_error("doc_type is required.")
    else:
        doc_type = DOC_TYPE_ALIASES.get(doc_type_raw.lower())
        if not doc_type:
            result.add_error(
                f"'{doc_type_raw}' isn't a recognized document type. Use MEMO, IND, OO, EO, "
                "or the full name (Memorandum, Endorsement, Office Order, Executive Order)."
            )
    parsed["doc_type"] = doc_type

    year_raw = (raw.get("series_year") or "").strip()
    series_year = None
    if not year_raw:
        result.add_error("series_year is required.")
    else:
        try:
            series_year = int(year_raw)
        except ValueError:
            result.add_error(f"'{year_raw}' isn't a whole number for series_year.")
            series_year = None
        else:
            if not (1900 <= series_year <= date.today().year + 1):
                result.add_error("series_year must be between 1900 and next year.")
                series_year = None
    parsed["series_year"] = series_year

    number_raw = (raw.get("number") or "").strip()
    number = None
    auto_number = True
    if number_raw:
        auto_number = False
        try:
            number = int(number_raw)
        except ValueError:
            result.add_error(f"'{number_raw}' isn't a whole number for number.")
        else:
            if number <= 0:
                result.add_error("number must be positive.")
                number = None
    parsed["number"] = number
    parsed["auto_number"] = auto_number

    subject = (raw.get("subject") or "").strip()
    if not subject:
        result.add_error("subject is required.")
    parsed["subject"] = subject
    parsed["description"] = (raw.get("description") or "").strip()

    date_issued_raw = raw.get("date_issued") or ""
    if not date_issued_raw.strip():
        result.add_error("date_issued is required.")
        parsed["date_issued"] = None
    else:
        parsed_date, error = _parse_date(date_issued_raw, date_format)
        if error:
            result.add_error(f"date_issued: {error}")
        parsed["date_issued"] = parsed_date.isoformat() if parsed_date else None

    effectivity_raw = raw.get("effectivity_date") or ""
    if effectivity_raw.strip():
        parsed_eff, error = _parse_date(effectivity_raw, date_format)
        if error:
            result.add_error(f"effectivity_date: {error}")
        parsed["effectivity_date"] = parsed_eff.isoformat() if parsed_eff else None
    else:
        parsed["effectivity_date"] = None

    for field in ("originating_office", "signatory", "addressee", "keywords", "physical_location"):
        parsed[field] = (raw.get(field) or "").strip()

    status_raw = (raw.get("status") or "").strip()
    if status_raw:
        match = next((c for c in Document.Status.values if c.upper() == status_raw.upper()), None)
        if not match:
            result.add_error(
                f"'{status_raw}' isn't a recognized status. Use one of: "
                f"{', '.join(Document.Status.values)}."
            )
        parsed["status"] = match or Document.Status.ACTIVE
    else:
        parsed["status"] = Document.Status.ACTIVE

    classification_raw = (raw.get("classification") or "").strip()
    if classification_raw:
        match = next(
            (c for c in Document.Classification.values if c.upper() == classification_raw.upper()),
            None,
        )
        if not match:
            result.add_error(
                f"'{classification_raw}' isn't a recognized classification. Use one of: "
                f"{', '.join(Document.Classification.values)}."
            )
        parsed["classification"] = match or Document.Classification.INTERNAL
    else:
        parsed["classification"] = Document.Classification.INTERNAL

    level_raw = (raw.get("indorsement_level") or "").strip()
    indorsement_level = None
    if doc_type == DocumentType.INDORSEMENT:
        if not level_raw:
            result.add_error("indorsement_level is required for endorsements.")
        else:
            try:
                indorsement_level = int(level_raw)
                if indorsement_level <= 0:
                    result.add_error("indorsement_level must be positive.")
                    indorsement_level = None
            except ValueError:
                result.add_error(f"'{level_raw}' isn't a whole number for indorsement_level.")
    parsed["indorsement_level"] = indorsement_level

    if doc_type and series_year and not auto_number and number:
        key = (doc_type, series_year, number)
        existing = existing_keys.get(key)
        if existing:
            result.add_error(f"{existing} is already on file. Pick another number.")
        else:
            earlier = seen_in_batch.get(key)
            if earlier is not None:
                result.add_error(f"Same control number as line {earlier.line_number}.")
                earlier.add_error(f"Same control number as line {result.line_number}.")
            else:
                seen_in_batch[key] = result


def parse_and_validate(csv_file, *, uploaded_by, max_rows=None) -> ImportBatch:
    csv_file.seek(0)
    raw_bytes = csv_file.read()
    csv_file.seek(0)
    batch = ImportBatch.objects.create(file=csv_file, uploaded_by=uploaded_by)

    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        batch.status = ImportBatch.Status.FAILED
        batch.error_summary = "The file isn't valid UTF-8 text. Save it as UTF-8 CSV and try again."
        batch.save(update_fields=["status", "error_summary"])
        return batch

    reader = csv.DictReader(io.StringIO(text))
    fieldnames = [(f or "").strip().lower() for f in (reader.fieldnames or [])]
    reader.fieldnames = fieldnames

    data_rows = []
    for line_number, row in enumerate(reader, start=2):
        normalized = {
            (key or "").strip().lower(): (value or "").strip()
            for key, value in row.items() if key
        }
        data_rows.append((line_number, normalized))

    limit = max_rows if max_rows is not None else settings.IMPORT_MAX_ROWS
    if limit is not None and len(data_rows) > limit:
        batch.status = ImportBatch.Status.FAILED
        batch.rows_read = len(data_rows)
        batch.error_summary = (
            f"This file has {len(data_rows)} rows, over the {limit}-row limit for the web "
            "importer. Use `python manage.py import_documents` for larger files."
        )
        batch.save(update_fields=["status", "rows_read", "error_summary"])
        return batch

    date_format = _infer_date_format(
        value
        for _, row in data_rows
        for value in (row.get("date_issued"), row.get("effectivity_date"))
    )

    existing_keys = {
        (doc.doc_type, doc.series_year, doc.number): doc.control_number
        for doc in Document.objects.filter(number__isnull=False)
    }

    results = []
    seen_in_batch = {}
    for line_number, raw in data_rows:
        result = _RowResult(line_number, raw)
        _validate_row(result, existing_keys=existing_keys, seen_in_batch=seen_in_batch, date_format=date_format)
        results.append(result)

    unknown_cols = sorted(c for c in fieldnames if c and c not in EXPECTED_COLUMNS)
    if unknown_cols:
        note = f"Unrecognized column(s) ignored: {', '.join(unknown_cols)}."
        for result in results:
            result.add_warning(note)

    rows_ready = sum(1 for r in results if r.status != ImportRow.RowStatus.ERROR)
    rows_skipped = len(results) - rows_ready

    ImportRow.objects.bulk_create(
        [
            ImportRow(
                batch=batch, line_number=r.line_number, raw_data=r.raw,
                parsed_data=r.parsed, row_status=r.status, messages=r.messages,
            )
            for r in results
        ],
        batch_size=500,
    )

    batch.rows_read = len(results)
    batch.rows_ready = rows_ready
    batch.rows_skipped = rows_skipped
    batch.status = ImportBatch.Status.VALIDATED
    batch.validated_at = timezone.now()
    batch.save(update_fields=["rows_read", "rows_ready", "rows_skipped", "status", "validated_at"])
    return batch


def commit(batch: ImportBatch, *, acting_user=None) -> ImportBatch:
    if batch.status != ImportBatch.Status.VALIDATED:
        raise ImportNotReady("This batch hasn't been validated, or has already been committed.")
    if batch.rows_ready == 0:
        raise ImportNotReady("There is nothing ready to import in this batch.")
    if batch.has_blocking_errors:
        raise ImportNotReady("Fix the rows marked ERROR and re-upload before committing.")

    importer = acting_user or batch.uploaded_by

    try:
        with transaction.atomic():
            rows = list(
                batch.rows.exclude(row_status=ImportRow.RowStatus.ERROR).order_by("line_number")
            )

            groups_explicit, groups_auto = {}, {}
            for row in rows:
                data = row.parsed_data
                group_key = (data["doc_type"], data["series_year"])
                if data.get("auto_number"):
                    groups_auto.setdefault(group_key, []).append(row)
                else:
                    groups_explicit.setdefault(group_key, set()).add(data["number"])

            # One select_for_update() lock per (doc_type, series_year) group, taken up
            # front — mirrors Document.save()'s own auto-number lock, but for the whole
            # group's run of numbers at once rather than one row at a time.
            next_number = {}
            for group_key in set(groups_auto) | set(groups_explicit):
                doc_type, series_year = group_key
                locked = Document.objects.select_for_update().filter(
                    doc_type=doc_type, series_year=series_year
                )
                next_number[group_key] = locked.aggregate(Max("number"))["number__max"] or 0

            assigned_number = {}
            for group_key, auto_rows in groups_auto.items():
                taken = groups_explicit.get(group_key, set())
                candidate = next_number[group_key]
                for row in auto_rows:
                    candidate += 1
                    while candidate in taken:
                        candidate += 1
                    assigned_number[row.pk] = candidate

            for row in rows:
                data = row.parsed_data
                number = assigned_number[row.pk] if data.get("auto_number") else data["number"]
                document = Document(
                    doc_type=data["doc_type"],
                    series_year=data["series_year"],
                    number=number,
                    subject=data["subject"],
                    description=data.get("description") or "",
                    date_issued=date.fromisoformat(data["date_issued"]),
                    effectivity_date=(
                        date.fromisoformat(data["effectivity_date"])
                        if data.get("effectivity_date") else None
                    ),
                    originating_office=data.get("originating_office") or "",
                    signatory=data.get("signatory") or "",
                    addressee=data.get("addressee") or "",
                    keywords=data.get("keywords") or "",
                    status=data["status"],
                    classification=data["classification"],
                    indorsement_level=data.get("indorsement_level"),
                    physical_location=data.get("physical_location") or "",
                    created_by=importer,
                    updated_by=importer,
                )
                document.save()
                row.document = document

            ImportRow.objects.bulk_update(rows, ["document"])

            batch.status = ImportBatch.Status.COMMITTED
            batch.committed_at = timezone.now()
            batch.save(update_fields=["status", "committed_at"])
    except Exception as exc:
        batch.status = ImportBatch.Status.FAILED
        batch.error_summary = str(exc)[:255]
        batch.save(update_fields=["status", "error_summary"])
        raise
    return batch
