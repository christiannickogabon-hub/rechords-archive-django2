import csv
import logging
import os
import re
from datetime import date
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from django.contrib import messages
from django.db.models import Count, Max, Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, DeleteView, DetailView, ListView, TemplateView, UpdateView

from accounts.mixins import AdminRequiredMixin, StaffUserRequiredMixin, SuperAdminRequiredMixin
from accounts.models import Office, User

from .forms import (AttachmentForm, DocumentFolderForm, DocumentForm,
                    DocumentSearchForm)
from .models import (AccessLog, Attachment, Document, DocumentFolder,
                     DocumentType, Notification, folder_catalogue, folder_labels,
                     folder_tones)
from . import watermark

logger = logging.getLogger(__name__)


# The icon and the accent now live on the folder itself, so a series an
# administrator opens looks like the built-in four.


def _notify_recipients(document):
    """Only people who may actually open the record hear about it.

    The notification repeats the control number and subject, so telling a staff
    user about an internal or confidential filing would leak what the
    classification is meant to hide.
    """
    people = User.objects.filter(is_active=True)
    if document.classification == Document.Classification.PUBLIC:
        return people
    return people.filter(Q(role=User.Role.ADMIN) | Q(is_superuser=True))


class DashboardView(StaffUserRequiredMixin, TemplateView):
    template_name = "documents/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        visible = Document.objects.visible_to(self.request.user)
        this_year = date.today().year

        counts = {row["doc_type"]: row["n"] for row in
                  visible.values("doc_type").annotate(n=Count("id"))}

        context["type_cards"] = [
            {
                "code": folder.code,
                "label": folder.label,
                "icon": folder.icon,
                "tone": folder.tone,
                "total": counts.get(folder.code, 0),
            }
            for folder in folder_catalogue()
        ]
        context["total"] = visible.count()
        context["this_year"] = this_year
        context["recent"] = visible.select_related("created_by")[:8]
        context["latest_filed"] = _tag_with_tones(list(visible.order_by("-created_at")[:6]))
        return context


def _tag_with_tones(documents):
    """Hang each record's series colour on it, in one query for the whole page."""
    tones = folder_tones()
    for document in documents:
        document.tone = tones.get(document.doc_type, "slate")
    return documents


class DocumentListView(StaffUserRequiredMixin, ListView):
    template_name = "documents/document_list.html"
    context_object_name = "documents"
    paginate_by = 20

    def get_queryset(self):
        self.form = DocumentSearchForm(self.request.GET or None)
        qs = Document.objects.visible_to(self.request.user).select_related("created_by")
        if self.request.GET:
            qs = self.form.filter(qs)
        return qs

    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        if request.GET and getattr(self, "form", None) and self.form.is_filtered:
            AccessLog.record(request, AccessLog.Action.SEARCH, note=self.form.summary())
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = self.form
        _tag_with_tones(context["documents"])
        querystring = self.request.GET.copy()
        querystring.pop("page", None)
        context["querystring"] = querystring.urlencode()
        return context


class DocumentFolderView(StaffUserRequiredMixin, ListView):
    """One folder: its series years as sub-folders, and the records inside."""

    template_name = "documents/document_list.html"
    context_object_name = "documents"
    paginate_by = 20

    def get_queryset(self):
        folders = {f.code: f for f in folder_catalogue(include_hidden=True)}
        if self.kwargs["doc_type"] not in folders:
            raise Http404("Unknown document type.")
        folder = folders[self.kwargs["doc_type"]]
        self.folder_label = folder.label
        self.folder_tone = folder.tone
        self.folder_icon = folder.icon

        # ?year= opens one series sub-folder; without it the whole folder shows
        raw_year = self.request.GET.get("year", "").strip()
        self.folder_year = int(raw_year) if raw_year.isdigit() else None

        in_folder = (
            Document.objects.visible_to(self.request.user)
            .filter(doc_type=self.kwargs["doc_type"])
        )

        # the sub-folders: one per series year, newest first
        self.series_folders = [
            {"year": row["series_year"], "total": row["n"]}
            for row in (in_folder.values("series_year")
                                 .annotate(n=Count("id"))
                                 .order_by("-series_year"))
        ]
        self.folder_total = sum(row["total"] for row in self.series_folders)

        documents = in_folder.select_related("created_by")
        if self.folder_year:
            documents = documents.filter(series_year=self.folder_year)
        return documents

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        _tag_with_tones(context["documents"])
        context["folder_code"] = self.kwargs["doc_type"]
        context["folder_label"] = self.folder_label
        context["folder_tone"] = self.folder_tone
        context["folder_icon"] = self.folder_icon
        context["folder_year"] = self.folder_year
        context["series_folders"] = self.series_folders
        context["folder_total"] = self.folder_total
        querystring = f"doc_type={self.kwargs['doc_type']}"
        if self.folder_year:
            querystring += f"&series_year={self.folder_year}"
        context["querystring"] = querystring
        return context


class DocumentDetailView(StaffUserRequiredMixin, DetailView):
    template_name = "documents/document_detail.html"
    context_object_name = "document"

    def get_queryset(self):
        return Document.objects.visible_to(self.request.user).select_related(
            "created_by", "updated_by", "related_to"
        )

    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        AccessLog.record(request, AccessLog.Action.VIEW, document=self.object)
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["attachment_form"] = AttachmentForm()
        context["can_certify_file"] = bool(
            self.object.file and watermark.can_certify(self.object.file.name.split("/")[-1])
        )
        context["chain"] = self.object.referenced_by.filter(is_deleted=False)
        if self.request.user.is_archive_admin:
            context["history"] = self.object.access_logs.select_related("user")[:15]
        return context


class DocumentViewerView(StaffUserRequiredMixin, DetailView):
    """Full-screen read of the scanned copy, with download and exit controls."""

    template_name = "documents/document_viewer.html"
    context_object_name = "document"

    def get_queryset(self):
        return Document.objects.visible_to(self.request.user)

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if not self.object.file:
            messages.error(request, "That record has no scanned copy on file.")
            return redirect(self.object)
        return self.render_to_response(self.get_context_data(object=self.object))


class DocumentCreateView(AdminRequiredMixin, CreateView):
    model = Document
    form_class = DocumentForm
    template_name = "documents/document_form.html"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.updated_by = self.request.user
        response = super().form_valid(form)
        AccessLog.record(self.request, AccessLog.Action.CREATE, document=self.object)
        Notification.notify(
            recipients=_notify_recipients(self.object),
            actor=self.request.user,
            verb=f"filed {self.object.control_number} — {self.object.subject}",
            document=self.object,
            email_subject=f"New document filed: {self.object.control_number}",
            link=self.request.build_absolute_uri(self.object.get_absolute_url()),
        )
        messages.success(self.request, f"{self.object.control_number} filed.")
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["heading"] = "File a new document"
        return context


class DocumentUpdateView(AdminRequiredMixin, UpdateView):
    model = Document
    form_class = DocumentForm
    template_name = "documents/document_form.html"

    def get_queryset(self):
        return Document.objects.filter(is_deleted=False)

    def form_valid(self, form):
        form.instance.updated_by = self.request.user
        response = super().form_valid(form)
        changed = ", ".join(form.changed_data) or "no field changes"
        AccessLog.record(
            self.request, AccessLog.Action.UPDATE, document=self.object, note=changed
        )
        Notification.notify(
            recipients=_notify_recipients(self.object).filter(pk=self.object.created_by_id),
            actor=self.request.user,
            verb=f"updated {self.object.control_number} — {self.object.subject}",
            document=self.object,
        )
        messages.success(self.request, f"{self.object.control_number} updated.")
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["heading"] = f"Edit {self.object.control_number}"
        return context


class DocumentDeleteView(AdminRequiredMixin, DeleteView):
    model = Document
    template_name = "documents/document_confirm_delete.html"
    success_url = reverse_lazy("documents:document_list")
    context_object_name = "document"

    def get_queryset(self):
        return Document.objects.filter(is_deleted=False)

    def form_valid(self, form):
        """Soft delete: the record moves to the bin, the control number stays taken."""
        self.object = self.get_object()
        control = self.object.control_number
        self.object.soft_delete()
        AccessLog.record(self.request, AccessLog.Action.DELETE, document=self.object)
        Notification.notify(
            recipients=_notify_recipients(self.object).filter(pk=self.object.created_by_id),
            actor=self.request.user,
            verb=f"removed {control} to the bin",
        )
        messages.success(self.request, f"{control} moved to the bin. An administrator can restore it.")
        return redirect(self.success_url)


class RecycleBinView(AdminRequiredMixin, ListView):
    template_name = "documents/recycle_bin.html"
    context_object_name = "documents"
    paginate_by = 25

    def get_queryset(self):
        return Document.objects.filter(is_deleted=True).select_related("created_by").order_by("deleted_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["retention_days"] = settings.RECYCLE_BIN_RETENTION_DAYS
        context["due_count"] = sum(1 for d in context["documents"] if d.due_for_disposal)
        return context


def document_dispose(request, pk):
    """Destroy a record for good. Super administrators only, and only after the retention window."""
    if request.method != "POST":
        raise Http404
    if not request.user.is_authenticated or not request.user.is_super_admin:
        AccessLog.record(request, AccessLog.Action.DENIED, note=f"dispose document {pk}")
        raise Http404

    document = get_object_or_404(Document, pk=pk, is_deleted=True)
    if not document.due_for_disposal:
        messages.error(
            request,
            f"{document.control_number} has been in the bin for {document.days_in_bin or 0} day(s). "
            f"It can only be disposed of after {settings.RECYCLE_BIN_RETENTION_DAYS}.",
        )
        return redirect("documents:recycle_bin")

    # Record what existed before destroying it — afterwards there is nothing left to describe.
    summary = (f"Disposed of {document.control_number} — {document.subject} "
               f"(filed {document.date_issued:%d %b %Y}, in bin {document.days_in_bin} days)")
    AccessLog.record(request, AccessLog.Action.DELETE, note=summary)

    for field in (document.file,):
        if field:
            try:
                field.delete(save=False)
            except Exception:
                logger.warning("Could not remove the stored file for %s", document.control_number)
    document.delete()

    messages.success(request, summary.replace("Disposed of", "Permanently removed"))
    return redirect("documents:recycle_bin")


def document_restore(request, pk):
    if not request.user.is_authenticated or not request.user.is_archive_admin:
        AccessLog.record(request, AccessLog.Action.DENIED, note=f"restore document {pk}")
        raise Http404
    document = get_object_or_404(Document, pk=pk, is_deleted=True)
    document.restore()
    AccessLog.record(request, AccessLog.Action.RESTORE, document=document)
    Notification.notify(
        recipients=[document.created_by],
        actor=request.user,
        verb=f"restored {document.control_number} — {document.subject}",
        document=document,
    )
    messages.success(request, f"{document.control_number} restored.")
    return redirect("documents:recycle_bin")


def _open_stored_file(file_field):
    """Open the file from its storage, falling back to MEDIA_ROOT.

    Files uploaded before this project moved to Cloudinary still live on local
    disk, so the configured storage raises for them.
    """
    try:
        return file_field.open("rb")
    except Exception:
        logger.warning("Storage could not open %s; trying local media", file_field.name)
    local = Path(settings.MEDIA_ROOT) / file_field.name
    if local.is_file():
        return local.open("rb")
    return None


# Characters Windows will not take in a file name, plus the control characters.
_UNSAFE_IN_FILENAME = re.compile(r'[<>:"/\\|?*]|[\x00-\x1f]')


def _download_name(document, extension, label=""):
    """Name the download after the record, not after its storage key.

    Stored files carry a random name, which is meaningless once it reaches
    somebody's Downloads folder. A copy of MEMORANDUM No. 010 should arrive
    saying so.
    """
    stem = document.control_number
    if label:
        stem = f"{stem} - {label}"
    if extension.lower() == ".pdf":
        stem = f"{stem} - certified true copy"
    stem = _UNSAFE_IN_FILENAME.sub("", stem)
    stem = " ".join(stem.split()).strip(" .")
    return f"{stem[:120]}{extension}"


def _serve(request, file_field, document, action, inline=False, label=""):
    """Serve the file: inline for previewing, or as a stamped PDF download."""
    if not file_field:
        raise Http404("No file is attached to this record.")
    filename = file_field.name.split("/")[-1]

    payload = _open_stored_file(file_field)
    if payload is None:
        logger.error("File missing for document %s: %s", document.pk, file_field.name)
        messages.error(
            request,
            "That file could not be retrieved from storage. It may not have finished "
            "uploading, or the storage account is not allowing this file to be served.",
        )
        return redirect(document)

    AccessLog.record(request, action, document=document)

    if not inline:
        subtitle = f"{document.control_number} - issued {document.date_issued:%d %b %Y}"
        stamped = None
        if watermark.can_certify(filename):
            try:
                stamped = watermark.certified_pdf(payload, filename, subtitle)
            except Exception:
                logger.exception("Could not certify %s for document %s", filename, document.pk)
        if stamped is not None:
            payload.close()
            return FileResponse(
                stamped, as_attachment=True,
                filename=_download_name(document, ".pdf", label),
            )
        # Nothing leaves stamped unless it is a faithful copy, so say plainly
        # that this one is not certified rather than letting it pass as one.
        messages.warning(
            request,
            f"{filename} was downloaded in its original format. A certified true copy "
            "cannot be produced for this file type — attach a PDF or a scan of the "
            "signed document to certify it.",
        )

    if inline:
        return FileResponse(payload, as_attachment=False, filename=filename)
    # Not certifiable, but it should still arrive under the record's name.
    extension = os.path.splitext(filename)[1]
    return FileResponse(
        payload, as_attachment=True, filename=_download_name(document, extension, label)
    )


def document_suggest(request):
    """Live keyword suggestions for the search field, queried as the user types."""
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    term = request.GET.get("q", "").strip()
    results = Document.objects.visible_to(request.user).search(term)[:8] if term else []
    html = render_to_string(
        "documents/_search_suggestions.html", {"suggestions": results}, request=request
    )
    return HttpResponse(html)


def document_download(request, pk):
    if not request.user.is_authenticated:
        return redirect("accounts:login")
    document = get_object_or_404(Document.objects.visible_to(request.user), pk=pk)
    inline = "view" in request.GET
    action = AccessLog.Action.VIEW if inline else AccessLog.Action.DOWNLOAD
    return _serve(request, document.file, document, action, inline=inline)


def attachment_download(request, pk):
    if not request.user.is_authenticated:
        return redirect("accounts:login")
    attachment = get_object_or_404(Attachment, pk=pk)
    if not Document.objects.visible_to(request.user).filter(pk=attachment.document_id).exists():
        AccessLog.record(request, AccessLog.Action.DENIED, note=f"attachment {pk}")
        raise Http404
    return _serve(request, attachment.file, attachment.document,
                  AccessLog.Action.DOWNLOAD, label=attachment.label)


def attachment_add(request, pk):
    if not request.user.is_authenticated or not request.user.is_archive_admin:
        AccessLog.record(request, AccessLog.Action.DENIED, note=f"add attachment to {pk}")
        raise Http404
    document = get_object_or_404(Document, pk=pk, is_deleted=False)
    if request.method != "POST":
        return redirect(document)
    form = AttachmentForm(request.POST, request.FILES)
    if form.is_valid():
        attachment = form.save(commit=False)
        attachment.document = document
        attachment.uploaded_by = request.user
        attachment.save()
        AccessLog.record(
            request, AccessLog.Action.UPDATE, document=document,
            note=f"Added enclosure: {attachment.label}",
        )
        messages.success(request, f"Enclosure “{attachment.label}” added.")
    else:
        messages.error(request, form.errors.as_text())
    return redirect(document)


def export_csv(request):
    """Export the current search result as CSV. Administrators only."""
    if not request.user.is_authenticated or not request.user.is_archive_admin:
        AccessLog.record(request, AccessLog.Action.DENIED, note="csv export")
        raise Http404
    form = DocumentSearchForm(request.GET or None)
    qs = Document.objects.visible_to(request.user)
    if request.GET:
        qs = form.filter(qs)

    response = HttpResponse(content_type="text/csv")
    stamp = date.today().isoformat()
    response["Content-Disposition"] = f'attachment; filename="archive-index-{stamp}.csv"'
    writer = csv.writer(response)
    writer.writerow([
        "Control number", "Type", "Series", "No.", "Subject", "Date issued",
        "Originating office", "Signed by", "Status", "Classification", "Filed by", "Filed on",
    ])
    for doc in qs.select_related("created_by").iterator():
        writer.writerow([
            doc.control_number, doc.type_label, doc.series_year, doc.number,
            doc.subject, doc.date_issued, doc.originating_office, doc.signatory,
            doc.get_status_display(), doc.get_classification_display(),
            doc.created_by.username, doc.created_at.strftime("%Y-%m-%d %H:%M"),
        ])
    AccessLog.record(request, AccessLog.Action.DOWNLOAD, note=f"CSV export ({qs.count()} rows)")
    return response


class AuditTrailView(SuperAdminRequiredMixin, ListView):
    """Who did what, kept to the super administrator — it names people, not records."""

    template_name = "documents/audit_trail.html"
    context_object_name = "entries"
    paginate_by = 50

    def get_queryset(self):
        qs = AccessLog.objects.select_related("user", "document")
        action = self.request.GET.get("action")
        username = self.request.GET.get("user")
        if action:
            qs = qs.filter(action=action)
        if username:
            qs = qs.filter(user__username__icontains=username)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["actions"] = AccessLog.Action.choices
        context["selected_action"] = self.request.GET.get("action", "")
        context["selected_user"] = self.request.GET.get("user", "")
        return context

class FolderListView(AdminRequiredMixin, ListView):
    """The series on file, and the way to open another one."""

    template_name = "documents/folder_list.html"
    context_object_name = "folders"

    def get_queryset(self):
        return DocumentFolder.objects.all()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["hidden_count"] = DocumentFolder.objects.filter(is_hidden=True).count()
        return context


class FolderCreateView(AdminRequiredMixin, CreateView):
    template_name = "documents/folder_form.html"
    form_class = DocumentFolderForm
    success_url = reverse_lazy("documents:folder_list")

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.sort_order = (
            DocumentFolder.objects.aggregate(Max("sort_order"))["sort_order__max"] or 0
        ) + 10
        response = super().form_valid(form)
        AccessLog.record(self.request, AccessLog.Action.CREATE,
                         note=f"Opened the {self.object.label} folder ({self.object.code})")
        messages.success(
            self.request,
            f"{self.object.label} is open. Records filed under it are numbered "
            f"{self.object.long_name} No. 001, s. {date.today().year} and on.",
        )
        return response


class FolderUpdateView(AdminRequiredMixin, UpdateView):
    template_name = "documents/folder_form.html"
    form_class = DocumentFolderForm
    success_url = reverse_lazy("documents:folder_list")

    def get_queryset(self):
        return DocumentFolder.objects.all()

    def form_valid(self, form):
        response = super().form_valid(form)
        AccessLog.record(self.request, AccessLog.Action.UPDATE,
                         note=f"Edited the {self.object.label} folder")
        messages.success(self.request, f"{self.object.label} updated.")
        return response


def folder_delete(request, pk):
    """Only an empty, non-built-in folder can go — otherwise records lose their series."""
    folder = get_object_or_404(DocumentFolder, pk=pk)
    if request.method != "POST" or not request.user.is_authenticated or not request.user.is_archive_admin:
        AccessLog.record(request, AccessLog.Action.DENIED, note=f"remove folder {pk}")
        raise Http404
    if not folder.can_be_removed:
        messages.error(
            request,
            f"{folder.label} cannot be removed — {folder.removal_blocked_reason}",
        )
        return redirect("documents:folder_list")
    label = folder.label
    folder.delete()
    AccessLog.record(request, AccessLog.Action.DELETE, note=f"Removed the {label} folder")
    messages.success(request, f"{label} removed.")
    return redirect("documents:folder_list")


def folder_hide(request, pk):
    """Take a folder out of use without touching what is filed under it."""
    folder = get_object_or_404(DocumentFolder, pk=pk)
    if request.method != "POST" or not request.user.is_authenticated or not request.user.is_archive_admin:
        AccessLog.record(request, AccessLog.Action.DENIED, note=f"hide folder {pk}")
        raise Http404

    folder.is_hidden = not folder.is_hidden
    folder.save(update_fields=["is_hidden"])
    AccessLog.record(
        request, AccessLog.Action.UPDATE,
        note=f"{'Hid' if folder.is_hidden else 'Restored'} the {folder.label} folder",
    )
    if folder.is_hidden:
        messages.success(
            request,
            f"{folder.label} is hidden. Nothing new can be filed under it, and its "
            f"{folder.document_count} record(s) stay searchable.",
        )
    else:
        messages.success(request, f"{folder.label} is back in use.")
    return redirect("documents:folder_list")
